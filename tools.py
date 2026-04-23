"""工具定义与执行器。"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


MAX_TOOL_OUTPUT_CHARS = 3000
MAX_FILE_PREVIEW_CHARS = 12000
DEFAULT_LIST_LIMIT = 200
DEFAULT_GREP_LIMIT = 50
MAX_TEXT_FILE_SIZE = 512 * 1024


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head_len = limit * 2 // 3
    tail_len = limit - head_len
    omitted = len(text) - limit
    head = text[:head_len]
    tail = text[-tail_len:]
    return (
        head
        + f"\n\n... [已省略 {omitted} 字符。请缩小范围后再读] ...\n\n"
        + tail
    )


def _read_text_file(path: Path, limit: int = MAX_FILE_PREVIEW_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"文件不是 UTF-8 文本: {path}")
    return _truncate(text, limit=limit)


def _relative_display(path: Path, workspace_root: Path) -> str:
    return str(path.relative_to(workspace_root))


def _looks_dangerous_command(cmd: str) -> bool:
    patterns = [
        r"\brm\b",
        r"\bsudo\b",
        r"git\s+reset\s+--hard",
        r"git\s+checkout\s+--",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bmkfs\b",
    ]
    return any(re.search(pattern, cmd) for pattern in patterns)


@dataclass
class ToolRuntime:
    workspace_root: Path
    auto_approve: bool = False
    command_timeout: int = 30

    def __post_init__(self) -> None:
        self.workspace_root = self.workspace_root.expanduser().resolve()
        self.tool_schemas = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "读取工作区内文本文件内容。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "文件路径"}
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "写入文本文件，覆盖已有内容。执行前通常会询问用户确认。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "文件路径"},
                            "content": {
                                "type": "string",
                                "description": "要写入的完整内容",
                            },
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": "列出工作区内的文件或目录，适合做轻量探索。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "起始目录或文件，默认当前工作区根目录",
                            },
                            "glob": {
                                "type": "string",
                                "description": "glob 模式，例如 **/*.py",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "最多返回多少项，默认 200",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "grep_text",
                    "description": "在工作区内搜索文本，返回 file:line 片段。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {
                                "type": "string",
                                "description": "Python 正则表达式",
                            },
                            "path": {
                                "type": "string",
                                "description": "待搜索的目录或文件，默认整个工作区",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "最多返回多少条匹配，默认 50",
                            },
                        },
                        "required": ["pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "description": (
                        "在工作区根目录执行 shell 命令，返回 stdout/stderr。"
                        "执行前通常会询问用户确认。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "cmd": {"type": "string", "description": "完整 shell 命令"}
                        },
                        "required": ["cmd"],
                    },
                },
            },
        ]

    def tool_names(self) -> set[str]:
        return {schema["function"]["name"] for schema in self.tool_schemas}

    def tool_summary(self) -> str:
        return "\n".join(
            [
                "- read_file(path): 读取工作区内文本文件",
                "- write_file(path, content): 写文件，执行前会请求确认",
                "- list_files(path='.', glob='**/*', limit=200): 列目录或文件",
                "- grep_text(pattern, path='.', limit=50): 搜索文本",
                "- run_command(cmd): 在工作区根目录执行 shell，执行前会请求确认",
            ]
        )

    def _resolve_path(self, path: str, *, allow_missing: bool = False) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        resolved = candidate.resolve(strict=False)

        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError(f"路径超出工作区: {path}") from exc

        if not allow_missing and not resolved.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        return resolved

    def _confirm(self, action: str, preview: str) -> bool:
        if self.auto_approve:
            return True

        if not sys.stdin.isatty():
            return False

        print(f"\n[permission] {action}")
        print(_truncate(preview, limit=500))
        answer = input("允许吗？输入 y 继续，其余任意键取消: ").strip().lower()
        return answer in {"y", "yes"}

    def read_file(self, path: str) -> str:
        try:
            file_path = self._resolve_path(path)
        except Exception as exc:
            return f"ERROR: {exc}"

        if file_path.is_dir():
            return f"ERROR: 这是目录不是文件: {_relative_display(file_path, self.workspace_root)}"

        try:
            content = _read_text_file(file_path)
        except Exception as exc:
            return f"ERROR: {exc}"

        rel = _relative_display(file_path, self.workspace_root)
        return f"[file] {rel}\n{content}"

    def write_file(self, path: str, content: str) -> str:
        try:
            file_path = self._resolve_path(path, allow_missing=True)
        except Exception as exc:
            return f"ERROR: {exc}"

        rel = _relative_display(file_path, self.workspace_root)
        preview = f"写入文件: {rel}\n\n{content[:400]}"
        if not self._confirm("write_file", preview):
            return f"DENIED: 用户拒绝写入 {rel}"

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"OK: 写入 {len(content)} 字符到 {rel}"

    def list_files(self, path: str = ".", glob: str = "**/*", limit: int = DEFAULT_LIST_LIMIT) -> str:
        try:
            base_path = self._resolve_path(path)
        except Exception as exc:
            return f"ERROR: {exc}"

        if limit <= 0:
            limit = DEFAULT_LIST_LIMIT

        try:
            if base_path.is_file():
                matches = [base_path]
            else:
                matches = sorted(base_path.glob(glob))
        except Exception as exc:
            return f"ERROR: list_files 失败: {exc}"

        lines = []
        count = 0
        for match in matches:
            try:
                resolved = match.resolve(strict=False)
                resolved.relative_to(self.workspace_root)
            except Exception:
                continue

            suffix = "/" if match.is_dir() else ""
            lines.append(f"{_relative_display(resolved, self.workspace_root)}{suffix}")
            count += 1
            if count >= limit:
                break

        if not lines:
            return "没有匹配项"

        total_note = ""
        if len(matches) > limit:
            total_note = f"\n... [已截断，只显示前 {limit} 项]"
        return "\n".join(lines) + total_note

    def grep_text(self, pattern: str, path: str = ".", limit: int = DEFAULT_GREP_LIMIT) -> str:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return f"ERROR: 非法正则表达式: {exc}"

        try:
            base_path = self._resolve_path(path)
        except Exception as exc:
            return f"ERROR: {exc}"

        if limit <= 0:
            limit = DEFAULT_GREP_LIMIT

        if base_path.is_file():
            candidates = [base_path]
        else:
            candidates = sorted(p for p in base_path.rglob("*") if p.is_file())

        results = []
        for file_path in candidates:
            try:
                if file_path.stat().st_size > MAX_TEXT_FILE_SIZE:
                    continue
                text = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            for line_no, line in enumerate(text.splitlines(), start=1):
                if not regex.search(line):
                    continue
                rel = _relative_display(file_path, self.workspace_root)
                results.append(f"{rel}:{line_no}: {line.strip()}")
                if len(results) >= limit:
                    return "\n".join(results) + f"\n... [已截断，只显示前 {limit} 条匹配]"

        return "\n".join(results) if results else "没有匹配项"

    def run_command(self, cmd: str) -> str:
        danger = "高风险命令" if _looks_dangerous_command(cmd) else "执行命令"
        preview = f"{danger}（工作区起点: {self.workspace_root}）\n\n{cmd}"
        if not self._confirm("run_command", preview):
            return "DENIED: 用户拒绝执行命令"

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
                cwd=self.workspace_root,
            )
        except subprocess.TimeoutExpired:
            return f"ERROR: 命令执行超过 {self.command_timeout} 秒被终止"
        except Exception as exc:
            return f"ERROR: 命令执行失败: {type(exc).__name__}: {exc}"

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        combined = stdout
        if stderr:
            combined += f"\n[stderr]\n{stderr}"
        combined += f"\n[exit code] {result.returncode}"
        return _truncate(combined)

    def execute_tool(self, name: str, args: dict) -> str:
        if name == "read_file":
            return self.read_file(**args)
        if name == "write_file":
            return self.write_file(**args)
        if name == "list_files":
            return self.list_files(**args)
        if name == "grep_text":
            return self.grep_text(**args)
        if name == "run_command":
            return self.run_command(**args)
        return f"ERROR: 未知工具 {name}"
