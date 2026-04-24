import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from context_live_regression import (
    ContextLiveRun,
    ContextLiveTask,
    build_prefill_messages,
    load_context_live_tasks,
    render_markdown_report,
    summarize_run,
)


class ContextLiveRegressionTests(unittest.TestCase):
    def test_load_context_live_tasks_reads_json_array(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.json"
            path.write_text(
                '[{"id":"demo","description":"demo","active_goal":"read config","prompt":"continue","checks":["config"],"use_tools":false}]',
                encoding="utf-8",
            )

            tasks = load_context_live_tasks(path)

            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].task_id, "demo")
            self.assertFalse(tasks[0].use_tools)

    def test_build_prefill_messages_creates_user_assistant_pairs(self):
        task = ContextLiveTask(
            task_id="demo",
            description="demo",
            active_goal="读取配置",
            prompt="继续",
            checks=["配置"],
            prefill_turns=2,
            prefill_chunk="不要丢任务主线。",
            prefill_repeat=3,
        )

        messages = build_prefill_messages(task)

        self.assertEqual(len(messages), 4)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[1]["role"], "assistant")
        self.assertIn("读取配置", messages[0]["content"])

    def test_render_markdown_report_includes_live_columns(self):
        run = ContextLiveRun(
            model="qwen2.5-coder:7b",
            workspace_root="/tmp/demo",
            tasks_file="/tmp/demo/tasks.json",
            started_at="2026-04-24T10:00:00",
            finished_at="2026-04-24T10:00:05",
            total_duration_ms=5000,
            task_results=[],
        )

        markdown = render_markdown_report(run)
        summary = summarize_run(run)

        self.assertIn("# context live regression report: qwen2.5-coder:7b", markdown)
        self.assertEqual(summary["pass_rate"], "0/0")


if __name__ == "__main__":
    unittest.main()
