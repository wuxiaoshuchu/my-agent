import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent import WorkspaceInspector


class WorkspaceInspectorTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)

        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )

        (self.repo / "tracked.txt").write_text("v1\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )

        (self.repo / "tracked.txt").write_text("v2\n", encoding="utf-8")
        (self.repo / "notes.txt").write_text("hello\n", encoding="utf-8")

        self.inspector = WorkspaceInspector(self.repo)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_status_report_shows_modified_and_untracked_files(self):
        report = self.inspector.status_report()
        self.assertIn("tracked.txt", report)
        self.assertIn("notes.txt", report)

    def test_branch_report_includes_git_status_header(self):
        report = self.inspector.branch_report()
        self.assertIn("##", report)

    def test_diff_report_stat_lists_changed_file(self):
        report = self.inspector.diff_report(stat_only=True)
        self.assertIn("tracked.txt", report)

    def test_diff_report_for_untracked_file_returns_preview(self):
        report = self.inspector.diff_report(target="notes.txt")
        self.assertIn("还没有被 Git 跟踪", report)
        self.assertIn("hello", report)


if __name__ == "__main__":
    unittest.main()
