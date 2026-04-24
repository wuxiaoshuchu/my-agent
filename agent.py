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
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter

from context_engine import (
    SessionMemory,
    build_context_stats,
    compact_messages,
    conversation_messages,
    render_session_memory,
    should_auto_compact,
)
from performance_trace import (
    ModelRequestTrace,
    build_request_payload_profile,
    render_payload_profile,
    summarize_request_trace,
)
from runtime_config import (
    CONFIG_FILENAME,
    RuntimeConfigSources,
    describe_runtime_provider,
    list_local_models,
    load_workspace_runtime_config,
    normalize_positive_int,
    normalize_string_setting,
    resolve_runtime_value,
    save_workspace_runtime_config,
    workspace_config_path,
)
from tools import ToolRuntime
from workspace_inspector import RepoStatusSnapshot, WorkspaceInspector


DEFAULT_MODEL = "qwen2.5-coder:7b"
DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_NUM_CTX = 16384
DEFAULT_MAX_TURNS = 20
DEFAULT_COMMAND_TIMEOUT = 30
PROJECT_GUIDE_FILES = ("HARNESS.md", "CLAUDE.md")
ROADMAP_GUIDE_FILES = ("way-to-claw-code.md",)
MAX_PROJECT_GUIDE_CHARS = 4000
MAX_ROADMAP_GUIDE_CHARS = 2600
LOW_SIGNAL_GOAL_TEXTS = {
    "继续",
    "继续吧",
    "继续做",
    "接着来",
    "接着做",
    "go on",
    "continue",
    "keep going",
    "next",
}


SYSTEM_PROMPT_TEMPLATE = """你是一个本地编码助手，用户在 Mac 终端里和你对话。

## 当前工作区
- 工作区根目录：{workspace_root}
- 根目录预览：
{workspace_snapshot}

## 可用工具
{tool_summary}

{project_guide}
{roadmap_guide}

## 行为规则
- 优先使用专用工具而不是 shell：
  - 读文件用 read_file
  - 搜索文件用 list_files
  - 搜索文本用 grep_text
  - 单处局部改动优先用 edit_file
  - 多处局部改动优先用 apply_patch
  - 新建文件或整文件重写再用 write_file
- 只有在专用工具做不到时，再使用 run_command
- 你可以连续调用多个工具，但任务完成后必须输出纯文字总结
- 不要伪造“我已经改好了”之类的描述；修改必须真的通过工具完成
- 如果 system memory 提到了历史摘要，把它当成压缩后的事实索引；需要精确细节时再读文件或查看最近 turn
- 如果用户这一轮只说“继续 / continue”等低信息跟进，优先沿用 Session Memory 里的当前任务目标继续执行

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
    workspace_config_path: Path
    runtime_sources: RuntimeConfigSources


@dataclass
class ActivityEntry:
    timestamp: str
    kind: str
    summary: str


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


def load_workspace_guide(
    workspace_root: Path,
    *,
    filenames: tuple[str, ...],
    heading: str,
    max_chars: int,
) -> str:
    for filename in filenames:
        path = workspace_root / filename
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            return f"## {heading}（来自 {filename}）\n(读取失败: {exc})"

        if len(text) > max_chars:
            text = (
                text[:max_chars]
                + f"\n... [{heading} 过长，已截断，共 {len(text)} 字符]"
            )
        return f"## {heading}（来自 {filename}）\n{text}"

    return ""


def load_project_guide(workspace_root: Path) -> str:
    return load_workspace_guide(
        workspace_root,
        filenames=PROJECT_GUIDE_FILES,
        heading="项目约定",
        max_chars=MAX_PROJECT_GUIDE_CHARS,
    )


def load_roadmap_guide(workspace_root: Path) -> str:
    return load_workspace_guide(
        workspace_root,
        filenames=ROADMAP_GUIDE_FILES,
        heading="长期路线图",
        max_chars=MAX_ROADMAP_GUIDE_CHARS,
    )


def build_system_prompt(config: AgentConfig, runtime: ToolRuntime) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        workspace_root=config.workspace_root,
        workspace_snapshot=build_workspace_snapshot(config.workspace_root),
        tool_summary=runtime.tool_summary(),
        project_guide=load_project_guide(config.workspace_root),
        roadmap_guide=load_roadmap_guide(config.workspace_root),
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
            name = obj.get("name") or obj.get("function_name")
            args = (
                obj.get("arguments")
                or obj.get("parameters")
                or obj.get("input")
                or obj.get("args")
                or {}
            )
            if name in tool_names and isinstance(args, dict):
                results.append((name, args))
    return results


def resolve_active_goal(previous_goal: str, new_user_text: str) -> str:
    text = new_user_text.strip()
    normalized = re.sub(r"\s+", " ", text.lower())
    if not text:
        return previous_goal
    if normalized in LOW_SIGNAL_GOAL_TEXTS and previous_goal:
        return previous_goal
    if len(text) <= 12 and previous_goal and normalized in {"ok", "好的", "收到"}:
        return previous_goal
    return text


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
        self.memory = SessionMemory()
        self.activity_log.clear()
        self.request_traces: list[ModelRequestTrace] = []
        self.rebuild_messages([])
        self.log_activity("system", "会话已初始化")

    def non_system_messages(self) -> list[dict[str, object]]:
        return conversation_messages(self.messages)

    def rebuild_messages(
        self,
        conversation: list[dict[str, object]] | None = None,
    ) -> None:
        if conversation is None:
            conversation = self.non_system_messages()
        self.messages = [
            {
                "role": "system",
                "content": build_system_prompt(self.config, self.runtime),
            }
        ]
        memory_prompt = render_session_memory(self.memory)
        if memory_prompt:
            self.messages.append({"role": "system", "content": memory_prompt})
        self.messages.extend(conversation)

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

    def context_report(self) -> str:
        stats = build_context_stats(self.messages)
        lines = [
            f"总消息数: {stats.total_messages}",
            f"非 system 消息数: {stats.non_system_messages}",
            f"turn 数: {stats.turn_count}",
            f"估算 tokens: {stats.estimated_tokens} / num_ctx {self.config.num_ctx}",
            f"active goal: {self.memory.active_goal or '(空)'}",
            f"compact 次数: {len(self.memory.compaction_blocks)}",
        ]
        return "\n".join(lines)

    def compact_history(self, *, reason: str) -> str:
        result = compact_messages(
            self.messages,
            memory=self.memory,
            reason=reason,
        )
        if not result.compacted:
            return "当前会话还不需要 compact。\n\n" + self.context_report()

        self.memory = result.memory
        self.rebuild_messages(result.kept_messages)
        after_stats = build_context_stats(self.messages)
        summary = (
            f"已 compact 历史上下文（{reason}）\n"
            f"- dropped turns: {result.dropped_turns}\n"
            f"- dropped messages: {result.dropped_messages}\n"
            f"- tokens: {result.before_stats.estimated_tokens} -> {after_stats.estimated_tokens}\n"
            f"- kept recent turns: {after_stats.turn_count}\n"
            f"- active goal: {self.memory.active_goal or '(空)'}"
        )
        self.log_activity(
            "compact",
            f"{reason}: turns={result.dropped_turns} messages={result.dropped_messages}",
        )
        return summary

    def maybe_auto_compact(self) -> None:
        if not should_auto_compact(self.messages, num_ctx=self.config.num_ctx):
            return
        report = self.compact_history(reason="auto")
        print(f"[compact] {report}")

    def summary_report(self, limit: int = 8) -> str:
        lines = ["本轮摘要"]
        request_traces = getattr(self, "request_traces", [])

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

        lines.append("")
        lines.append("Context：")
        lines.append(self.context_report())

        if request_traces:
            lines.append("")
            lines.append("Model 请求：")
            lines.extend(
                f"- {summarize_request_trace(trace)}"
                for trace in request_traces[-min(limit, 3) :]
            )

        return "\n".join(lines)

    def performance_report(self, limit: int = 5) -> str:
        request_traces = getattr(self, "request_traces", [])
        current_payload = build_request_payload_profile(
            self.messages,
            self.runtime.tool_schemas,
            turn=len(request_traces) + 1,
        )
        lines = [
            "性能观察",
            "",
            "当前请求载荷：",
            render_payload_profile(current_payload),
            "",
            "最近模型请求：",
        ]
        if not request_traces:
            lines.append("- 还没有模型请求。")
            return "\n".join(lines)

        lines.extend(
            f"- {summarize_request_trace(trace)}"
            for trace in request_traces[-limit:]
        )
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
            "commands: /help /model /compact /perf /patch /summary /status /diff /commit /quit",
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

    def model_report(self) -> str:
        lines = [
            "模型运行时",
            "",
            f"当前模型: {self.config.model}",
            f"模型来源: {self.config.runtime_sources.model}",
            f"base URL: {self.config.base_url}",
            f"base URL 来源: {self.config.runtime_sources.base_url}",
            f"provider: {describe_runtime_provider(self.config.base_url)}",
            f"num_ctx: {self.config.num_ctx}",
            f"num_ctx 来源: {self.config.runtime_sources.num_ctx}",
            f"工作区配置: {self.config.workspace_config_path}",
        ]

        models, error = list_local_models()
        lines.append("")
        lines.append("本地 Ollama 模型：")
        if error:
            lines.append(f"- {error}")
        elif not models:
            lines.append("- 当前没有检测到已安装模型。")
        else:
            for record in models:
                marker = " [current]" if record.name == self.config.model else ""
                lines.append(
                    f"- {record.name} | {record.size} | {record.modified}{marker}"
                )

        lines.extend(
            [
                "",
                "用法：",
                "  /model 查看当前模型与本地模型列表",
                "  /model use <name> 只切换当前会话",
                f"  /model set <name> 切换并写入 {CONFIG_FILENAME}",
                "  /model ctx <N> 更新并写入 num_ctx",
            ]
        )
        return "\n".join(lines)

    def use_model(self, model_name: str, *, persist: bool) -> str:
        model_name = model_name.strip()
        if not model_name:
            return "模型名不能为空。"

        self.config.model = model_name
        if persist:
            config_path = save_workspace_runtime_config(
                self.config.workspace_root,
                {"model": model_name},
            )
            self.config.workspace_config_path = config_path
            self.config.runtime_sources = RuntimeConfigSources(
                model=f"workspace:{config_path.name}",
                base_url=self.config.runtime_sources.base_url,
                num_ctx=self.config.runtime_sources.num_ctx,
            )
            self.log_activity("model", f"已切换默认模型到 {model_name}")
            return (
                f"已切换默认模型到 {model_name}\n"
                f"配置文件: {config_path}"
            )

        self.config.runtime_sources = RuntimeConfigSources(
            model="session",
            base_url=self.config.runtime_sources.base_url,
            num_ctx=self.config.runtime_sources.num_ctx,
        )
        self.log_activity("model", f"已切换当前会话模型到 {model_name}")
        return f"已切换当前会话模型到 {model_name}"

    def set_num_ctx(self, num_ctx: int) -> str:
        self.config.num_ctx = num_ctx
        config_path = save_workspace_runtime_config(
            self.config.workspace_root,
            {"num_ctx": num_ctx},
        )
        self.config.workspace_config_path = config_path
        self.config.runtime_sources = RuntimeConfigSources(
            model=self.config.runtime_sources.model,
            base_url=self.config.runtime_sources.base_url,
            num_ctx=f"workspace:{config_path.name}",
        )
        self.log_activity("model", f"已更新默认 num_ctx 到 {num_ctx}")
        return f"已更新默认 num_ctx 到 {num_ctx}\n配置文件: {config_path}"

    def add_user_message(self, text: str) -> None:
        self.memory = SessionMemory(
            active_goal=resolve_active_goal(self.memory.active_goal, text),
            compaction_blocks=self.memory.compaction_blocks,
        )
        self.rebuild_messages()
        self.messages.append({"role": "user", "content": text})
        self.log_activity("user", text[:200])

    def run_until_idle(self) -> bool:
        for turn in range(1, self.config.max_turns + 1):
            print(f"\n--- turn {turn} ---")
            self.maybe_auto_compact()
            payload_profile = build_request_payload_profile(
                self.messages,
                self.runtime.tool_schemas,
                turn=turn,
            )
            self.log_activity(
                "model_request",
                summarize_request_trace(
                    ModelRequestTrace(
                        turn=turn,
                        status="pending",
                        duration_ms=0,
                        tool_calls=0,
                        content_chars=0,
                        payload=payload_profile,
                    )
                ),
            )

            request_kwargs = dict(
                model=self.config.model,
                messages=self.messages,
                extra_body={"options": {"num_ctx": self.config.num_ctx}},
            )
            if self.runtime.tool_schemas:
                request_kwargs["tools"] = self.runtime.tool_schemas
                request_kwargs["tool_choice"] = "auto"

            start = perf_counter()
            try:
                response = self.client.chat.completions.create(**request_kwargs)
            except Exception as exc:
                duration_ms = int((perf_counter() - start) * 1000)
                status = "timeout" if type(exc).__name__ == "APITimeoutError" else "error"
                trace = ModelRequestTrace(
                    turn=turn,
                    status=status,
                    duration_ms=duration_ms,
                    tool_calls=0,
                    content_chars=0,
                    payload=payload_profile,
                    error=f"{type(exc).__name__}: {exc}",
                )
                self.request_traces.append(trace)
                self.log_activity("model_error", summarize_request_trace(trace))
                raise
            msg = response.choices[0].message

            tool_calls = []
            parsed_from_text = False
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
                    parsed_from_text = True
                    print("[note] 从普通文本里解析到 tool call")
                    self.log_activity(
                        "tool_parse",
                        ", ".join(tool_call["name"] for tool_call in tool_calls),
                    )

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
            trace = ModelRequestTrace(
                turn=turn,
                status="ok",
                duration_ms=int((perf_counter() - start) * 1000),
                tool_calls=len(tool_calls),
                content_chars=len(msg.content or ""),
                payload=payload_profile,
            )
            self.request_traces.append(trace)
            self.log_activity("model_response", summarize_request_trace(trace))

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
                        "  /model  查看或切换模型配置",
                        "  /compact 压缩较早会话历史",
                        "  /perf [N] 查看当前请求载荷和最近模型请求",
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

        if command == "/model":
            if not args:
                print(_truncate_cli_output(self.model_report()))
                return True

            subcommand = args[0]
            if subcommand == "use":
                if len(args) != 2:
                    print("用法: /model use <name>")
                    return True
                print(self.use_model(args[1], persist=False))
                return True

            if subcommand == "set":
                if len(args) != 2:
                    print("用法: /model set <name>")
                    return True
                print(self.use_model(args[1], persist=True))
                return True

            if subcommand == "ctx":
                if len(args) != 2:
                    print("用法: /model ctx <N>")
                    return True
                try:
                    num_ctx = normalize_positive_int(args[1], field_name="num_ctx")
                except ValueError as exc:
                    print(exc)
                    return True
                print(self.set_num_ctx(num_ctx))
                return True

            print("用法: /model [use <name> | set <name> | ctx <N>]")
            return True

        if command == "/compact":
            print(self.compact_history(reason="manual"))
            return True

        if command == "/perf":
            limit = 5
            if args:
                try:
                    limit = max(1, int(args[0]))
                except ValueError:
                    print("用法: /perf [N]")
                    return True
            print(_truncate_cli_output(self.performance_report(limit=limit)))
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
    parser.add_argument("--model", default=None, help="模型名；默认读取 jarvis.config.json 或内置默认值")
    parser.add_argument(
        "--base-url",
        default=None,
        help="OpenAI 兼容 API 地址；默认读取 jarvis.config.json 或内置默认值",
    )
    parser.add_argument("--api-key", default=None, help="API key；本地 Ollama 可留空")
    parser.add_argument(
        "--cwd",
        default=".",
        help="工作区根目录。所有相对路径都以这里为基准。",
    )
    parser.add_argument(
        "--num-ctx",
        type=int,
        default=None,
        help="上下文窗口；默认读取 jarvis.config.json 或内置默认值",
    )
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
    workspace_config = load_workspace_runtime_config(workspace_root)
    config_path = workspace_config_path(workspace_root)
    config_label = f"workspace:{config_path.name}"

    model_value, model_source = resolve_runtime_value(
        cli_value=args.model,
        config_value=workspace_config.get("model"),
        default=DEFAULT_MODEL,
        config_label=config_label,
    )
    base_url_value, base_url_source = resolve_runtime_value(
        cli_value=args.base_url,
        config_value=workspace_config.get("base_url"),
        default=DEFAULT_BASE_URL,
        config_label=config_label,
    )
    num_ctx_value, num_ctx_source = resolve_runtime_value(
        cli_value=args.num_ctx,
        config_value=workspace_config.get("num_ctx"),
        default=DEFAULT_NUM_CTX,
        config_label=config_label,
    )

    model = normalize_string_setting(model_value, field_name="model")
    base_url = normalize_string_setting(base_url_value, field_name="base_url")
    num_ctx = normalize_positive_int(num_ctx_value, field_name="num_ctx")
    api_key = args.api_key if args.api_key is not None else default_api_key(base_url)
    return AgentConfig(
        model=model,
        base_url=base_url,
        api_key=api_key,
        num_ctx=num_ctx,
        max_turns=args.max_turns,
        workspace_root=workspace_root,
        auto_approve=args.auto_approve,
        command_timeout=args.command_timeout,
        workspace_config_path=config_path,
        runtime_sources=RuntimeConfigSources(
            model=model_source,
            base_url=base_url_source,
            num_ctx=num_ctx_source,
        ),
    )


def _truncate_cli_output(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [输出过长，已截断，共 {len(text)} 字符]"


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        config = build_config(args)
        session = AgentSession(config)
    except Exception as exc:
        print(f"[error] {type(exc).__name__}: {exc}")
        return 1

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
