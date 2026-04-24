import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from performance_trace import (
    ModelRequestTrace,
    ToolBatchTrace,
    ToolExecutionTrace,
    build_request_payload_profile,
    render_payload_profile,
    summarize_payload_profile,
    summarize_tool_batch_trace,
    summarize_request_trace,
    summarize_tool_trace,
)


class PerformanceTraceTests(unittest.TestCase):
    def test_build_request_payload_profile_counts_system_and_tools(self):
        profile = build_request_payload_profile(
            [
                {"role": "system", "content": "rules"},
                {"role": "system", "content": "## Session Memory\n- 当前任务目标：读取配置"},
                {"role": "user", "content": "读取 jarvis.config.json"},
            ],
            [
                {
                    "type": "function",
                    "function": {"name": "read_file", "description": "读取文件"},
                }
            ],
            turn=1,
        )

        self.assertEqual(profile.turn, 1)
        self.assertEqual(profile.prompt_profile, "full")
        self.assertEqual(profile.total_messages, 3)
        self.assertEqual(profile.non_system_messages, 1)
        self.assertTrue(profile.tools_enabled)
        self.assertGreater(profile.system_message_chars, 0)
        self.assertGreater(profile.session_memory_chars, 0)
        self.assertGreater(profile.tool_schema_chars, 0)

    def test_render_and_summaries_include_payload_details(self):
        profile = build_request_payload_profile(
            [{"role": "user", "content": "continue"}],
            [],
            turn=2,
        )
        trace = ModelRequestTrace(
            turn=2,
            status="timeout",
            duration_ms=20000,
            tool_calls=0,
            content_chars=0,
            payload=profile,
            error="APITimeoutError: Request timed out.",
        )

        payload_summary = summarize_payload_profile(profile)
        trace_summary = summarize_request_trace(trace)
        rendered = render_payload_profile(profile)

        self.assertIn("turn=2", payload_summary)
        self.assertIn("prompt=full", payload_summary)
        self.assertIn("tools=off", payload_summary)
        self.assertIn("timeout 20000ms", trace_summary)
        self.assertIn("APITimeoutError", trace_summary)
        self.assertIn("- prompt_profile: full", rendered)
        self.assertIn("- tools_enabled: no", rendered)

    def test_summarize_tool_trace_includes_status_and_mode(self):
        trace = ToolExecutionTrace(
            tool_name="grep_text",
            category="discovery",
            status="ok",
            duration_ms=87,
            output_chars=240,
            read_only=True,
            needs_approval=False,
        )

        summary = summarize_tool_trace(trace)

        self.assertIn("grep_text [discovery] ok 87ms", summary)
        self.assertIn("output_chars=240", summary)
        self.assertIn("read-only", summary)
        self.assertIn("no-approval", summary)

    def test_summarize_tool_batch_trace_includes_batch_shape(self):
        trace = ToolBatchTrace(
            mode="read_only_batch",
            tool_names=("read_file", "grep_text"),
            tool_count=2,
            read_only_count=2,
            mutating_count=0,
            duration_ms=93,
            total_output_chars=512,
            error_count=0,
            denied_count=0,
        )

        summary = summarize_tool_batch_trace(trace)

        self.assertIn("read_only_batch", summary)
        self.assertIn("tools=2", summary)
        self.assertIn("output_chars=512", summary)
        self.assertIn("[read_file, grep_text]", summary)


if __name__ == "__main__":
    unittest.main()
