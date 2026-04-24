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

from context_regression_harness import (
    ContextRegressionRun,
    load_context_regression_cases,
    output_prefix,
    render_markdown_report,
    run_context_regression_case,
    serialize_run,
    summarize_context_regression_run,
)


DEFAULT_CASES_FILE = PROJECT_ROOT / "benchmarks" / "context_regression_cases.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "context-regression-results"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic regressions for jarvis context engine.")
    parser.add_argument(
        "--cases-file",
        default=str(DEFAULT_CASES_FILE),
        help="JSON case set used for the regression run.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory used to write markdown/json reports.",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        default=[],
        help="Only run the specified task id. Can be passed multiple times.",
    )
    parser.add_argument(
        "--cwd",
        default=str(PROJECT_ROOT),
        help="Workspace root recorded in the report.",
    )
    return parser.parse_args(argv)


def write_outputs(output_dir: Path, run: ContextRegressionRun) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_prefix()
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
    cases_file = Path(args.cases_file).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    cases = load_context_regression_cases(cases_file)
    if args.task_id:
        allowed = set(args.task_id)
        cases = [case for case in cases if case.task_id in allowed]
        if not cases:
            raise ValueError(f"没有匹配到 task id: {sorted(allowed)}")

    started_at = datetime.now().isoformat(timespec="seconds")
    run_start = perf_counter()
    results = []
    for index, case in enumerate(cases, start=1):
        print(f"[context] case {index}/{len(cases)}: {case.task_id}")
        result = run_context_regression_case(case)
        status = "pass" if result.passed else "fail"
        print(
            f"[context] case {case.task_id} -> {status} "
            f"({result.duration_ms} ms, auto={result.auto_compact}, compacted={result.compacted})"
        )
        results.append(result)

    run = ContextRegressionRun(
        workspace_root=str(Path(args.cwd).expanduser().resolve()),
        cases_file=str(cases_file),
        started_at=started_at,
        finished_at=datetime.now().isoformat(timespec="seconds"),
        total_duration_ms=int((perf_counter() - run_start) * 1000),
        case_results=results,
    )
    json_path, md_path = write_outputs(output_dir, run)
    summary = summarize_context_regression_run(run)
    print(
        "\n".join(
            [
                f"pass_rate: {summary['pass_rate']}",
                f"auto_compact_cases: {summary['auto_compact_cases']}",
                f"compacted_cases: {summary['compacted_cases']}",
                f"total_duration_ms: {summary['total_duration_ms']}",
                f"json: {json_path}",
                f"markdown: {md_path}",
            ]
        )
    )
    return 0 if summary["passed_cases"] == summary["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
