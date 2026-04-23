import tempfile
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent import AgentConfig, build_system_prompt, extract_fake_tool_calls
from tools import ToolRuntime


class ExtractFakeToolCallsTests(unittest.TestCase):
    def test_extracts_tool_call_from_xml_wrapper(self):
        content = '<tool_call>{"name":"read_file","arguments":{"path":"README.md"}}</tool_call>'
        calls = extract_fake_tool_calls(content, {"read_file"})
        self.assertEqual(calls, [("read_file", {"path": "README.md"})])

    def test_extracts_tool_call_from_json_fence(self):
        content = '```json\n{"name":"grep_text","arguments":{"pattern":"TODO"}}\n```'
        calls = extract_fake_tool_calls(content, {"grep_text"})
        self.assertEqual(calls, [("grep_text", {"pattern": "TODO"})])

    def test_ignores_unknown_tools(self):
        content = '{"name":"unknown_tool","arguments":{"path":"README.md"}}'
        calls = extract_fake_tool_calls(content, {"read_file"})
        self.assertEqual(calls, [])

    def test_build_system_prompt_includes_harness_md(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "HARNESS.md").write_text("Always update CHANGELOG.md", encoding="utf-8")
            config = AgentConfig(
                model="demo",
                base_url="http://localhost:11434/v1",
                api_key="ollama",
                num_ctx=4096,
                max_turns=5,
                workspace_root=workspace,
                auto_approve=True,
                command_timeout=5,
            )
            runtime = ToolRuntime(workspace, auto_approve=True, command_timeout=5)

            prompt = build_system_prompt(config, runtime)

            self.assertIn("项目约定（来自 HARNESS.md）", prompt)
            self.assertIn("Always update CHANGELOG.md", prompt)


if __name__ == "__main__":
    unittest.main()
