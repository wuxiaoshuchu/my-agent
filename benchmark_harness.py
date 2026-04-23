from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    prompt: str
    checks: list[str]
    description: str = ""


@dataclass(frozen=True)
class BenchmarkTaskResult:
    task_id: str
    description: str
    duration_ms: int
    passed: bool
    checks_passed: int
    total_checks: int
    missing_checks: list[str]
    tool_calls: int
    tool_names: list[str]
    final_output: str
    error: str | None = None


@dataclass(frozen=True)
class BenchmarkRun:
    model: str
    base_url: str
    num_ctx: int
    workspace_root: str
    tasks_file: str
    started_at: str
    finished_at: str
    total_duration_ms: int
    task_results: list[BenchmarkTaskResult]


def load_benchmark_tasks(path: Path) -> list[BenchmarkTask]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} 必须是 JSON array。")

    tasks: list[BenchmarkTask] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError(f"{path} 里的任务必须是 object。")
        task_id = str(item["id"]).strip()
        prompt = str(item["prompt"]).strip()
        checks = [str(check).strip() for check in item.get("checks", []) if str(check).strip()]
        description = str(item.get("description", "")).strip()
        if not task_id or not prompt:
            raise ValueError(f"{path} 里的任务必须包含非空 id 和 prompt。")
        tasks.append(
            BenchmarkTask(
                task_id=task_id,
                prompt=prompt,
                checks=checks,
                description=description,
            )
        )
    return tasks


def evaluate_output(text: str, checks: list[str]) -> tuple[bool, list[str], int]:
    normalized = text.lower()
    missing = [check for check in checks if check.lower() not in normalized]
    return not missing, missing, len(checks) - len(missing)


def extract_tool_names(activity_summaries: list[str]) -> list[str]:
    names: list[str] = []
    for summary in activity_summaries:
        match = re.match(r"^→\s*([a-zA-Z0-9_]+)\(", summary)
        if match:
            names.append(match.group(1))
    return names


def summarize_run(run: BenchmarkRun) -> dict[str, object]:
    passed_tasks = sum(1 for result in run.task_results if result.passed)
    average_duration_ms = int(
        sum(result.duration_ms for result in run.task_results) / max(len(run.task_results), 1)
    )
    return {
        "model": run.model,
        "task_count": len(run.task_results),
        "passed_tasks": passed_tasks,
        "pass_rate": f"{passed_tasks}/{len(run.task_results)}",
        "average_duration_ms": average_duration_ms,
        "total_duration_ms": run.total_duration_ms,
    }


def render_markdown_report(run: BenchmarkRun) -> str:
    summary = summarize_run(run)
    lines = [
        f"# benchmark report: {run.model}",
        "",
        f"- started_at: {run.started_at}",
        f"- finished_at: {run.finished_at}",
        f"- workspace: `{run.workspace_root}`",
        f"- tasks_file: `{run.tasks_file}`",
        f"- num_ctx: `{run.num_ctx}`",
        f"- pass_rate: `{summary['pass_rate']}`",
        f"- average_duration_ms: `{summary['average_duration_ms']}`",
        f"- total_duration_ms: `{run.total_duration_ms}`",
        "",
        "| task | pass | duration_ms | tool_calls | tools | missing_checks |",
        "|---|---:|---:|---:|---|---|",
    ]

    for result in run.task_results:
        passed = "yes" if result.passed else "no"
        tools = ", ".join(result.tool_names) if result.tool_names else "-"
        missing = ", ".join(result.missing_checks) if result.missing_checks else "-"
        lines.append(
            f"| `{result.task_id}` | {passed} | {result.duration_ms} | {result.tool_calls} | {tools} | {missing} |"
        )

    lines.append("")
    lines.append("## Final Outputs")
    lines.append("")
    for result in run.task_results:
        lines.append(f"### {result.task_id}")
        if result.description:
            lines.append(f"- description: {result.description}")
        if result.error:
            lines.append(f"- error: `{result.error}`")
        lines.append("")
        lines.append("```text")
        lines.append(result.final_output.strip() or "(empty)")
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def slugify_model_name(model: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", model).strip("-").lower()
    return slug or "model"


def benchmark_output_prefix(model: str, *, now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"{now.strftime('%Y-%m-%d_%H%M%S')}_{slugify_model_name(model)}"


def serialize_run(run: BenchmarkRun) -> dict[str, object]:
    data = asdict(run)
    data["summary"] = summarize_run(run)
    return data
