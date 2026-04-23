import tempfile
import unittest
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import ToolRuntime


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


if __name__ == "__main__":
    unittest.main()
