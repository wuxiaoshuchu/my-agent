from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from time import sleep

from openai import OpenAI

from agent import AgentSession, build_config, parse_args
from benchmark_harness import evaluate_output, extract_tool_names
from context_engine import SessionMemory, render_session_memory


@dataclass(frozen=True)
class ContextLiveTask:
    task_id: str
    description: str
    active_goal: str
    prompt: str
    checks: list[str]
    prefill_turns: int = 0
    prefill_chunk: str = ""
    prefill_repeat: int = 1
    num_ctx: int = 2048
    max_turns: int = 4
    request_timeout: int = 120
    required_compactions: int = 0
    required_tools: list[str] = ()
    max_parsed_tool_calls: int | None = None
    use_tools: bool = True
    warmup: bool = True
    max_attempts: int = 2


@dataclass(frozen=True)
class ContextLiveTaskResult:
    task_id: str
    description: str
    duration_ms: int
    passed: bool
    checks_passed: int
    total_checks: int
    missing_checks: list[str]
    compact_count: int
    parsed_tool_calls: int
    tool_names: list[str]
    final_output: str
    memory_preview: str
    transcript_excerpt: str
    error: str | None = None


@dataclass(frozen=True)
class ContextLiveRun:
    model: str
    workspace_root: str
    tasks_file: str
    started_at: str
    finished_at: str
    total_duration_ms: int
    task_results: list[ContextLiveTaskResult]


def load_context_live_tasks(path: Path) -> list[ContextLiveTask]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} 必须是 JSON array。")

    tasks: list[ContextLiveTask] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError(f"{path} 里的 task 必须是 object。")
        task_id = str(item["id"]).strip()
        prompt = str(item["prompt"]).strip()
        active_goal = str(item["active_goal"]).strip()
        checks = [str(check).strip() for check in item.get("checks", []) if str(check).strip()]
        if not task_id or not prompt or not active_goal:
            raise ValueError(f"{path} 里的 task 必须包含非空 id / prompt / active_goal。")
        tasks.append(
            ContextLiveTask(
                task_id=task_id,
                description=str(item.get("description", "")).strip(),
                active_goal=active_goal,
                prompt=prompt,
                checks=checks,
                prefill_turns=int(item.get("prefill_turns", 0)),
                prefill_chunk=str(item.get("prefill_chunk", "")).strip(),
                prefill_repeat=int(item.get("prefill_repeat", 1)),
                num_ctx=int(item.get("num_ctx", 2048)),
                max_turns=int(item.get("max_turns", 4)),
                request_timeout=int(item.get("request_timeout", 120)),
                required_compactions=int(item.get("required_compactions", 0)),
                required_tools=[str(name).strip() for name in item.get("required_tools", []) if str(name).strip()],
                max_parsed_tool_calls=(
                    None
                    if item.get("max_parsed_tool_calls") is None
                    else int(item["max_parsed_tool_calls"])
                ),
                use_tools=bool(item.get("use_tools", True)),
                warmup=bool(item.get("warmup", True)),
                max_attempts=int(item.get("max_attempts", 2)),
            )
        )
    return tasks


def build_prefill_messages(task: ContextLiveTask) -> list[dict[str, object]]:
    if task.prefill_turns <= 0:
        return []
    chunk = (" " + task.prefill_chunk.strip()) * max(task.prefill_repeat, 1)
    messages: list[dict[str, object]] = []
    for index in range(task.prefill_turns):
        messages.append(
            {
                "role": "user",
                "content": (
                    f"背景补充 {index + 1}：{chunk}\n"
                    f"当前任务主线不要丢失：{task.active_goal}"
                ).strip(),
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": (
                    f"收到，第 {index + 1} 轮背景我记住了。"
                    f" 当前任务主线仍然是：{task.active_goal}"
                ),
            }
        )
    return messages


def latest_assistant_output(messages: list[dict[str, object]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "assistant" and message.get("content"):
            return str(message["content"])
    return ""


def warmup_model(client: OpenAI, *, model: str, num_ctx: int) -> None:
    client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with OK only."}],
        stream=False,
        extra_body={"options": {"num_ctx": min(num_ctx, 2048)}},
    )


def run_context_live_task(
    *,
    workspace_root: Path,
    model: str,
    base_url: str | None,
    task: ContextLiveTask,
) -> ContextLiveTaskResult:
    error: str | None = None
    duration_ms = 0
    final_output = ""
    compact_entries = []
    tool_names: list[str] = []
    parsed_tool_calls = []
    memory_preview = ""
    transcript_excerpt = ""

    for attempt in range(1, max(task.max_attempts, 1) + 1):
        config_args = [
            "--cwd",
            str(workspace_root),
            "--model",
            model,
            "--num-ctx",
            str(task.num_ctx),
            "--max-turns",
            str(task.max_turns),
            "--auto-approve",
            "--repl",
        ]
        if base_url:
            config_args.extend(["--base-url", base_url])

        config = build_config(parse_args(config_args))
        session = AgentSession(config)
        session.client = OpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=task.request_timeout,
            max_retries=0,
        )
        if not task.use_tools:
            session.runtime.tool_schemas = []
            session.tool_names = set()
        if task.warmup:
            try:
                warmup_model(session.client, model=config.model, num_ctx=config.num_ctx)
            except Exception:
                pass

        session.memory = SessionMemory(active_goal=task.active_goal)
        session.activity_log.clear()
        session.rebuild_messages(build_prefill_messages(task))
        session.log_activity("system", f"live regression seeded (attempt {attempt})")

        start = perf_counter()
        error = None
        try:
            session.handle_user_turn(task.prompt)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        duration_ms = int((perf_counter() - start) * 1000)

        final_output = latest_assistant_output(session.messages)
        compact_entries = [entry for entry in session.activity_log if entry.kind == "compact"]
        tool_summaries = [entry.summary for entry in session.activity_log if entry.kind == "tool_call"]
        tool_names = extract_tool_names(tool_summaries)
        parsed_tool_calls = [entry for entry in session.activity_log if entry.kind == "tool_parse"]
        memory_preview = render_session_memory(session.memory)
        transcript_excerpt = "\n".join(
            f"{entry.timestamp} [{entry.kind}] {entry.summary}" for entry in session.activity_log[-12:]
        )
        if error and error.startswith("APIConnectionError:") and attempt < max(task.max_attempts, 1):
            sleep(1)
            continue
        break

    passed, missing, checks_passed = evaluate_output(final_output, task.checks)

    if len(compact_entries) < task.required_compactions:
        missing.append(f"required_compactions:{task.required_compactions}")
    for name in task.required_tools:
        if name not in tool_names:
            missing.append(f"required_tool:{name}")
    if (
        task.max_parsed_tool_calls is not None
        and len(parsed_tool_calls) > task.max_parsed_tool_calls
    ):
        missing.append(f"max_parsed_tool_calls:{task.max_parsed_tool_calls}")

    return ContextLiveTaskResult(
        task_id=task.task_id,
        description=task.description,
        duration_ms=duration_ms,
        passed=passed and not missing and error is None,
        checks_passed=checks_passed,
        total_checks=(
            len(task.checks)
            + task.required_compactions
            + len(task.required_tools)
            + (1 if task.max_parsed_tool_calls is not None else 0)
        ),
        missing_checks=missing,
        compact_count=len(compact_entries),
        parsed_tool_calls=len(parsed_tool_calls),
        tool_names=tool_names,
        final_output=final_output,
        memory_preview=memory_preview,
        transcript_excerpt=transcript_excerpt,
        error=error,
    )


def summarize_run(run: ContextLiveRun) -> dict[str, object]:
    passed_tasks = sum(1 for result in run.task_results if result.passed)
    return {
        "task_count": len(run.task_results),
        "passed_tasks": passed_tasks,
        "pass_rate": f"{passed_tasks}/{len(run.task_results)}",
        "total_duration_ms": run.total_duration_ms,
    }


def render_markdown_report(run: ContextLiveRun) -> str:
    summary = summarize_run(run)
    lines = [
        f"# context live regression report: {run.model}",
        "",
        f"- started_at: {run.started_at}",
        f"- finished_at: {run.finished_at}",
        f"- workspace: `{run.workspace_root}`",
        f"- tasks_file: `{run.tasks_file}`",
        f"- pass_rate: `{summary['pass_rate']}`",
        f"- total_duration_ms: `{summary['total_duration_ms']}`",
        "",
        "| task | pass | duration_ms | compactions | parsed_tool_calls | tools | missing_checks |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for result in run.task_results:
        lines.append(
            f"| `{result.task_id}` | {'yes' if result.passed else 'no'} | {result.duration_ms} | "
            f"{result.compact_count} | {result.parsed_tool_calls} | "
            f"{', '.join(result.tool_names) if result.tool_names else '-'} | "
            f"{', '.join(result.missing_checks) if result.missing_checks else '-'} |"
        )
    lines.append("")
    lines.append("## Task Details")
    lines.append("")
    for result in run.task_results:
        lines.append(f"### {result.task_id}")
        if result.description:
            lines.append(f"- description: {result.description}")
        if result.error:
            lines.append(f"- error: `{result.error}`")
        lines.append("")
        lines.append("#### final_output")
        lines.append("")
        lines.append("```text")
        lines.append(result.final_output.strip() or "(empty)")
        lines.append("```")
        lines.append("")
        lines.append("#### memory_preview")
        lines.append("")
        lines.append("```text")
        lines.append(result.memory_preview.strip() or "(empty)")
        lines.append("```")
        lines.append("")
        lines.append("#### transcript_excerpt")
        lines.append("")
        lines.append("```text")
        lines.append(result.transcript_excerpt.strip() or "(empty)")
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def serialize_run(run: ContextLiveRun) -> dict[str, object]:
    data = asdict(run)
    data["summary"] = summarize_run(run)
    return data


def output_prefix(model: str, *, now: datetime | None = None) -> str:
    now = now or datetime.now()
    slug = model.replace(":", "-").replace("/", "-")
    return f"{now.strftime('%Y-%m-%d_%H%M%S')}_{slug}"
