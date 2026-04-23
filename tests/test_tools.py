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
        self.last_confirmation: tuple[str, str] | None = None

    def _confirm(self, action: str, preview: str) -> bool:
        self.last_confirmation = (action, preview)
        return self.allow


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
        _, preview = runtime.last_confirmation
        self.assertIn("[patch preview before apply]", preview)
        self.assertIn("+++ b/notes/demo.txt", preview)

    def test_edit_file_shows_patch_before_apply(self):
        self.runtime.write_file("src/app.py", "print('hello')\n")
        runtime = CapturingToolRuntime(self.workspace)
        result = runtime.edit_file("src/app.py", "print('hello')", "print('patched')")

        self.assertIn("OK: 已编辑", result)
        self.assertIsNotNone(runtime.last_confirmation)
        _, preview = runtime.last_confirmation
        self.assertIn("[patch preview before apply]", preview)
        self.assertIn("-print('hello')", preview)
        self.assertIn("+print('patched')", preview)


if __name__ == "__main__":
    unittest.main()
