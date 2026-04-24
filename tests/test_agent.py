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
    resolve_active_goal,
)
from context_engine import SessionMemory
from performance_trace import ModelRequestTrace, RequestPayloadProfile
from runtime_config import RuntimeConfigSources
from tools import ToolRuntime, infer_tool_profile


class ExtractFakeToolCallsTests(unittest.TestCase):
    def test_extracts_tool_call_from_xml_wrapper(self):
        content = '<tool_call>{"name":"read_file","arguments":{"path":"README.md"}}</tool_call>'
        calls = extract_fake_tool_calls(content, {"read_file"})
        self.assertEqual(calls, [("read_file", {"path": "README.md"})])

    def test_extracts_tool_call_from_json_fence(self):
        content = '```json\n{"name":"grep_text","arguments":{"pattern":"TODO"}}\n```'
        calls = extract_fake_tool_calls(content, {"grep_text"})
        self.assertEqual(calls, [("grep_text", {"pattern": "TODO"})])

    def test_extracts_tool_call_from_function_name_variant(self):
        content = '```json\n{"function_name":"read_file","arguments":{"path":"jarvis.config.json"}}\n```'
        calls = extract_fake_tool_calls(content, {"read_file"})
        self.assertEqual(calls, [("read_file", {"path": "jarvis.config.json"})])

    def test_ignores_unknown_tools(self):
        content = '{"name":"unknown_tool","arguments":{"path":"README.md"}}'
        calls = extract_fake_tool_calls(content, {"read_file"})
        self.assertEqual(calls, [])

    def test_resolve_active_goal_keeps_previous_goal_on_continue(self):
        goal = resolve_active_goal("修复 compact 后的 fake tool call", "继续")
        self.assertEqual(goal, "修复 compact 后的 fake tool call")

    def test_resolve_active_goal_accepts_new_substantive_goal(self):
        goal = resolve_active_goal("修复 compact 后的 fake tool call", "给 compact 增加更稳的摘要")
        self.assertEqual(goal, "给 compact 增加更稳的摘要")

    def test_build_system_prompt_includes_harness_md(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "HARNESS.md").write_text("Always update CHANGELOG.md", encoding="utf-8")
            (workspace / "way-to-claw-code.md").write_text("P1: compact and session memory", encoding="utf-8")
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
            self.assertIn("长期路线图（来自 way-to-claw-code.md）", prompt)
            self.assertIn("compact and session memory", prompt)

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
            session.config = type("Config", (), {"num_ctx": 4096})()
            session.memory = SessionMemory(active_goal="please update tracked.txt")
            session.messages = [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "please update tracked.txt"},
            ]

            summary = AgentSession.summary_report(session, limit=5)

            self.assertIn("本轮摘要", summary)
            self.assertIn("please update tracked.txt", summary)
            self.assertIn("建议 commit message", summary)
            self.assertIn("Context：", summary)

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

    def test_infer_tool_profile_uses_read_only_for_lookup_tasks(self):
        profile = infer_tool_profile("读取 jarvis.config.json，告诉我默认 model")
        self.assertEqual(profile, "read_only")

    def test_infer_tool_profile_keeps_full_for_edit_tasks(self):
        profile = infer_tool_profile("修改 agent.py，给 /perf 加更多输出")
        self.assertEqual(profile, "full")

    def test_run_until_idle_omits_tools_when_no_tool_schemas(self):
        class FakeMessage:
            content = "done"
            tool_calls = None

        class FakeChoice:
            message = FakeMessage()

        class FakeResponse:
            choices = [FakeChoice()]

        class FakeCompletions:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return FakeResponse()

        class FakeChat:
            def __init__(self):
                self.completions = FakeCompletions()

        class FakeClient:
            def __init__(self):
                self.chat = FakeChat()

        session = object.__new__(AgentSession)
        session.config = type(
            "Config",
            (),
            {"max_turns": 1, "model": "demo", "num_ctx": 2048},
        )()
        session.client = FakeClient()
        session.runtime = type("Runtime", (), {"tool_schemas": [], "execute_tool": lambda *args, **kwargs: ""})()
        session.tool_names = set()
        session.messages = [{"role": "user", "content": "hello"}]
        session.activity_log = []
        session.request_traces = []
        session.maybe_auto_compact = lambda: None
        session.log_activity = lambda *args, **kwargs: None

        finished = AgentSession.run_until_idle(session)

        self.assertTrue(finished)
        call = session.client.chat.completions.calls[0]
        self.assertNotIn("tools", call)
        self.assertNotIn("tool_choice", call)
        self.assertEqual(len(session.request_traces), 1)
        self.assertEqual(session.request_traces[0].status, "ok")
        self.assertFalse(session.request_traces[0].payload.tools_enabled)

    def test_performance_report_includes_current_payload_and_recent_requests(self):
        session = object.__new__(AgentSession)
        session.messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "hello"},
        ]
        session.runtime = type(
            "Runtime",
            (),
            {"tool_schemas": [], "active_tool_profile": "read_only"},
        )()
        session.request_traces = [
            ModelRequestTrace(
                turn=1,
                status="ok",
                duration_ms=3210,
                tool_calls=0,
                content_chars=5,
                payload=RequestPayloadProfile(
                    turn=1,
                    total_messages=2,
                    non_system_messages=1,
                    estimated_tokens=42,
                    system_message_chars=5,
                    session_memory_chars=0,
                    tool_schema_count=0,
                    tool_schema_chars=2,
                    tools_enabled=False,
                ),
            )
        ]

        report = AgentSession.performance_report(session, limit=3)

        self.assertIn("性能观察", report)
        self.assertIn("当前工具画像：read_only", report)
        self.assertIn("当前请求载荷", report)
        self.assertIn("最近模型请求", report)
        self.assertIn("3210ms", report)

    def test_add_user_message_switches_to_read_only_tool_profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session = object.__new__(AgentSession)
            session.runtime = ToolRuntime(Path(tmpdir), auto_approve=True, command_timeout=5)
            session.memory = SessionMemory(active_goal="")
            session.messages = []
            session.activity_log = []
            session.log_activity = lambda *args, **kwargs: None
            session.rebuild_messages = lambda conversation=None: None

            AgentSession.add_user_message(session, "读取 jarvis.config.json，告诉我默认 model")

            self.assertEqual(session.runtime.active_tool_profile, "read_only")
            self.assertEqual(
                set(session.runtime.active_tool_names),
                {"read_file", "list_files", "grep_text"},
            )


if __name__ == "__main__":
    unittest.main()
