import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime_diagnostics import (
    DiagnosticCaseResult,
    DiagnosticRun,
    infer_root_cause,
    render_markdown_report,
    summarize_diagnostic_run,
)


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_infer_root_cause_detects_small_model_tool_latency_pattern(self):
        run = DiagnosticRun(
            model="qwen2.5-coder:7b",
            workspace_root="/tmp/demo",
            started_at="2026-04-23T10:00:00",
            finished_at="2026-04-23T10:01:00",
            short_timeout_s=20,
            long_timeout_s=120,
            results=[
                DiagnosticCaseResult("direct_chat_minimal", "ok", 6000, "HTTP request completed"),
                DiagnosticCaseResult("openai_minimal", "ok", 400, "content='OK'"),
                DiagnosticCaseResult("openai_agent_quick_prompt", "ok", 3500, "content='OK'"),
                DiagnosticCaseResult(
                    "agent_runtime_defaults_short",
                    "timeout",
                    20000,
                    "APITimeoutError: Request timed out.",
                ),
                DiagnosticCaseResult(
                    "agent_runtime_defaults_long",
                    "ok",
                    62000,
                    "completed with 1 tool calls",
                ),
            ],
        )

        root_cause = infer_root_cause(run)

        self.assertIn("20 秒超时", root_cause)
        self.assertIn("jarvis", root_cause)

    def test_render_markdown_report_includes_root_cause_and_table(self):
        run = DiagnosticRun(
            model="qwen2.5-coder:7b",
            workspace_root="/tmp/demo",
            started_at="2026-04-23T10:00:00",
            finished_at="2026-04-23T10:01:00",
            short_timeout_s=20,
            long_timeout_s=120,
            results=[
                DiagnosticCaseResult("api_version", "ok", 10, "HTTP request completed"),
                DiagnosticCaseResult("agent_runtime_defaults_short", "timeout", 20000, "timeout"),
            ],
        )

        markdown = render_markdown_report(run)
        summary = summarize_diagnostic_run(run)

        self.assertIn("# runtime diagnostics: qwen2.5-coder:7b", markdown)
        self.assertIn("## Root Cause", markdown)
        self.assertEqual(summary["timeout_cases"], 1)


if __name__ == "__main__":
    unittest.main()
