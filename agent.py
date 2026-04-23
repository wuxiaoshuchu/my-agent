"""本地编码 agent。

这一版在最小 loop 之上补了几件更像“产品”的能力：
1. REPL 模式：支持多轮对话，不必每次重新启动进程
2. Session 类：把消息历史、模型调用、工具执行收口起来
3. 更合适的探索工具：list_files / grep_text，减少模型滥用 shell
4. 基础确认机制：写文件、执行命令前先征求用户同意
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tools import ToolRuntime


DEFAULT_MODEL = "qwen2.5-coder:7b"
DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_NUM_CTX = 16384
DEFAULT_MAX_TURNS = 20
DEFAULT_COMMAND_TIMEOUT = 30
PROJECT_GUIDE_FILES = ("HARNESS.md", "CLAUDE.md")
MAX_PROJECT_GUIDE_CHARS = 4000


SYSTEM_PROMPT_TEMPLATE = """你是一个本地编码助手，用户在 Mac 终端里和你对话。

## 当前工作区
- 工作区根目录：{workspace_root}
- 根目录预览：
{workspace_snapshot}

## 可用工具
{tool_summary}

{project_guide}

## 行为规则
- 优先使用专用工具而不是 shell：
  - 读文件用 read_file
  - 搜索文件用 list_files
  - 搜索文本用 grep_text
  - 局部改动优先用 edit_file
  - 新建文件或整文件重写再用 write_file
- 只有在专用工具做不到时，再使用 run_command
- 你可以连续调用多个工具，但任务完成后必须输出纯文字总结
- 不要伪造“我已经改好了”之类的描述；修改必须真的通过工具完成

## 路径与命令
- 所有相对路径都以工作区根目录为基准
- 文件读写受工作区限制，不要尝试访问工作区外的路径
- shell 命令会在工作区根目录启动，并且执行前可能需要用户确认
- 如果需要大范围搜索，先缩小目录，再 grep，避免制造超长输出

## 结束条件
- 当你不再需要工具时，直接用自然语言给出结果
- 不要在最终答案里再输出 JSON 或伪造 tool call"""


@dataclass
class AgentConfig:
    model: str
    base_url: str
    api_key: str
    num_ctx: int
    max_turns: int
    workspace_root: Path
    auto_approve: bool
    command_timeout: int


@dataclass
class ActivityEntry:
    timestamp: str
    kind: str
    summary: str


@dataclass
class RepoStatusSnapshot:
    in_repo: bool
    branch: str = "-"
    tracking: str | None = None
    ahead: int = 0
    behind: int = 0
    staged: int = 0
    modified: int = 0
    untracked: int = 0

    @property
    def clean(self) -> bool:
        return self.in_repo and self.staged == 0 and self.modified == 0 and self.untracked == 0

    @property
    def total_changes(self) -> int:
        return self.staged + self.modified + self.untracked


class WorkspaceInspector:
    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root

    def _run_git(self, *args: str) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except FileNotFoundError:
            return False, "git 不可用"
        except subprocess.TimeoutExpired:
            return False, "git 命令超时"

        output = (result.stdout or "").rstrip("\n")
        error = (result.stderr or "").strip()
        if result.returncode != 0:
            return False, error or output or f"git 命令失败: {' '.join(args)}"
        return True, output

    def is_git_repo(self) -> bool:
        ok, output = self._run_git("rev-parse", "--is-inside-work-tree")
        return ok and output == "true"

    def status_lines(self) -> list[str]:
        ok, status = self._run_git("status", "--short")
        if not ok or not status:
            return []
        return status.splitlines()

    def is_clean(self) -> bool:
        return len(self.status_lines()) == 0

    def changed_paths(self) -> list[str]:
        paths: list[str] = []
        for line in self.status_lines():
            body = line[3:].strip() if len(line) >= 4 else line.strip()
            if " -> " in body:
                body = body.split(" -> ", 1)[1]
            if body:
                paths.append(body)
        return paths

    def status_snapshot(self) -> RepoStatusSnapshot:
        if not self.is_git_repo():
            return RepoStatusSnapshot(in_repo=False)

        ok, branch_output = self._run_git("status", "--short", "--branch")
        branch_line = branch_output.splitlines()[0] if ok and branch_output else "## unknown"
        branch, tracking, ahead, behind = self._parse_branch_line(branch_line)

        staged = 0
        modified = 0
        untracked = 0
        for line in self.status_lines():
            code = line[:2]
            if code == "??":
                untracked += 1
                continue
            if code and code[0] != " ":
                staged += 1
            if len(code) > 1 and code[1] != " ":
                modified += 1

        return RepoStatusSnapshot(
            in_repo=True,
            branch=branch,
            tracking=tracking,
            ahead=ahead,
            behind=behind,
            staged=staged,
            modified=modified,
            untracked=untracked,
        )

    def _parse_branch_line(self, branch_line: str) -> tuple[str, str | None, int, int]:
        text = branch_line.removeprefix("## ").strip()
        ahead = 0
        behind = 0

        bracket = ""
        if " [" in text and text.endswith("]"):
            text, bracket = text.rsplit(" [", 1)
            bracket = bracket[:-1]

        tracking = None
        branch = text
        if "..." in text:
            branch, tracking = text.split("...", 1)

        if bracket:
            for chunk in bracket.split(","):
                chunk = chunk.strip()
                if chunk.startswith("ahead "):
                    try:
                        ahead = int(chunk.split()[1])
                    except (IndexError, ValueError):
                        ahead = 0
                if chunk.startswith("behind "):
                    try:
                        behind = int(chunk.split()[1])
                    except (IndexError, ValueError):
                        behind = 0

        return branch or "unknown", tracking, ahead, behind

    def branch_report(self) -> str:
        if not self.is_git_repo():
            return "当前工作区不是 Git 仓库。"

        ok, status = self._run_git("status", "--short", "--branch")
        if not ok:
            return status

        lines = status.splitlines()
        first_line = lines[0] if lines else "(无法读取分支信息)"
        ok_remote, remote = self._run_git("remote", "get-url", "origin")
        if ok_remote and remote:
            return f"{first_line}\norigin: {remote}"
        return first_line

    def status_report(self) -> str:
        if not self.is_git_repo():
            return "当前工作区不是 Git 仓库。"

        ok, status = self._run_git("status", "--short", "--branch")
        if not ok:
            return status

        ok_remote, remote = self._run_git("remote", "get-url", "origin")
        if ok_remote and remote:
            return f"{status}\norigin: {remote}"
        return status

    def diff_report(self, *, target: str | None = None, stat_only: bool = False) -> str:
        if not self.is_git_repo():
            return "当前工作区不是 Git 仓库。"

        args = ["diff"]
        if stat_only:
            args.append("--stat")
        if target:
            args.extend(["--", target])

        ok, diff = self._run_git(*args)
        if ok and diff:
            return diff

        if target:
            status_ok, path_status = self._run_git("status", "--short", "--", target)
            if status_ok and path_status.startswith("??"):
                preview_path = (self.workspace_root / target).resolve(strict=False)
                if preview_path.exists() and preview_path.is_file():
                    preview = preview_path.read_text(encoding="utf-8")
                    return (
                        f"{target} 还没有被 Git 跟踪，所以 `git diff` 不会显示它。\n\n"
                        f"[untracked file preview]\n{preview[:4000]}"
                    )
                return f"{target} 还没有被 Git 跟踪，所以 `git diff` 不会显示它。"

        if stat_only:
            status_ok, short_status = self._run_git("status", "--short")
            if status_ok and short_status:
                return f"(git diff --stat 没有输出)\n\n当前工作区变更：\n{short_status}"
        return diff or "没有可显示的 diff。"

    def patch_report(self, target: str | None = None) -> str:
        if not self.is_git_repo():
            return "当前工作区不是 Git 仓库。"

        if target:
            return self.diff_report(target=target, stat_only=False)

        tracked = self.diff_report(stat_only=False)
        sections: list[str] = []
        if tracked and tracked != "没有可显示的 diff。":
            sections.append(tracked)

        for line in self.status_lines():
            if not line.startswith("?? "):
                continue
            path = line[3:].strip()
            preview_path = (self.workspace_root / path).resolve(strict=False)
            if preview_path.is_dir():
                sections.append(f"[untracked dir] {path}/")
                continue
            try:
                preview = preview_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                sections.append(f"[untracked file] {path}")
                continue

            sections.append(
                "\n".join(
                    [
                        "--- /dev/null",
                        f"+++ b/{path}",
                        "[untracked file preview]",
                        _truncate_cli_output(preview, limit=3000),
                    ]
                )
            )

        if not sections:
            return "当前没有 patch 可预览。"
        return "\n\n".join(sections)

    def suggest_commit_message(self) -> str:
        paths = self.changed_paths()
        if not paths:
            return "chore: update project"

        if any(path.startswith(".vscode/") for path in paths):
            return "chore: add vscode jarvis workflow"
        if "agent.py" in paths and any(path.startswith("tests/") for path in paths):
            return "feat: improve jarvis workflow"
        if any(path in {"README.md", "CHANGELOG.md", "HARNESS.md"} for path in paths):
            return "docs: update project guidance"
        if len(paths) == 1:
            stem = Path(paths[0]).stem.replace("_", " ")
            return f"chore: update {stem}"
        return "chore: update project"

    def commit_all(self, message: str) -> tuple[bool, str]:
        if not self.is_git_repo():
            return False, "当前工作区不是 Git 仓库。"
        if self.is_clean():
            return False, "当前没有可提交的变更。"

        ok, output = self._run_git("add", "-A")
        if not ok:
            return False, output

        ok, output = self._run_git("commit", "-m", message)
        if not ok:
            return False, output
        return True, output


ASCII_BANNER = r"""
     _                  _
    | | __ _ _ ____   _(_)___
 _  | |/ _` | '__\ \ / / / __|
| |_| | (_| | |   \ V /| \__ \
 \___/ \__,_|_|    \_/ |_|___/
""".strip("\n")


def supports_color(stream=None) -> bool:
    stream = stream or sys.stdout
    is_tty = bool(getattr(stream, "isatty", lambda: False)())
    return is_tty and os.environ.get("TERM", "") not in {"", "dumb"}


def style_text(text: str, *, color: str, use_color: bool) -> str:
    if not use_color:
        return text
    codes = {
        "cyan": "36",
        "green": "32",
        "yellow": "33",
        "red": "31",
        "dim": "2",
        "bold": "1",
    }
    code = codes[color]
    return f"\033[{code}m{text}\033[0m"


def render_banner(*, use_color: bool) -> str:
    banner = style_text(ASCII_BANNER, color="cyan", use_color=use_color)
    subtitle = style_text("local coding agent", color="dim", use_color=use_color)
    return f"{banner}\n{subtitle}"


def format_git_summary(snapshot: RepoStatusSnapshot) -> str:
    if not snapshot.in_repo:
        return "git: not-a-repo"

    parts = [f"git: branch={snapshot.branch}"]
    if snapshot.ahead:
        parts.append(f"ahead={snapshot.ahead}")
    if snapshot.behind:
        parts.append(f"behind={snapshot.behind}")
    if snapshot.clean:
        parts.append("clean")
    else:
        if snapshot.staged:
            parts.append(f"staged={snapshot.staged}")
        if snapshot.modified:
            parts.append(f"modified={snapshot.modified}")
        if snapshot.untracked:
            parts.append(f"untracked={snapshot.untracked}")
    return " | ".join(parts)


def build_prompt_label(snapshot: RepoStatusSnapshot, *, auto_approve: bool) -> str:
    parts: list[str] = []
    if snapshot.in_repo:
        parts.append(snapshot.branch)
        if snapshot.ahead:
            parts.append(f"+{snapshot.ahead}")
        if snapshot.behind:
            parts.append(f"-{snapshot.behind}")
        if snapshot.clean:
            parts.append("clean")
        else:
            if snapshot.staged:
                parts.append(f"s{snapshot.staged}")
            if snapshot.modified:
                parts.append(f"m{snapshot.modified}")
            if snapshot.untracked:
                parts.append(f"u{snapshot.untracked}")
    else:
        parts.append("no-git")

    parts.append("auto" if auto_approve else "ask")
    return f"jarvis [{' '.join(parts)}]> "


def default_api_key(base_url: str) -> str:
    if "localhost" in base_url or "127.0.0.1" in base_url:
        return "ollama"
    return ""


def build_workspace_snapshot(workspace_root: Path) -> str:
    try:
        entries = sorted(workspace_root.iterdir(), key=lambda p: (p.is_file(), p.name))
    except OSError as exc:
        return f"(读取目录失败: {exc})"

    preview = []
    for item in entries[:30]:
        suffix = "/" if item.is_dir() else ""
        preview.append(f"- {item.name}{suffix}")
    if len(entries) > 30:
        preview.append(f"- ...（共 {len(entries)} 项，已截断）")
    return "\n".join(preview) if preview else "(空目录)"


def load_project_guide(workspace_root: Path) -> str:
    for filename in PROJECT_GUIDE_FILES:
        path = workspace_root / filename
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            return f"## 项目约定（来自 {filename}）\n(读取失败: {exc})"

        if len(text) > MAX_PROJECT_GUIDE_CHARS:
            text = (
                text[:MAX_PROJECT_GUIDE_CHARS]
                + f"\n... [项目约定过长，已截断，共 {len(text)} 字符]"
            )
        return f"## 项目约定（来自 {filename}）\n{text}"

    return ""


def build_system_prompt(config: AgentConfig, runtime: ToolRuntime) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        workspace_root=config.workspace_root,
        workspace_snapshot=build_workspace_snapshot(config.workspace_root),
        tool_summary=runtime.tool_summary(),
        project_guide=load_project_guide(config.workspace_root),
    )


def _find_top_level_json_objects(text: str):
    """顺序扫描，找出 text 里所有合法的顶层 JSON 对象。"""
    decoder = json.JSONDecoder()
    results = []
    i = 0
    n = len(text)
    while i < n:
        brace = text.find("{", i)
        if brace == -1:
            break
        try:
            obj, end = decoder.raw_decode(text, brace)
            results.append(obj)
            i = end
        except json.JSONDecodeError:
            i = brace + 1
    return results


def extract_fake_tool_calls(content: str, tool_names: set[str]):
    """从普通文本里抠出模型伪装的 tool call。"""
    if not content:
        return []

    fragments = []

    for match in re.finditer(r"<tool_call>(.*?)</tool_call>", content, re.DOTALL):
        fragments.append(match.group(1))

    for match in re.finditer(r"```(?:json)?\s*(.*?)```", content, re.DOTALL):
        fragments.append(match.group(1))

    if not fragments:
        fragments.append(content)

    results = []
    for fragment in fragments:
        for obj in _find_top_level_json_objects(fragment):
            if not isinstance(obj, dict):
                continue
            name = obj.get("name")
            args = obj.get("arguments") or obj.get("parameters") or {}
            if name in tool_names and isinstance(args, dict):
                results.append((name, args))
    return results


def pretty_tool_call(name: str, args: dict) -> str:
    args_str = json.dumps(args, ensure_ascii=False)
    if len(args_str) > 140:
        args_str = args_str[:140] + "..."
    return f"→ {name}({args_str})"


def pretty_tool_result(result: str) -> str:
    if len(result) > 400:
        return result[:400] + f"\n... [共 {len(result)} 字符]"
    return result


class AgentSession:
    def __init__(self, config: AgentConfig):
        self.config = config
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "缺少 openai 依赖，请先执行 `pip install -r requirements.txt`"
            ) from exc

        self.client = OpenAI(base_url=config.base_url, api_key=config.api_key)
        self.runtime = ToolRuntime(
            workspace_root=config.workspace_root,
            auto_approve=config.auto_approve,
            command_timeout=config.command_timeout,
        )
        self.inspector = WorkspaceInspector(config.workspace_root)
        self.tool_names = self.runtime.tool_names()
        self.activity_log: list[ActivityEntry] = []
        self.reset()

    def reset(self) -> None:
        self.messages = [
            {
                "role": "system",
                "content": build_system_prompt(self.config, self.runtime),
            }
        ]
        self.activity_log.clear()
        self.log_activity("system", "会话已初始化")

    def log_activity(self, kind: str, summary: str) -> None:
        entry = ActivityEntry(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            kind=kind,
            summary=summary,
        )
        self.activity_log.append(entry)
        if len(self.activity_log) > 200:
            self.activity_log = self.activity_log[-200:]

    def _confirm(self, prompt: str) -> bool:
        if self.runtime.auto_approve:
            return True
        if not sys.stdin.isatty():
            return False
        answer = input(f"{prompt} 输入 y 继续，其余任意键取消: ").strip().lower()
        return answer in {"y", "yes"}

    def summary_report(self, limit: int = 8) -> str:
        lines = ["本轮摘要"]

        entries = self.activity_log[-limit:]
        if entries:
            lines.append("")
            lines.append("最近动作：")
            lines.extend(
                f"- {entry.timestamp} [{entry.kind}] {entry.summary}" for entry in entries
            )

        if self.inspector.is_git_repo():
            lines.append("")
            lines.append("Git 状态：")
            lines.append(self.inspector.status_report())

            diff_stat = self.inspector.diff_report(stat_only=True)
            if diff_stat:
                lines.append("")
                lines.append("Diff 摘要：")
                lines.append(diff_stat)

            if not self.inspector.is_clean():
                lines.append("")
                lines.append(
                    f"建议 commit message: {self.inspector.suggest_commit_message()}"
                )
        else:
            lines.append("")
            lines.append("当前工作区不是 Git 仓库。")

        return "\n".join(lines)

    def render_repl_header(self) -> str:
        use_color = supports_color()
        snapshot = self.inspector.status_snapshot()
        lines = [
            render_banner(use_color=use_color),
            "",
            style_text(f"workspace: {self.config.workspace_root}", color="bold", use_color=use_color),
            f"model: {self.config.model} | approval: {'auto' if self.runtime.auto_approve else 'ask'}",
            format_git_summary(snapshot),
            "commands: /help /patch /summary /status /diff /commit /quit",
        ]
        return "\n".join(lines)

    def prompt_label(self) -> str:
        return build_prompt_label(
            self.inspector.status_snapshot(), auto_approve=self.runtime.auto_approve
        )

    def commit_current_changes(self, message: str | None = None) -> str:
        if not self.inspector.is_git_repo():
            return "当前工作区不是 Git 仓库。"
        if self.inspector.is_clean():
            return "当前没有可提交的变更。"

        commit_message = message or self.inspector.suggest_commit_message()
        preview = "\n".join(
            [
                "准备创建 commit：",
                f"message: {commit_message}",
                "",
                "将提交这些变更：",
                self.inspector.status_report(),
            ]
        )
        print(preview)
        if not self._confirm("确认创建这个 commit 吗？"):
            return "已取消 commit。"

        ok, output = self.inspector.commit_all(commit_message)
        if ok:
            self.log_activity("commit", commit_message)
        return output

    def add_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self.log_activity("user", text[:200])

    def run_until_idle(self) -> bool:
        for turn in range(1, self.config.max_turns + 1):
            print(f"\n--- turn {turn} ---")

            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=self.messages,
                tools=self.runtime.tool_schemas,
                tool_choice="auto",
                extra_body={"options": {"num_ctx": self.config.num_ctx}},
            )
            msg = response.choices[0].message

            tool_calls = []
            if msg.tool_calls:
                for tool_call in msg.tool_calls:
                    try:
                        args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    tool_calls.append(
                        {
                            "id": tool_call.id,
                            "name": tool_call.function.name,
                            "args": args,
                        }
                    )
            else:
                for name, args in extract_fake_tool_calls(
                    msg.content or "", self.tool_names
                ):
                    tool_calls.append(
                        {
                            "id": f"fake_{uuid.uuid4().hex[:8]}",
                            "name": name,
                            "args": args,
                        }
                    )
                if tool_calls:
                    print("[note] 从普通文本里解析到 tool call")

            assistant_msg = {"role": "assistant", "content": msg.content or ""}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tool_call["id"],
                        "type": "function",
                        "function": {
                            "name": tool_call["name"],
                            "arguments": json.dumps(
                                tool_call["args"], ensure_ascii=False
                            ),
                        },
                    }
                    for tool_call in tool_calls
                ]
                if not msg.tool_calls:
                    assistant_msg["content"] = ""
            self.messages.append(assistant_msg)

            if msg.content and not (tool_calls and not msg.tool_calls):
                print(f"[assistant] {msg.content}")
                self.log_activity("assistant", msg.content[:200])

            if not tool_calls:
                print("\n=== 任务结束 ===")
                return True

            for tool_call in tool_calls:
                print(pretty_tool_call(tool_call["name"], tool_call["args"]))
                self.log_activity(
                    "tool_call",
                    pretty_tool_call(tool_call["name"], tool_call["args"]),
                )
                result = self.runtime.execute_tool(tool_call["name"], tool_call["args"])
                print(pretty_tool_result(result))
                self.log_activity(
                    "tool_result",
                    pretty_tool_result(result).replace("\n", " ")[:200],
                )

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": result,
                    }
                )

        print(f"\n=== 达到最大轮数 {self.config.max_turns}，强制停止 ===")
        return False

    def handle_user_turn(self, text: str) -> bool:
        self.add_user_message(text)
        return self.run_until_idle()

    def handle_slash_command(self, raw: str) -> bool:
        parts = shlex.split(raw)
        command = parts[0]
        args = parts[1:]
        self.log_activity("slash", raw)

        if command in {"/quit", "/exit"}:
            print("bye")
            return False

        if command == "/help":
            print(
                "\n".join(
                    [
                        "可用命令：",
                        "  /help   查看帮助",
                        "  /tools  查看工具说明",
                        "  /pwd    显示工作区根目录",
                        "  /status 查看当前 Git 状态",
                        "  /branch 查看当前分支",
                        "  /diff [--stat|path] 查看改动",
                        "  /patch [path] 预览这次修改的 patch",
                        "  /summary [N] 查看本轮摘要",
                        "  /commit [message] 提交当前变更",
                        "  /history [N] 查看最近会话动作",
                        "  /approve [on|off|status] 查看或切换审批模式",
                        "  /clear  清空当前会话历史",
                        "  /quit   退出",
                    ]
                )
            )
            return True

        if command == "/tools":
            print(self.runtime.tool_summary())
            return True

        if command == "/pwd":
            print(self.config.workspace_root)
            return True

        if command == "/status":
            print(self.inspector.status_report())
            return True

        if command == "/branch":
            print(self.inspector.branch_report())
            return True

        if command == "/diff":
            stat_only = False
            target = None
            if args:
                if args[0] == "--stat":
                    stat_only = True
                    if len(args) > 1:
                        target = args[1]
                else:
                    target = args[0]
            print(_truncate_cli_output(self.inspector.diff_report(target=target, stat_only=stat_only)))
            return True

        if command == "/patch":
            target = args[0] if args else None
            print(_truncate_cli_output(self.inspector.patch_report(target=target)))
            return True

        if command == "/summary":
            limit = 8
            if args:
                try:
                    limit = max(1, int(args[0]))
                except ValueError:
                    print("用法: /summary [N]")
                    return True
            print(_truncate_cli_output(self.summary_report(limit=limit)))
            return True

        if command == "/commit":
            message = " ".join(args).strip() or None
            print(_truncate_cli_output(self.commit_current_changes(message=message)))
            return True

        if command == "/history":
            limit = 20
            if args:
                try:
                    limit = max(1, int(args[0]))
                except ValueError:
                    print("用法: /history [N]")
                    return True

            entries = self.activity_log[-limit:]
            if not entries:
                print("当前没有会话动作。")
                return True
            print(
                "\n".join(
                    f"{entry.timestamp} [{entry.kind}] {entry.summary}" for entry in entries
                )
            )
            return True

        if command == "/approve":
            if not args or args[0] == "status":
                mode = "on" if self.runtime.auto_approve else "off"
                print(f"auto-approve: {mode}")
                return True
            if args[0] == "on":
                self.runtime.auto_approve = True
                print("auto-approve: on")
                return True
            if args[0] == "off":
                self.runtime.auto_approve = False
                print("auto-approve: off")
                return True
            print("用法: /approve [on|off|status]")
            return True

        if command == "/clear":
            self.reset()
            print("会话历史已清空。")
            return True

        print(f"未知命令: {command}，输入 /help 查看帮助。")
        return True

    def repl(self) -> None:
        print(self.render_repl_header())

        while True:
            try:
                user_input = input(f"\n{self.prompt_label()}").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nbye")
                return

            if not user_input:
                continue

            if user_input.startswith("/"):
                if not self.handle_slash_command(user_input):
                    return
                continue

            try:
                self.handle_user_turn(user_input)
            except KeyboardInterrupt:
                print("\n[interrupted]")
            except Exception as exc:
                print(f"\n[error] {type(exc).__name__}: {exc}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="本地最小编码 agent")
    parser.add_argument("task", nargs="*", help="一次性任务描述；不传则进入 REPL")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="模型名")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI 兼容 API 地址")
    parser.add_argument("--api-key", default=None, help="API key；本地 Ollama 可留空")
    parser.add_argument(
        "--cwd",
        default=".",
        help="工作区根目录。所有相对路径都以这里为基准。",
    )
    parser.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX, help="上下文窗口")
    parser.add_argument(
        "--max-turns", type=int, default=DEFAULT_MAX_TURNS, help="单个用户任务最多迭代轮数"
    )
    parser.add_argument(
        "--command-timeout",
        type=int,
        default=DEFAULT_COMMAND_TIMEOUT,
        help="run_command 超时时间（秒）",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="跳过写文件/执行命令前的确认提示",
    )
    parser.add_argument(
        "--repl",
        action="store_true",
        help="强制进入 REPL，即使传了任务文本",
    )
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> AgentConfig:
    workspace_root = Path(args.cwd).expanduser().resolve()
    api_key = args.api_key if args.api_key is not None else default_api_key(args.base_url)
    return AgentConfig(
        model=args.model,
        base_url=args.base_url,
        api_key=api_key,
        num_ctx=args.num_ctx,
        max_turns=args.max_turns,
        workspace_root=workspace_root,
        auto_approve=args.auto_approve,
        command_timeout=args.command_timeout,
    )


def _truncate_cli_output(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [输出过长，已截断，共 {len(text)} 字符]"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = build_config(args)
    session = AgentSession(config)

    if args.repl or not args.task:
        session.repl()
        return 0

    task = " ".join(args.task)
    try:
        session.handle_user_turn(task)
    except KeyboardInterrupt:
        print("\n[interrupted]")
        return 130
    except Exception as exc:
        print(f"\n[error] {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
