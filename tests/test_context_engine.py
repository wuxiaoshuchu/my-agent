import sys
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from context_engine import (
    SessionMemory,
    build_context_stats,
    compact_messages,
    render_session_memory,
    should_auto_compact,
)


class ContextEngineTests(unittest.TestCase):
    def test_build_context_stats_counts_turns_and_non_system_messages(self):
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "task 1"},
            {"role": "assistant", "content": "working"},
            {"role": "user", "content": "task 2"},
            {"role": "assistant", "content": "done"},
        ]

        stats = build_context_stats(messages)

        self.assertEqual(stats.total_messages, 5)
        self.assertEqual(stats.non_system_messages, 4)
        self.assertEqual(stats.turn_count, 2)
        self.assertGreater(stats.estimated_tokens, 0)

    def test_compact_messages_keeps_recent_turns_and_preserves_goal(self):
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "先看 README"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "1", "content": "README body"},
            {"role": "user", "content": "再看 agent.py"},
            {"role": "assistant", "content": "我看到了 AgentSession。"},
            {"role": "user", "content": "总结一下当前架构"},
            {"role": "assistant", "content": "现在是单 agent 线性 loop。"},
        ]

        result = compact_messages(
            messages,
            memory=SessionMemory(active_goal="总结一下当前架构"),
            reason="manual",
            now=datetime(2026, 4, 24, 1, 23, 45),
        )

        self.assertTrue(result.compacted)
        self.assertEqual(result.dropped_turns, 1)
        self.assertEqual(len(result.kept_messages), 4)
        self.assertEqual(result.kept_messages[0]["content"], "再看 agent.py")
        self.assertEqual(result.memory.active_goal, "总结一下当前架构")
        self.assertEqual(len(result.memory.compaction_blocks), 1)

        memory_text = render_session_memory(result.memory)
        self.assertIn("当前任务目标", memory_text)
        self.assertIn("先看 README", memory_text)
        self.assertIn("read_file", memory_text)

    def test_should_auto_compact_when_conversation_gets_large(self):
        messages = [{"role": "system", "content": "rules"}]
        for index in range(4):
            messages.append({"role": "user", "content": f"任务 {index} " + ("x" * 2400)})
            messages.append({"role": "assistant", "content": f"结果 {index}"})

        self.assertTrue(should_auto_compact(messages, num_ctx=4096))


if __name__ == "__main__":
    unittest.main()
