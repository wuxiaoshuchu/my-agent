#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from context_live_regression import (
    ContextLiveRun,
    load_context_live_tasks,
    output_prefix,
    render_markdown_report,
    run_context_live_task,
    serialize_run,
    summarize_run,
)


DEFAULT_TASKS_FILE = PROJECT_ROOT / "benchmarks" / "context_live_tasks.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "context-live-results"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live-model regressions for jarvis context engine.")
    parser.add_argument("--model", required=True, help="Model name to use for the live regression.")
    parser.add_argument(
        "--tasks-file",
        default=str(DEFAULT_TASKS_FILE),
        help="JSON task set used for the live regression run.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory used to write markdown/json reports.",
    )
    parser.add_argument(
        "--cwd",
        default=str(PROJECT_ROOT),
        help="Workspace root for the live regression session.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override OpenAI-compatible base URL.",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        default=[],
        help="Only run the specified task id. Can be passed multiple times.",
    )
    return parser.parse_args(argv)


def write_outputs(output_dir: Path, run: ContextLiveRun) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_prefix(run.model)
    json_path = output_dir / f"{prefix}.json"
    md_path = output_dir / f"{prefix}.md"
    json_path.write_text(
        json.dumps(serialize_run(run), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown_report(run) + "\n", encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tasks_file = Path(args.tasks_file).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    workspace_root = Path(args.cwd).expanduser().resolve()
    tasks = load_context_live_tasks(tasks_file)
    if args.task_id:
        allowed = set(args.task_id)
        tasks = [task for task in tasks if task.task_id in allowed]
        if not tasks:
            raise ValueError(f"没有匹配到 task id: {sorted(allowed)}")

    started_at = datetime.now().isoformat(timespec="seconds")
    run_start = perf_counter()
    results = []
    for index, task in enumerate(tasks, start=1):
        print(f"[context-live:{args.model}] task {index}/{len(tasks)}: {task.task_id}", flush=True)
        result = run_context_live_task(
            workspace_root=workspace_root,
            model=args.model,
            base_url=args.base_url,
            task=task,
        )
        status = "pass" if result.passed else "fail"
        print(
            f"[context-live:{args.model}] task {task.task_id} -> {status} "
            f"({result.duration_ms} ms, compactions={result.compact_count}, parsed={result.parsed_tool_calls})",
            flush=True,
        )
        results.append(result)

    run = ContextLiveRun(
        model=args.model,
        workspace_root=str(workspace_root),
        tasks_file=str(tasks_file),
        started_at=started_at,
        finished_at=datetime.now().isoformat(timespec="seconds"),
        total_duration_ms=int((perf_counter() - run_start) * 1000),
        task_results=results,
    )
    json_path, md_path = write_outputs(output_dir, run)
    summary = summarize_run(run)
    print(
        "\n".join(
            [
                f"model: {args.model}",
                f"pass_rate: {summary['pass_rate']}",
                f"total_duration_ms: {summary['total_duration_ms']}",
                f"json: {json_path}",
                f"markdown: {md_path}",
            ]
        ),
        flush=True,
    )
    return 0 if summary["passed_tasks"] == summary["task_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
