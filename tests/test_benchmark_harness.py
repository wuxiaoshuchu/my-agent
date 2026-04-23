import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmark_harness import (
    BenchmarkRun,
    BenchmarkTaskResult,
    evaluate_output,
    extract_tool_names,
    load_benchmark_tasks,
    render_markdown_report,
    summarize_run,
)


class BenchmarkHarnessTests(unittest.TestCase):
    def test_evaluate_output_returns_missing_checks(self):
        passed, missing, checks_passed = evaluate_output(
            "model qwen2.5-coder:7b num_ctx 16384",
            ["qwen2.5-coder:7b", "16384", "localhost"],
        )

        self.assertFalse(passed)
        self.assertEqual(missing, ["localhost"])
        self.assertEqual(checks_passed, 2)

    def test_extract_tool_names_parses_activity_summaries(self):
        names = extract_tool_names(
            [
                '→ read_file({"path":"README.md"})',
                '→ grep_text({"pattern":"model"})',
            ]
        )
        self.assertEqual(names, ["read_file", "grep_text"])

    def test_load_benchmark_tasks_reads_json_array(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.json"
            path.write_text(
                '[{"id":"demo","prompt":"hello","checks":["hello"]}]',
                encoding="utf-8",
            )

            tasks = load_benchmark_tasks(path)

            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].task_id, "demo")

    def test_render_markdown_report_includes_summary_and_outputs(self):
        run = BenchmarkRun(
            model="qwen2.5-coder:7b",
            base_url="http://localhost:11434/v1",
            num_ctx=16384,
            workspace_root="/tmp/demo",
            tasks_file="/tmp/demo/tasks.json",
            started_at="2026-04-23T10:00:00",
            finished_at="2026-04-23T10:00:09",
            total_duration_ms=9000,
            task_results=[
                BenchmarkTaskResult(
                    task_id="demo",
                    description="demo task",
                    duration_ms=9000,
                    passed=True,
                    checks_passed=1,
                    total_checks=1,
                    missing_checks=[],
                    tool_calls=2,
                    tool_names=["read_file", "grep_text"],
                    final_output="hello world",
                )
            ],
        )

        markdown = render_markdown_report(run)
        summary = summarize_run(run)

        self.assertIn("# benchmark report: qwen2.5-coder:7b", markdown)
        self.assertIn("hello world", markdown)
        self.assertEqual(summary["pass_rate"], "1/1")


if __name__ == "__main__":
    unittest.main()
