"""工具定义与执行器。"""

from __future__ import annotations

import difflib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


MAX_TOOL_OUTPUT_CHARS = 3000
MAX_FILE_PREVIEW_CHARS = 12000
MAX_PATCH_PREVIEW_CHARS = 4000
MAX_CONFIRM_PREVIEW_CHARS = 2000
MAX_FULL_PATCH_DISPLAY_CHARS = 12000
PATCH_REVIEW_PANEL_WIDTH = 78
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


def _build_patch_preview(
    old_text: str,
    new_text: str,
    rel_path: str,
    *,
    existed_before: bool,
    limit: int | None = MAX_PATCH_PREVIEW_CHARS,
) -> str:
    from_file = f"a/{rel_path}" if existed_before else "/dev/null"
    to_file = f"b/{rel_path}"
    diff = "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=from_file,
            tofile=to_file,
        )
    )
    if not diff:
        return "(没有文本变化)"
    if limit is None:
        return diff
    return _truncate(diff, limit=limit)


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


def _supports_color(stream=None) -> bool:
    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", None) and stream.isatty())


def _style_text(text: str, *, color: str, use_color: bool) -> str:
    if not use_color:
        return text
    color_map = {
        "red": "\033[31m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "cyan": "\033[36m",
        "bold": "\033[1m",
        "dim": "\033[2m",
    }
    prefix = color_map.get(color)
    if not prefix:
        return text
    return f"{prefix}{text}\033[0m"


def _format_patch_diff(diff: str, *, use_color: bool) -> str:
    lines = []
    for line in diff.splitlines():
        if line.startswith("@@") or line.startswith("+++") or line.startswith("---"):
            lines.append(_style_text(line, color="cyan", use_color=use_color))
        elif line.startswith("+") and not line.startswith("+++"):
            lines.append(_style_text(line, color="green", use_color=use_color))
        elif line.startswith("-") and not line.startswith("---"):
            lines.append(_style_text(line, color="red", use_color=use_color))
        else:
            lines.append(line)
    return "\n".join(lines)


def _count_patch_stats(diff: str) -> tuple[int, int, int]:
    additions = 0
    deletions = 0
    hunks = 0
    for line in diff.splitlines():
        if line.startswith("@@"):
            hunks += 1
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return additions, deletions, hunks


def _render_panel(title: str, rows: list[str], *, width: int = PATCH_REVIEW_PANEL_WIDTH) -> str:
    content_width = max(20, width - 4)

    def fit(line: str) -> str:
        if len(line) > content_width:
            return line[: content_width - 3] + "..."
        return line

    border = "+" + "-" * (content_width + 2) + "+"
    lines = [border, f"| {fit(f'[ {title} ]').ljust(content_width)} |"]
    for row in rows:
        lines.append(f"| {fit(row).ljust(content_width)} |")
    lines.append(border)
    return "\n".join(lines)


def _render_patch_review_screen(
    title: str,
    rel_path: str,
    diff: str,
    *,
    preview_label: str,
    meta_lines: list[str],
) -> str:
    additions, deletions, diff_hunks = _count_patch_stats(diff)
    rows = [
        f"file: {rel_path}",
        f"delta: +{additions} / -{deletions} | diff hunks: {diff_hunks}",
        *meta_lines,
    ]
    panel = _render_panel(title, rows)
    body = _format_patch_diff(diff, use_color=_supports_color())
    return f"{panel}\n{preview_label}\n{body}"


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
                    "description": "写入文本文件，覆盖已有内容。适合创建新文件或整文件重写。执行前通常会询问用户确认。",
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
                    "name": "edit_file",
                    "description": "按精确文本片段编辑文件。适合局部修改，不用整文件重写。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "文件路径"},
                            "old_text": {
                                "type": "string",
                                "description": "要替换的原始文本片段，必须与文件内容完全匹配",
                            },
                            "new_text": {
                                "type": "string",
                                "description": "替换后的文本片段",
                            },
                            "replace_all": {
                                "type": "boolean",
                                "description": "是否替换全部匹配项，默认 false",
                            },
                        },
                        "required": ["path", "old_text", "new_text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "apply_patch",
                    "description": "对同一个文件按顺序应用多个精确文本替换。适合一次完成多处局部修改。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "文件路径"},
                            "edits": {
                                "type": "array",
                                "description": "按顺序应用的编辑列表",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "old_text": {
                                            "type": "string",
                                            "description": "要替换的原始文本片段",
                                        },
                                        "new_text": {
                                            "type": "string",
                                            "description": "替换后的文本片段",
                                        },
                                        "replace_all": {
                                            "type": "boolean",
                                            "description": "是否替换全部匹配项，默认 false",
                                        },
                                    },
                                    "required": ["old_text", "new_text"],
                                },
                            },
                        },
                        "required": ["path", "edits"],
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
                "- write_file(path, content): 整文件写入，适合新建或重写文件",
                "- edit_file(path, old_text, new_text, replace_all=False): 精确替换文件中的一段文本",
                "- apply_patch(path, edits): 一次应用多个精确文本替换，适合多处局部改动",
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

    def _confirm(
        self,
        action: str,
        preview: str,
        *,
        full_preview: str | None = None,
        accept_label: str = "继续",
    ) -> bool:
        if self.auto_approve:
            return True

        if not sys.stdin.isatty():
            return False

        print(f"\n[permission] {action}")
        print(_truncate(preview, limit=MAX_CONFIRM_PREVIEW_CHARS))
        if full_preview is None:
            answer = input("允许吗？输入 y 继续，其余任意键取消: ").strip().lower()
            return answer in {"y", "yes"}

        while True:
            answer = input(
                f"输入 y {accept_label}，p 查看完整 patch，n 取消: "
            ).strip().lower()
            if answer in {"y", "yes"}:
                return True
            if answer in {"", "n", "no"}:
                return False
            if answer == "p":
                print("\n[full patch preview]")
                print(_truncate(full_preview, limit=MAX_FULL_PATCH_DISPLAY_CHARS))
                continue
            print("请输入 y / p / n")

    def _choose_patch_apply_mode(
        self,
        action: str,
        preview: str,
        *,
        full_preview: str,
        allow_hunk_review: bool,
    ) -> str:
        if self.auto_approve:
            return "apply_all"

        if not sys.stdin.isatty():
            return "deny"

        print(f"\n[permission] {action}")
        print(_truncate(preview, limit=MAX_CONFIRM_PREVIEW_CHARS))

        if not allow_hunk_review:
            while True:
                answer = input(
                    "输入 y 应用这个 patch，p 查看完整 patch，n 取消: "
                ).strip().lower()
                if answer in {"y", "yes"}:
                    return "apply_all"
                if answer in {"", "n", "no"}:
                    return "deny"
                if answer == "p":
                    print("\n[full patch preview]")
                    print(_truncate(full_preview, limit=MAX_FULL_PATCH_DISPLAY_CHARS))
                    continue
                print("请输入 y / p / n")

        while True:
            answer = input(
                "输入 y 全部应用，h 逐段选择，p 查看完整 patch，n 取消: "
            ).strip().lower()
            if answer in {"y", "yes"}:
                return "apply_all"
            if answer in {"h", "review"}:
                return "review_hunks"
            if answer in {"", "n", "no"}:
                return "deny"
            if answer == "p":
                print("\n[full patch preview]")
                print(_truncate(full_preview, limit=MAX_FULL_PATCH_DISPLAY_CHARS))
                continue
            print("请输入 y / h / p / n")

    def _choose_patch_hunk_action(
        self,
        rel_path: str,
        hunk_index: int,
        total_hunks: int,
        preview: str,
        *,
        full_preview: str,
    ) -> str:
        if self.auto_approve:
            return "apply"

        if not sys.stdin.isatty():
            return "skip"

        print(f"\n[patch hunk {hunk_index}/{total_hunks}] {rel_path}")
        print(_truncate(preview, limit=MAX_CONFIRM_PREVIEW_CHARS))

        while True:
            answer = input(
                "输入 y 应用这段，s 跳过，a 应用这段和剩余，p 查看完整 hunk，q 结束并保留已接受内容: "
            ).strip().lower()
            if answer in {"y", "yes"}:
                return "apply"
            if answer in {"", "s", "skip"}:
                return "skip"
            if answer in {"a", "all"}:
                return "apply_rest"
            if answer in {"q", "quit"}:
                return "stop"
            if answer == "p":
                print("\n[full hunk preview]")
                print(_truncate(full_preview, limit=MAX_FULL_PATCH_DISPLAY_CHARS))
                continue
            print("请输入 y / s / a / p / q")

    def _normalize_patch_edits(self, edits: list[dict]) -> list[dict[str, str | bool]]:
        if not edits:
            raise ValueError("edits 不能为空")

        normalized: list[dict[str, str | bool]] = []
        for index, edit in enumerate(edits, start=1):
            if not isinstance(edit, dict):
                raise ValueError(f"patch 第 {index} 项不是对象")
            old_text = edit.get("old_text")
            new_text = edit.get("new_text")
            replace_all = bool(edit.get("replace_all", False))
            if not isinstance(old_text, str) or not isinstance(new_text, str):
                raise ValueError(f"patch 第 {index} 项缺少合法的 old_text/new_text")
            normalized.append(
                {
                    "old_text": old_text,
                    "new_text": new_text,
                    "replace_all": replace_all,
                }
            )
        return normalized

    def _apply_patch_edits(
        self,
        content: str,
        edits: list[dict[str, str | bool]],
    ) -> tuple[str, int]:
        updated = content
        total_replacements = 0
        for index, edit in enumerate(edits, start=1):
            try:
                updated, replaced = self._replace_exact(
                    updated,
                    old_text=str(edit["old_text"]),
                    new_text=str(edit["new_text"]),
                    replace_all=bool(edit["replace_all"]),
                )
            except ValueError as exc:
                raise ValueError(f"patch 第 {index} 项失败: {exc}") from exc
            total_replacements += replaced
        return updated, total_replacements

    def _replace_exact(
        self,
        content: str,
        *,
        old_text: str,
        new_text: str,
        replace_all: bool,
    ) -> tuple[str, int]:
        occurrences = content.count(old_text)
        if occurrences == 0:
            raise ValueError("old_text 没有在目标文件中找到，无法精确编辑")
        if occurrences > 1 and not replace_all:
            raise ValueError(
                f"old_text 出现了 {occurrences} 次。请提供更精确的片段，或显式设置 replace_all=true"
            )

        if replace_all:
            updated = content.replace(old_text, new_text)
            replaced_count = occurrences
        else:
            updated = content.replace(old_text, new_text, 1)
            replaced_count = 1

        if updated == content:
            raise ValueError("编辑后内容没有变化")
        return updated, replaced_count

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

        existed_before = file_path.exists()
        old_text = ""
        if existed_before and file_path.is_file():
            try:
                old_text = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return f"ERROR: 目标文件不是 UTF-8 文本: {_relative_display(file_path, self.workspace_root)}"

        rel = _relative_display(file_path, self.workspace_root)
        full_patch = _build_patch_preview(
            old_text,
            content,
            rel,
            existed_before=existed_before,
            limit=None,
        )
        patch = _build_patch_preview(
            old_text,
            content,
            rel,
            existed_before=existed_before,
        )
        preview = _render_patch_review_screen(
            "Write File Review",
            rel,
            patch,
            preview_label="[patch preview before apply]",
            meta_lines=[
                f"mode: {'create' if not existed_before else 'overwrite'}",
                "actions: y apply | p full patch | n cancel",
            ],
        )
        full_preview_screen = _render_patch_review_screen(
            "Write File Review",
            rel,
            full_patch,
            preview_label="[patch preview before apply]",
            meta_lines=[
                f"mode: {'create' if not existed_before else 'overwrite'}",
                "actions: y apply | p full patch | n cancel",
            ],
        )
        if not self._confirm(
            "write_file",
            preview,
            full_preview=full_preview_screen,
            accept_label="应用这个 patch",
        ):
            return f"DENIED: 用户拒绝写入 {rel}"

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"OK: 写入 {len(content)} 字符到 {rel}\n\n[patch preview]\n{patch}"

    def edit_file(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
    ) -> str:
        try:
            file_path = self._resolve_path(path)
        except Exception as exc:
            return f"ERROR: {exc}"

        if file_path.is_dir():
            return f"ERROR: 这是目录不是文件: {_relative_display(file_path, self.workspace_root)}"

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"ERROR: 目标文件不是 UTF-8 文本: {_relative_display(file_path, self.workspace_root)}"

        try:
            updated, replaced_count = self._replace_exact(
                content,
                old_text=old_text,
                new_text=new_text,
                replace_all=replace_all,
            )
        except ValueError as exc:
            return f"ERROR: {exc}"

        rel = _relative_display(file_path, self.workspace_root)
        full_patch = _build_patch_preview(
            content,
            updated,
            rel,
            existed_before=True,
            limit=None,
        )
        patch = _build_patch_preview(
            content,
            updated,
            rel,
            existed_before=True,
        )
        mode_text = "replace_all" if replace_all else "single replace"
        preview = _render_patch_review_screen(
            "Edit File Review",
            rel,
            patch,
            preview_label="[patch preview before apply]",
            meta_lines=[
                f"mode: {mode_text}",
                "actions: y apply | p full patch | n cancel",
            ],
        )
        full_preview_screen = _render_patch_review_screen(
            "Edit File Review",
            rel,
            full_patch,
            preview_label="[patch preview before apply]",
            meta_lines=[
                f"mode: {mode_text}",
                "actions: y apply | p full patch | n cancel",
            ],
        )
        if not self._confirm(
            "edit_file",
            preview,
            full_preview=full_preview_screen,
            accept_label="应用这个 patch",
        ):
            return f"DENIED: 用户拒绝编辑 {rel}"

        file_path.write_text(updated, encoding="utf-8")
        return (
            f"OK: 已编辑 {rel}，替换 {replaced_count} 处匹配\n\n"
            f"[patch preview]\n{patch}"
        )

    def apply_patch(self, path: str, edits: list[dict]) -> str:
        try:
            file_path = self._resolve_path(path)
        except Exception as exc:
            return f"ERROR: {exc}"

        if file_path.is_dir():
            return f"ERROR: 这是目录不是文件: {_relative_display(file_path, self.workspace_root)}"

        try:
            original = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"ERROR: 目标文件不是 UTF-8 文本: {_relative_display(file_path, self.workspace_root)}"

        try:
            normalized_edits = self._normalize_patch_edits(edits)
            updated, total_replacements = self._apply_patch_edits(
                original,
                normalized_edits,
            )
        except ValueError as exc:
            return f"ERROR: {exc}"

        rel = _relative_display(file_path, self.workspace_root)
        full_patch = _build_patch_preview(
            original,
            updated,
            rel,
            existed_before=True,
            limit=None,
        )
        patch = _build_patch_preview(
            original,
            updated,
            rel,
            existed_before=True,
        )
        allow_hunk_review = len(normalized_edits) > 1
        preview = _render_patch_review_screen(
            "Patch Review",
            rel,
            patch,
            preview_label="[patch preview before apply]",
            meta_lines=[
                f"planned edits: {len(normalized_edits)}",
                (
                    "actions: y apply all | h review hunks | p full patch | n cancel"
                    if allow_hunk_review
                    else "actions: y apply | p full patch | n cancel"
                ),
            ],
        )
        full_preview_screen = _render_patch_review_screen(
            "Patch Review",
            rel,
            full_patch,
            preview_label="[patch preview before apply]",
            meta_lines=[
                f"planned edits: {len(normalized_edits)}",
                (
                    "actions: y apply all | h review hunks | p full patch | n cancel"
                    if allow_hunk_review
                    else "actions: y apply | p full patch | n cancel"
                ),
            ],
        )
        decision = self._choose_patch_apply_mode(
            "apply_patch",
            preview,
            full_preview=full_preview_screen,
            allow_hunk_review=allow_hunk_review,
        )
        if decision == "deny":
            return f"DENIED: 用户拒绝应用 patch 到 {rel}"
        if decision == "apply_all":
            file_path.write_text(updated, encoding="utf-8")
            return (
                f"OK: 已对 {rel} 应用 {len(normalized_edits)} 个 patch hunk，替换 {total_replacements} 处匹配\n\n"
                f"[patch preview]\n{patch}"
            )

        current = original
        accepted_hunks = 0
        skipped_hunks = 0
        total_replacements = 0
        apply_remaining = False
        notes: list[str] = []

        for index, edit in enumerate(normalized_edits, start=1):
            try:
                candidate, replaced = self._replace_exact(
                    current,
                    old_text=str(edit["old_text"]),
                    new_text=str(edit["new_text"]),
                    replace_all=bool(edit["replace_all"]),
                )
            except ValueError as exc:
                skipped_hunks += 1
                notes.append(
                    f"第 {index} 段在当前内容下无法应用，已跳过: {exc}"
                )
                continue

            hunk_full_patch = _build_patch_preview(
                current,
                candidate,
                rel,
                existed_before=True,
                limit=None,
            )
            hunk_patch = _build_patch_preview(
                current,
                candidate,
                rel,
                existed_before=True,
            )
            hunk_preview = _render_patch_review_screen(
                f"Patch Hunk {index}/{len(normalized_edits)}",
                rel,
                hunk_patch,
                preview_label="[patch hunk preview]",
                meta_lines=[
                    f"progress: accepted {accepted_hunks} | skipped {skipped_hunks} | remaining {len(normalized_edits) - index + 1}",
                    "actions: y apply | s skip | a apply rest | p full hunk | q stop",
                ],
            )
            hunk_full_preview = _render_patch_review_screen(
                f"Patch Hunk {index}/{len(normalized_edits)}",
                rel,
                hunk_full_patch,
                preview_label="[patch hunk preview]",
                meta_lines=[
                    f"progress: accepted {accepted_hunks} | skipped {skipped_hunks} | remaining {len(normalized_edits) - index + 1}",
                    "actions: y apply | s skip | a apply rest | p full hunk | q stop",
                ],
            )
            if apply_remaining:
                hunk_decision = "apply"
            else:
                hunk_decision = self._choose_patch_hunk_action(
                    rel,
                    index,
                    len(normalized_edits),
                    hunk_preview,
                    full_preview=hunk_full_preview,
                )

            if hunk_decision == "stop":
                remaining = len(normalized_edits) - index + 1
                skipped_hunks += remaining
                notes.append(
                    f"用户在第 {index} 段结束逐段审批，剩余 {remaining} 个 hunk 已跳过"
                )
                break
            if hunk_decision == "skip":
                skipped_hunks += 1
                continue

            current = candidate
            accepted_hunks += 1
            total_replacements += replaced
            if hunk_decision == "apply_rest":
                apply_remaining = True

        if accepted_hunks == 0:
            lines = [f"DENIED: 用户没有应用 {rel} 的任何 patch hunk"]
            if notes:
                lines.extend(["", "[patch notes]"])
                lines.extend(f"- {note}" for note in notes)
            return "\n".join(lines)

        final_patch = _build_patch_preview(
            original,
            current,
            rel,
            existed_before=True,
        )
        file_path.write_text(current, encoding="utf-8")

        summary = (
            f"OK: 已对 {rel} 选择性应用 {accepted_hunks}/{len(normalized_edits)} 个 patch hunk，"
            f"替换 {total_replacements} 处匹配"
        )
        if skipped_hunks:
            summary += f"，跳过 {skipped_hunks} 个"

        lines = [summary, "", "[patch preview]", final_patch]
        if notes:
            lines.extend(["", "[patch notes]"])
            lines.extend(f"- {note}" for note in notes)
        return "\n".join(lines)

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
        if name == "edit_file":
            return self.edit_file(**args)
        if name == "apply_patch":
            return self.apply_patch(**args)
        if name == "list_files":
            return self.list_files(**args)
        if name == "grep_text":
            return self.grep_text(**args)
        if name == "run_command":
            return self.run_command(**args)
        return f"ERROR: 未知工具 {name}"
