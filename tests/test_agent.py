import tempfile
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent import (
    ActivityEntry,
    AgentConfig,
    AgentSession,
    build_system_prompt,
    build_config,
    extract_fake_tool_calls,
    parse_args,
)
from runtime_config import RuntimeConfigSources
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
                workspace_config_path=workspace / "jarvis.config.json",
                runtime_sources=RuntimeConfigSources(
                    model="default",
                    base_url="default",
                    num_ctx="default",
                ),
            )
            runtime = ToolRuntime(workspace, auto_approve=True, command_timeout=5)

            prompt = build_system_prompt(config, runtime)

            self.assertIn("项目约定（来自 HARNESS.md）", prompt)
            self.assertIn("Always update CHANGELOG.md", prompt)

    def test_summary_report_includes_recent_activity_and_commit_hint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "tracked.txt").write_text("v1\n", encoding="utf-8")

            import subprocess

            subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=workspace,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=workspace,
                check=True,
                capture_output=True,
            )
            subprocess.run(["git", "add", "tracked.txt"], cwd=workspace, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=workspace, check=True, capture_output=True)
            (workspace / "tracked.txt").write_text("v2\n", encoding="utf-8")

            session = object.__new__(AgentSession)
            from agent import WorkspaceInspector

            session.inspector = WorkspaceInspector(workspace)
            session.activity_log = [
                ActivityEntry(timestamp="10:00:00", kind="user", summary="please update tracked.txt")
            ]

            summary = AgentSession.summary_report(session, limit=5)

            self.assertIn("本轮摘要", summary)
            self.assertIn("please update tracked.txt", summary)
            self.assertIn("建议 commit message", summary)

    def test_build_config_prefers_workspace_runtime_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "jarvis.config.json").write_text(
                '{\n  "model": "qwen2.5-coder:14b",\n  "base_url": "http://localhost:11434/v1",\n  "num_ctx": 24576\n}\n',
                encoding="utf-8",
            )

            args = parse_args(["--cwd", str(workspace)])
            config = build_config(args)

            self.assertEqual(config.model, "qwen2.5-coder:14b")
            self.assertEqual(config.num_ctx, 24576)
            self.assertEqual(config.runtime_sources.model, "workspace:jarvis.config.json")

    def test_build_config_allows_cli_override_over_workspace_runtime_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "jarvis.config.json").write_text(
                '{\n  "model": "qwen2.5-coder:14b"\n}\n',
                encoding="utf-8",
            )

            args = parse_args(
                ["--cwd", str(workspace), "--model", "deepseek-coder-v2:16b"]
            )
            config = build_config(args)

            self.assertEqual(config.model, "deepseek-coder-v2:16b")
            self.assertEqual(config.runtime_sources.model, "cli")


if __name__ == "__main__":
    unittest.main()
