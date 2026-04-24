from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from agent import extract_fake_tool_calls, resolve_active_goal
from context_engine import (
    SessionMemory,
    compact_messages,
    render_session_memory,
    should_auto_compact,
)


@dataclass(frozen=True)
class ContextRegressionCase:
    task_id: str
    description: str
    messages: list[dict[str, object]]
    active_goal: str = ""
    follow_up: str = ""
    num_ctx: int = 16384
    force_compact: bool = False
    fake_tool_text: str = ""
    tool_names: list[str] = ()
    expected: dict[str, object] | None = None


@dataclass(frozen=True)
class ContextRegressionCaseResult:
    task_id: str
    description: str
    duration_ms: int
    passed: bool
    checks_passed: int
    total_checks: int
    missing_checks: list[str]
    auto_compact: bool
    compacted: bool
    resolved_goal: str
    dropped_turns: int
    fake_tool_names: list[str]
    memory_preview: str
    kept_preview: str
    error: str | None = None


@dataclass(frozen=True)
class ContextRegressionRun:
    workspace_root: str
    cases_file: str
    started_at: str
    finished_at: str
    total_duration_ms: int
    case_results: list[ContextRegressionCaseResult]


def load_context_regression_cases(path: Path) -> list[ContextRegressionCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} 必须是 JSON array。")

    cases: list[ContextRegressionCase] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError(f"{path} 里的 case 必须是 object。")
        task_id = str(item["id"]).strip()
        description = str(item.get("description", "")).strip()
        messages = item.get("messages", [])
        if not task_id or not isinstance(messages, list):
            raise ValueError(f"{path} 里的 case 必须包含非空 id 和 messages array。")
        cases.append(
            ContextRegressionCase(
                task_id=task_id,
                description=description,
                messages=messages,
                active_goal=str(item.get("active_goal", "")).strip(),
                follow_up=str(item.get("follow_up", "")).strip(),
                num_ctx=int(item.get("num_ctx", 16384)),
                force_compact=bool(item.get("force_compact", False)),
                fake_tool_text=str(item.get("fake_tool_text", "")).strip(),
                tool_names=list(item.get("tool_names", [])),
                expected=dict(item.get("expected", {})),
            )
        )
    return cases


def run_context_regression_case(case: ContextRegressionCase) -> ContextRegressionCaseResult:
    start = perf_counter()
    error: str | None = None
    expected = case.expected or {}
    total_checks = count_expected_checks(expected)
    auto_compact = False
    compacted = False
    resolved_goal = case.active_goal
    dropped_turns = 0
    fake_tool_names: list[str] = []
    memory_preview = ""
    kept_preview = ""

    try:
        resolved_goal = resolve_active_goal(case.active_goal, case.follow_up or case.active_goal)
        auto_compact = should_auto_compact(case.messages, num_ctx=case.num_ctx)
        should_compact = case.force_compact or auto_compact

        if case.fake_tool_text:
            fake_tool_names = [
                name
                for name, _args in extract_fake_tool_calls(
                    case.fake_tool_text,
                    set(case.tool_names),
                )
            ]

        if should_compact:
            compaction = compact_messages(
                case.messages,
                memory=SessionMemory(active_goal=resolved_goal),
                reason="regression",
            )
            compacted = compaction.compacted
            dropped_turns = compaction.dropped_turns
            memory_preview = render_session_memory(compaction.memory)
            kept_preview = render_messages_preview(compaction.kept_messages)
        else:
            memory_preview = render_session_memory(SessionMemory(active_goal=resolved_goal))
            kept_preview = render_messages_preview(case.messages)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    missing_checks = evaluate_context_regression_case(
        expected,
        auto_compact=auto_compact,
        compacted=compacted,
        resolved_goal=resolved_goal,
        dropped_turns=dropped_turns,
        fake_tool_names=fake_tool_names,
        memory_preview=memory_preview,
        kept_preview=kept_preview,
        error=error,
    )
    duration_ms = int((perf_counter() - start) * 1000)
    checks_passed = total_checks - len(missing_checks)
    return ContextRegressionCaseResult(
        task_id=case.task_id,
        description=case.description,
        duration_ms=duration_ms,
        passed=not missing_checks and error is None,
        checks_passed=max(0, checks_passed),
        total_checks=total_checks,
        missing_checks=missing_checks,
        auto_compact=auto_compact,
        compacted=compacted,
        resolved_goal=resolved_goal,
        dropped_turns=dropped_turns,
        fake_tool_names=fake_tool_names,
        memory_preview=memory_preview,
        kept_preview=kept_preview,
        error=error,
    )


def evaluate_context_regression_case(
    expected: dict[str, object],
    *,
    auto_compact: bool,
    compacted: bool,
    resolved_goal: str,
    dropped_turns: int,
    fake_tool_names: list[str],
    memory_preview: str,
    kept_preview: str,
    error: str | None,
) -> list[str]:
    missing: list[str] = []
    if expected.get("auto_compact") is not None and auto_compact != bool(expected["auto_compact"]):
        missing.append(f"auto_compact={expected['auto_compact']}")
    if expected.get("compacted") is not None and compacted != bool(expected["compacted"]):
        missing.append(f"compacted={expected['compacted']}")
    if expected.get("resolved_goal") is not None and resolved_goal != str(expected["resolved_goal"]):
        missing.append(f"resolved_goal={expected['resolved_goal']}")
    if expected.get("dropped_turns") is not None and dropped_turns != int(expected["dropped_turns"]):
        missing.append(f"dropped_turns={expected['dropped_turns']}")
    for needle in expected.get("memory_contains", []):
        if str(needle) not in memory_preview:
            missing.append(f"memory_contains:{needle}")
    for needle in expected.get("memory_not_contains", []):
        if str(needle) in memory_preview:
            missing.append(f"memory_not_contains:{needle}")
    for needle in expected.get("kept_contains", []):
        if str(needle) not in kept_preview:
            missing.append(f"kept_contains:{needle}")
    expected_tools = expected.get("fake_tool_names", [])
    if expected_tools:
        for name in expected_tools:
            if str(name) not in fake_tool_names:
                missing.append(f"fake_tool_names:{name}")
    if expected.get("error") is not None:
        expected_error = str(expected["error"])
        if expected_error == "none" and error is not None:
            missing.append("error:none")
        elif expected_error != "none" and expected_error not in (error or ""):
            missing.append(f"error:{expected_error}")
    return missing


def render_messages_preview(messages: list[dict[str, object]], *, limit: int = 6) -> str:
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role", "unknown"))
        content = str(message.get("content", "")).strip()
        if not content and message.get("tool_calls"):
            content = "[tool_call payload]"
        if not content:
            continue
        lines.append(f"[{role}] {content}")
        if len(lines) >= limit:
            break
    return "\n".join(lines)


def count_expected_checks(expected: dict[str, object]) -> int:
    count = 0
    for key, value in expected.items():
        if key in {"memory_contains", "memory_not_contains", "kept_contains", "fake_tool_names"}:
            count += len(value)
        else:
            count += 1
    return count


def summarize_context_regression_run(run: ContextRegressionRun) -> dict[str, object]:
    passed_cases = sum(1 for result in run.case_results if result.passed)
    auto_cases = sum(1 for result in run.case_results if result.auto_compact)
    compacted_cases = sum(1 for result in run.case_results if result.compacted)
    return {
        "case_count": len(run.case_results),
        "passed_cases": passed_cases,
        "pass_rate": f"{passed_cases}/{len(run.case_results)}",
        "auto_compact_cases": auto_cases,
        "compacted_cases": compacted_cases,
        "total_duration_ms": run.total_duration_ms,
    }


def render_markdown_report(run: ContextRegressionRun) -> str:
    summary = summarize_context_regression_run(run)
    lines = [
        "# context regression report",
        "",
        f"- started_at: {run.started_at}",
        f"- finished_at: {run.finished_at}",
        f"- workspace: `{run.workspace_root}`",
        f"- cases_file: `{run.cases_file}`",
        f"- pass_rate: `{summary['pass_rate']}`",
        f"- auto_compact_cases: `{summary['auto_compact_cases']}`",
        f"- compacted_cases: `{summary['compacted_cases']}`",
        f"- total_duration_ms: `{summary['total_duration_ms']}`",
        "",
        "| case | pass | duration_ms | auto | compacted | missing_checks |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for result in run.case_results:
        lines.append(
            f"| `{result.task_id}` | {'yes' if result.passed else 'no'} | {result.duration_ms} | "
            f"{'yes' if result.auto_compact else 'no'} | {'yes' if result.compacted else 'no'} | "
            f"{', '.join(result.missing_checks) if result.missing_checks else '-'} |"
        )

    lines.append("")
    lines.append("## Case Details")
    lines.append("")
    for result in run.case_results:
        lines.append(f"### {result.task_id}")
        if result.description:
            lines.append(f"- description: {result.description}")
        lines.append(f"- resolved_goal: `{result.resolved_goal}`")
        lines.append(f"- fake_tool_names: `{', '.join(result.fake_tool_names) or '-'}`")
        if result.error:
            lines.append(f"- error: `{result.error}`")
        lines.append("")
        lines.append("#### memory_preview")
        lines.append("")
        lines.append("```text")
        lines.append(result.memory_preview or "(empty)")
        lines.append("```")
        lines.append("")
        lines.append("#### kept_preview")
        lines.append("")
        lines.append("```text")
        lines.append(result.kept_preview or "(empty)")
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def serialize_run(run: ContextRegressionRun) -> dict[str, object]:
    data = asdict(run)
    data["summary"] = summarize_context_regression_run(run)
    return data


def output_prefix(*, now: datetime | None = None) -> str:
    now = now or datetime.now()
    return now.strftime("%Y-%m-%d_%H%M%S")
