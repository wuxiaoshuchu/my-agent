from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime


@dataclass(frozen=True)
class DiagnosticCaseResult:
    case_id: str
    status: str
    duration_ms: int
    summary: str
    detail: str = ""


@dataclass(frozen=True)
class DiagnosticRun:
    model: str
    workspace_root: str
    started_at: str
    finished_at: str
    short_timeout_s: int
    long_timeout_s: int
    results: list[DiagnosticCaseResult]


def summarize_diagnostic_run(run: DiagnosticRun) -> dict[str, object]:
    ok_cases = sum(1 for result in run.results if result.status == "ok")
    timeout_cases = sum(1 for result in run.results if result.status == "timeout")
    error_cases = sum(
        1 for result in run.results if result.status not in {"ok", "timeout"}
    )
    total_duration_ms = sum(result.duration_ms for result in run.results)
    return {
        "case_count": len(run.results),
        "ok_cases": ok_cases,
        "timeout_cases": timeout_cases,
        "error_cases": error_cases,
        "total_duration_ms": total_duration_ms,
    }


def infer_root_cause(run: DiagnosticRun) -> str:
    by_id = {result.case_id: result for result in run.results}
    direct = by_id.get("direct_chat_minimal")
    openai_minimal = by_id.get("openai_minimal")
    quick_agent = by_id.get("openai_agent_quick_prompt")
    short_case = by_id.get("agent_runtime_defaults_short")
    long_case = by_id.get("agent_runtime_defaults_long")

    if (
        direct
        and direct.status == "ok"
        and direct.duration_ms >= 60_000
        and openai_minimal
        and openai_minimal.status == "ok"
        and quick_agent
        and quick_agent.status == "timeout"
        and long_case
        and long_case.status == "timeout"
    ):
        return (
            f"当前模型 `{run.model}` 在这台机器上的冷启动和首 token 成本已经过高："
            "最小直连请求都接近 1 分钟以上，带完整 `jarvis` prompt + tools 的请求"
            "在 120 秒下仍会超时。对这台 `M1 + 16GB` 机器来说，它暂时不适合作为"
            "默认本地模型。"
        )

    if (
        direct
        and direct.status == "ok"
        and direct.duration_ms >= 30_000
        and openai_minimal
        and openai_minimal.status == "ok"
        and quick_agent
        and quick_agent.status == "timeout"
        and short_case
        and short_case.status == "timeout"
        and long_case
        and long_case.status == "ok"
    ):
        return (
            f"当前模型 `{run.model}` 在这台机器上可以跑通真实 agent 任务，但冷启动和首轮工具决策"
            "明显偏慢：最小直连请求已经到几十秒量级，真实 agent 任务整轮时间也会远超"
            " `20s` benchmark 的阈值。它更适合作为实验模型，不适合作为默认本地模型。"
        )

    if (
        direct
        and direct.status == "ok"
        and openai_minimal
        and openai_minimal.status == "ok"
        and short_case
        and short_case.status == "timeout"
        and long_case
        and long_case.status == "ok"
    ):
        return (
            "Ollama 服务本身是健康的，瓶颈主要出现在 `jarvis` 风格 prompt + tools 下的"
            "首轮工具决策延迟；当前 20 秒超时对当前模型太紧。"
        )

    if direct and direct.status != "ok":
        return "Ollama 的最小直连请求都不稳定，优先排查本地运行时或服务状态。"

    if short_case and short_case.status == "timeout":
        return "真实 agent 任务在当前超时阈值下会超时，优先检查模型延迟和超时设置。"

    return "还没有足够证据锁定单一根因，需要继续补更多诊断样本。"


def render_markdown_report(run: DiagnosticRun) -> str:
    summary = summarize_diagnostic_run(run)
    root_cause = infer_root_cause(run)
    lines = [
        f"# runtime diagnostics: {run.model}",
        "",
        f"- started_at: `{run.started_at}`",
        f"- finished_at: `{run.finished_at}`",
        f"- workspace: `{run.workspace_root}`",
        f"- short_timeout_s: `{run.short_timeout_s}`",
        f"- long_timeout_s: `{run.long_timeout_s}`",
        f"- case_count: `{summary['case_count']}`",
        f"- timeout_cases: `{summary['timeout_cases']}`",
        f"- total_duration_ms: `{summary['total_duration_ms']}`",
        "",
        "## Root Cause",
        "",
        root_cause,
        "",
        "## Cases",
        "",
        "| case | status | duration_ms | summary |",
        "|---|---|---:|---|",
    ]
    for result in run.results:
        lines.append(
            f"| `{result.case_id}` | {result.status} | {result.duration_ms} | {result.summary} |"
        )

    lines.append("")
    lines.append("## Details")
    lines.append("")
    for result in run.results:
        lines.append(f"### {result.case_id}")
        lines.append(f"- status: `{result.status}`")
        lines.append(f"- duration_ms: `{result.duration_ms}`")
        lines.append(f"- summary: {result.summary}")
        if result.detail:
            lines.append("")
            lines.append("```text")
            lines.append(result.detail.strip())
            lines.append("```")
            lines.append("")
        else:
            lines.append("")
    return "\n".join(lines)


def diagnostics_output_prefix(model: str, *, now: datetime | None = None) -> str:
    now = now or datetime.now()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", model).strip("-").lower() or "model"
    return f"{now.strftime('%Y-%m-%d_%H%M%S')}_{slug}"


def serialize_run(run: DiagnosticRun) -> dict[str, object]:
    payload = asdict(run)
    payload["summary"] = summarize_diagnostic_run(run)
    payload["root_cause"] = infer_root_cause(run)
    return payload


def pretty_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
