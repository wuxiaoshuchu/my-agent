from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import ToolRuntime


class CapturingToolRuntime(ToolRuntime):
    def __init__(self, workspace_root, *, allow: bool = True):
        super().__init__(workspace_root, auto_approve=False, command_timeout=5)
        self.allow = allow
        self.last_confirmation: tuple[str, str, str | None, str] | None = None
        self.last_patch_plan: tuple[str, str, str, bool] | None = None
        self.patch_mode = "apply_all"
        self.hunk_actions: list[str] = []
        self.hunk_reviews: list[tuple[int, int, str, str]] = []

    def _confirm(
        self,
        action: str,
        preview: str,
        *,
        full_preview: str | None = None,
        accept_label: str = "继续",
    ) -> bool:
        self.last_confirmation = (action, preview, full_preview, accept_label)
        return self.allow

    def _choose_patch_apply_mode(
        self,
        action: str,
        preview: str,
        *,
        full_preview: str,
        allow_hunk_review: bool,
    ) -> str:
        self.last_patch_plan = (action, preview, full_preview, allow_hunk_review)
        if not self.allow:
            return "deny"
        return self.patch_mode

    def _choose_patch_hunk_action(
        self,
        rel_path: str,
        hunk_index: int,
        total_hunks: int,
        preview: str,
        *,
        full_preview: str,
    ) -> str:
        self.hunk_reviews.append((hunk_index, total_hunks, preview, full_preview))
        if self.hunk_actions:
            return self.hunk_actions.pop(0)
        return "apply" if self.allow else "skip"


class ToolRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmpdir.name)
        self.runtime = ToolRuntime(self.workspace, auto_approve=True, command_timeout=5)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_write_and_read_file_inside_workspace(self):
        result = self.runtime.write_file("notes/hello.txt", "hi")
        self.assertIn("OK:", result)
        self.assertIn("[patch preview]", result)

        content = self.runtime.read_file("notes/hello.txt")
        self.assertIn("hi", content)

    def test_rejects_path_outside_workspace(self):
        result = self.runtime.write_file("../escape.txt", "boom")
        self.assertIn("ERROR:", result)

    def test_list_files_and_grep_text(self):
        self.runtime.write_file("src/main.py", "print('hello')\n# TODO: fix\n")

        listing = self.runtime.list_files("src", "**/*.py", limit=20)
        self.assertIn("src/main.py", listing)

        grep = self.runtime.grep_text("TODO", "src", limit=20)
        self.assertIn("src/main.py:2", grep)

    def test_run_command_uses_workspace_as_cwd(self):
        output = self.runtime.run_command("pwd")
        self.assertIn(str(self.workspace), output)

    def test_scheduler_snapshot_is_derived_from_tool_metadata(self):
        self.runtime.update_tool_profile_for_task(
            "读取 jarvis.config.json，告诉我默认 model"
        )

        snapshot = self.runtime.scheduler_snapshot()

        self.assertEqual(snapshot.profile, "read_only")
        self.assertEqual(
            snapshot.active_tools,
            ("read_file", "list_files", "grep_text"),
        )
        self.assertEqual(snapshot.read_only_tools, snapshot.active_tools)
        self.assertEqual(snapshot.mutating_tools, ())
        self.assertEqual(snapshot.approval_tools, ())
        self.assertEqual(snapshot.parallel_tools, snapshot.active_tools)
        self.assertEqual(snapshot.context_tools, ())

    def test_tool_catalog_report_includes_scheduler_metadata(self):
        report = self.runtime.tool_catalog_report()

        self.assertIn("工具目录", report)
        self.assertIn("tool profile: full", report)
        self.assertIn("run_command", report)
        self.assertIn("meta: category=command; mutating, serial, approval, context", report)
        self.assertIn("meta: category=filesystem; read-only, parallel", report)

    def test_edit_file_replaces_exact_snippet_and_returns_patch(self):
        self.runtime.write_file("src/app.py", "print('hello')\nprint('bye')\n")
        result = self.runtime.edit_file(
            "src/app.py",
            "print('bye')",
            "print('patched')",
        )
        self.assertIn("OK: 已编辑", result)
        self.assertIn("[patch preview]", result)
        self.assertIn("patched", self.runtime.read_file("src/app.py"))

    def test_edit_file_requires_precise_match(self):
        self.runtime.write_file("src/app.py", "hello\nhello\n")
        result = self.runtime.edit_file("src/app.py", "hello", "patched")
        self.assertIn("replace_all=true", result)

    def test_write_file_shows_patch_before_apply(self):
        runtime = CapturingToolRuntime(self.workspace)
        result = runtime.write_file("notes/demo.txt", "hello\n")

        self.assertIn("OK:", result)
        self.assertIsNotNone(runtime.last_confirmation)
        _, preview, full_preview, accept_label = runtime.last_confirmation
        self.assertIn("[ Write File Review ]", preview)
        self.assertIn("[patch preview before apply]", preview)
        self.assertIn("status: ready to write", preview)
        self.assertIn("input: single-key mode; no Enter needed", preview)
        self.assertIn("keys: [y] apply | [p] full patch | [n] cancel", preview)
        self.assertIn("+++ b/notes/demo.txt", preview)
        self.assertIn("+++ b/notes/demo.txt", full_preview or "")
        self.assertIn("应用这个 patch", accept_label)

    def test_edit_file_shows_patch_before_apply(self):
        self.runtime.write_file("src/app.py", "print('hello')\n")
        runtime = CapturingToolRuntime(self.workspace)
        result = runtime.edit_file("src/app.py", "print('hello')", "print('patched')")

        self.assertIn("OK: 已编辑", result)
        self.assertIsNotNone(runtime.last_confirmation)
        _, preview, full_preview, _ = runtime.last_confirmation
        self.assertIn("[ Edit File Review ]", preview)
        self.assertIn("[patch preview before apply]", preview)
        self.assertIn("status: ready to edit", preview)
        self.assertIn("mode: single replace", preview)
        self.assertIn("input: single-key mode; no Enter needed", preview)
        self.assertIn("keys: [y] apply | [p] full patch | [n] cancel", preview)
        self.assertIn("-print('hello')", preview)
        self.assertIn("+print('patched')", preview)
        self.assertIn("+print('patched')", full_preview or "")

    def test_apply_patch_supports_multiple_hunks(self):
        self.runtime.write_file("src/app.py", "alpha\nbeta\ngamma\n")
        result = self.runtime.apply_patch(
            "src/app.py",
            [
                {"old_text": "alpha", "new_text": "ALPHA"},
                {"old_text": "gamma", "new_text": "GAMMA"},
            ],
        )
        self.assertIn("OK: 已对 src/app.py 应用 2 个 patch hunk", result)
        content = self.runtime.read_file("src/app.py")
        self.assertIn("ALPHA", content)
        self.assertIn("GAMMA", content)

    def test_apply_patch_shows_patch_before_apply(self):
        self.runtime.write_file("src/app.py", "alpha\nbeta\ngamma\n")
        runtime = CapturingToolRuntime(self.workspace)
        result = runtime.apply_patch(
            "src/app.py",
            [
                {"old_text": "alpha", "new_text": "ALPHA"},
                {"old_text": "gamma", "new_text": "GAMMA"},
            ],
        )

        self.assertIn("OK: 已对 src/app.py 应用 2 个 patch hunk", result)
        self.assertIsNotNone(runtime.last_patch_plan)
        action, preview, full_preview, allow_hunk_review = runtime.last_patch_plan
        self.assertEqual(action, "apply_patch")
        self.assertIn("[ Patch Review ]", preview)
        self.assertIn("[patch preview before apply]", preview)
        self.assertIn("status: waiting for approval", preview)
        self.assertIn("planned edits: 2", preview)
        self.assertIn("input: single-key mode; no Enter needed", preview)
        self.assertIn("keys: [y] apply all | [h] review hunks | [p] full patch", preview)
        self.assertIn("[n] cancel", preview)
        self.assertIn("-alpha", preview)
        self.assertIn("+ALPHA", preview)
        self.assertIn("+GAMMA", full_preview or "")
        self.assertTrue(allow_hunk_review)

    def test_apply_patch_can_review_hunks_individually(self):
        self.runtime.write_file("src/app.py", "alpha\nbeta\ngamma\n")
        runtime = CapturingToolRuntime(self.workspace)
        runtime.patch_mode = "review_hunks"
        runtime.hunk_actions = ["apply", "skip"]

        result = runtime.apply_patch(
            "src/app.py",
            [
                {"old_text": "alpha", "new_text": "ALPHA"},
                {"old_text": "gamma", "new_text": "GAMMA"},
            ],
        )

        self.assertIn("OK: 已对 src/app.py 选择性应用 1/2 个 patch hunk", result)
        content = self.runtime.read_file("src/app.py")
        self.assertIn("ALPHA", content)
        self.assertIn("gamma", content)
        self.assertNotIn("GAMMA", content)
        self.assertEqual(len(runtime.hunk_reviews), 2)
        self.assertIn("[ Patch Hunk 1/2 ]", runtime.hunk_reviews[0][2])
        self.assertIn("[patch hunk preview]", runtime.hunk_reviews[0][2])
        self.assertIn("status: reviewing hunk 1/2", runtime.hunk_reviews[0][2])
        self.assertIn("input: single-key mode; no Enter needed", runtime.hunk_reviews[0][2])
        self.assertIn("keys: [y] apply | [s] skip | [a] apply rest", runtime.hunk_reviews[0][2])
        self.assertIn("progress: accepted 0 | skipped 0 | remaining 2", runtime.hunk_reviews[0][2])

    def test_apply_patch_can_skip_every_hunk(self):
        self.runtime.write_file("src/app.py", "alpha\nbeta\ngamma\n")
        runtime = CapturingToolRuntime(self.workspace)
        runtime.patch_mode = "review_hunks"
        runtime.hunk_actions = ["skip", "skip"]

        result = runtime.apply_patch(
            "src/app.py",
            [
                {"old_text": "alpha", "new_text": "ALPHA"},
                {"old_text": "gamma", "new_text": "GAMMA"},
            ],
        )

        self.assertIn("DENIED: 用户没有应用 src/app.py 的任何 patch hunk", result)
        content = self.runtime.read_file("src/app.py")
        self.assertIn("alpha", content)
        self.assertIn("gamma", content)

    def test_apply_patch_hunk_preview_updates_progress(self):
        self.runtime.write_file("src/app.py", "alpha\nbeta\ngamma\n")
        runtime = CapturingToolRuntime(self.workspace)
        runtime.patch_mode = "review_hunks"
        runtime.hunk_actions = ["apply", "apply"]

        runtime.apply_patch(
            "src/app.py",
            [
                {"old_text": "alpha", "new_text": "ALPHA"},
                {"old_text": "gamma", "new_text": "GAMMA"},
            ],
        )

        self.assertEqual(len(runtime.hunk_reviews), 2)
        self.assertIn("status: reviewing hunk 2/2", runtime.hunk_reviews[1][2])
        self.assertIn("progress: accepted 1 | skipped 0 | remaining 1", runtime.hunk_reviews[1][2])

    def test_execute_tool_reports_invalid_parameters(self):
        result = self.runtime.execute_tool("read_file", {})
        self.assertIn("ERROR: 工具参数不合法 read_file", result)


if __name__ == "__main__":
    unittest.main()
