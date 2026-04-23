#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent import AgentSession, build_config, parse_args
from benchmark_harness import (
    BenchmarkRun,
    BenchmarkTaskResult,
    benchmark_output_prefix,
    evaluate_output,
    extract_tool_names,
    load_benchmark_tasks,
    render_markdown_report,
    serialize_run,
)
from openai import OpenAI


DEFAULT_TASKS_FILE = PROJECT_ROOT / "benchmarks" / "agent_tasks.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "benchmark-results"


def parse_script_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark jarvis against a task set.")
    parser.add_argument(
        "--models",
        nargs="+",
        required=True,
        help="One or more model names to benchmark.",
    )
    parser.add_argument(
        "--tasks-file",
        default=str(DEFAULT_TASKS_FILE),
        help="JSON task set used for the benchmark run.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory used to write markdown/json reports.",
    )
    parser.add_argument(
        "--cwd",
        default=str(PROJECT_ROOT),
        help="Workspace root for the benchmarked agent session.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override OpenAI-compatible base URL.",
    )
    parser.add_argument(
        "--num-ctx",
        type=int,
        default=None,
        help="Override num_ctx for the benchmark run.",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=4,
        help="Maximum turns allowed per benchmark task.",
    )
    parser.add_argument(
        "--request-timeout",
        type=int,
        default=30,
        help="Timeout in seconds for each model request.",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        default=[],
        help="Only run the specified task id. Can be passed multiple times.",
    )
    parser.add_argument(
        "--show-transcript",
        action="store_true",
        help="Print the full agent transcript while benchmarking.",
    )
    return parser.parse_args(argv)


def latest_assistant_output(messages: list[dict[str, object]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "assistant" and message.get("content"):
            return str(message["content"])
    return ""


def run_single_task(
    *,
    model: str,
    task,
    script_args: argparse.Namespace,
) -> BenchmarkTaskResult:
    config_args = [
        "--cwd",
        script_args.cwd,
        "--model",
        model,
        "--max-turns",
        str(script_args.max_turns),
        "--auto-approve",
        "--repl",
    ]
    if script_args.base_url:
        config_args.extend(["--base-url", script_args.base_url])
    if script_args.num_ctx is not None:
        config_args.extend(["--num-ctx", str(script_args.num_ctx)])

    config = build_config(parse_args(config_args))
    session = AgentSession(config)
    session.client = OpenAI(
        base_url=config.base_url,
        api_key=config.api_key,
        timeout=script_args.request_timeout,
        max_retries=0,
    )

    start = time.perf_counter()
    error: str | None = None
    transcript_buffer = io.StringIO()
    try:
        stream = None if script_args.show_transcript else transcript_buffer
        with contextlib.nullcontext() if stream is None else contextlib.redirect_stdout(stream):
            session.handle_user_turn(task.prompt)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    duration_ms = int((time.perf_counter() - start) * 1000)

    final_output = latest_assistant_output(session.messages)
    passed, missing, checks_passed = evaluate_output(final_output, task.checks)
    tool_summaries = [
        entry.summary for entry in session.activity_log if entry.kind == "tool_call"
    ]
    tool_names = extract_tool_names(tool_summaries)

    return BenchmarkTaskResult(
        task_id=task.task_id,
        description=task.description,
        duration_ms=duration_ms,
        passed=passed and error is None,
        checks_passed=checks_passed,
        total_checks=len(task.checks),
        missing_checks=missing,
        tool_calls=len(tool_names),
        tool_names=tool_names,
        final_output=final_output,
        error=error,
    )


def run_model_benchmark(script_args: argparse.Namespace, model: str) -> BenchmarkRun:
    tasks_file = Path(script_args.tasks_file).expanduser().resolve()
    tasks = load_benchmark_tasks(tasks_file)
    if script_args.task_id:
        allowed = set(script_args.task_id)
        tasks = [task for task in tasks if task.task_id in allowed]
        if not tasks:
            raise ValueError(f"没有匹配到 task id: {sorted(allowed)}")
    started_at = datetime.now().isoformat(timespec="seconds")
    run_start = time.perf_counter()
    results = []
    for index, task in enumerate(tasks, start=1):
        print(f"[{model}] task {index}/{len(tasks)}: {task.task_id}")
        result = run_single_task(model=model, task=task, script_args=script_args)
        status = "pass" if result.passed else "fail"
        print(
            f"[{model}] task {task.task_id} -> {status} ({result.duration_ms} ms, tools={result.tool_calls})"
        )
        results.append(result)
    total_duration_ms = int((time.perf_counter() - run_start) * 1000)
    finished_at = datetime.now().isoformat(timespec="seconds")

    resolved_config = build_config(
        parse_args(
            [
                "--cwd",
                script_args.cwd,
                "--model",
                model,
                "--max-turns",
                str(script_args.max_turns),
                "--auto-approve",
            ]
            + (["--base-url", script_args.base_url] if script_args.base_url else [])
            + (
                ["--num-ctx", str(script_args.num_ctx)]
                if script_args.num_ctx is not None
                else []
            )
        )
    )

    return BenchmarkRun(
        model=model,
        base_url=resolved_config.base_url,
        num_ctx=resolved_config.num_ctx,
        workspace_root=str(Path(script_args.cwd).expanduser().resolve()),
        tasks_file=str(tasks_file),
        started_at=started_at,
        finished_at=finished_at,
        total_duration_ms=total_duration_ms,
        task_results=results,
    )


def write_run_outputs(output_dir: Path, run: BenchmarkRun) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = benchmark_output_prefix(run.model)
    json_path = output_dir / f"{prefix}.json"
    md_path = output_dir / f"{prefix}.md"
    json_path.write_text(
        json.dumps(serialize_run(run), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown_report(run) + "\n", encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    args = parse_script_args(argv)
    output_dir = Path(args.output_dir).expanduser().resolve()
    exit_code = 0
    for model in args.models:
        run = run_model_benchmark(args, model)
        json_path, md_path = write_run_outputs(output_dir, run)
        summary = serialize_run(run)["summary"]
        print(
            "\n".join(
                [
                    f"model: {model}",
                    f"pass_rate: {summary['pass_rate']}",
                    f"average_duration_ms: {summary['average_duration_ms']}",
                    f"total_duration_ms: {summary['total_duration_ms']}",
                    f"json: {json_path}",
                    f"markdown: {md_path}",
                ]
            )
        )
        if summary["passed_tasks"] != summary["task_count"]:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
