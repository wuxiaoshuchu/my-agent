import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from context_regression_harness import (
    ContextRegressionCase,
    ContextRegressionRun,
    count_expected_checks,
    load_context_regression_cases,
    render_markdown_report,
    run_context_regression_case,
    summarize_context_regression_run,
)


class ContextRegressionHarnessTests(unittest.TestCase):
    def test_load_context_regression_cases_reads_json_array(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cases.json"
            path.write_text(
                '[{"id":"demo","description":"demo","messages":[{"role":"user","content":"hi"}]}]',
                encoding="utf-8",
            )

            cases = load_context_regression_cases(path)

            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0].task_id, "demo")

    def test_run_context_regression_case_handles_fake_tool_call_and_goal_resolution(self):
        case = ContextRegressionCase(
            task_id="demo",
            description="demo",
            active_goal="修 fake tool call",
            follow_up="继续",
            force_compact=True,
            tool_names=["read_file"],
            fake_tool_text='```json\n{"function_name":"read_file","arguments":{"path":"jarvis.config.json"}}\n```',
            messages=[
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "读取 jarvis.config.json"},
                {"role": "assistant", "content": '```json\n{"function_name":"read_file","arguments":{"path":"jarvis.config.json"}}\n```'},
                {"role": "user", "content": "继续"},
                {"role": "assistant", "content": "我继续处理。"},
                {"role": "user", "content": "总结一下"},
                {"role": "assistant", "content": "默认模型还是 qwen2.5-coder:7b。"},
            ],
            expected={
                "compacted": True,
                "resolved_goal": "修 fake tool call",
                "fake_tool_names": ["read_file"],
                "memory_not_contains": ["function_name"],
                "error": "none",
            },
        )

        result = run_context_regression_case(case)

        self.assertTrue(result.passed)
        self.assertEqual(result.resolved_goal, "修 fake tool call")
        self.assertEqual(result.fake_tool_names, ["read_file"])

    def test_count_expected_checks_counts_list_like_assertions(self):
        count = count_expected_checks(
            {
                "compacted": True,
                "memory_contains": ["foo", "bar"],
                "memory_not_contains": ["baz"],
                "fake_tool_names": ["read_file"],
            }
        )

        self.assertEqual(count, 5)

    def test_render_markdown_report_includes_case_details(self):
        run = ContextRegressionRun(
            workspace_root="/tmp/demo",
            cases_file="/tmp/demo/cases.json",
            started_at="2026-04-24T03:00:00",
            finished_at="2026-04-24T03:00:01",
            total_duration_ms=12,
            case_results=[
                run_context_regression_case(
                    ContextRegressionCase(
                        task_id="demo",
                        description="demo",
                        active_goal="修 fake tool call",
                        messages=[{"role": "system", "content": "rules"}],
                        expected={"compacted": False},
                    )
                )
            ],
        )

        markdown = render_markdown_report(run)
        summary = summarize_context_regression_run(run)

        self.assertIn("# context regression report", markdown)
        self.assertIn("#### memory_preview", markdown)
        self.assertEqual(summary["pass_rate"], "1/1")


if __name__ == "__main__":
    unittest.main()
