import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent import WorkspaceInspector, build_prompt_label


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

    def test_patch_report_includes_untracked_preview(self):
        report = self.inspector.patch_report()
        self.assertIn("tracked.txt", report)
        self.assertIn("notes.txt", report)
        self.assertIn("hello", report)

    def test_suggest_commit_message_prefers_agent_workflow(self):
        message = self.inspector.suggest_commit_message()
        self.assertTrue(message.startswith("chore:") or message.startswith("feat:"))

    def test_commit_all_creates_commit_and_cleans_worktree(self):
        ok, output = self.inspector.commit_all("test: save progress")
        self.assertTrue(ok)
        self.assertIn("test: save progress", output)
        self.assertTrue(self.inspector.is_clean())

    def test_status_snapshot_tracks_branch_and_dirty_counts(self):
        snapshot = self.inspector.status_snapshot()
        actual_branch = (
            subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.repo,
                check=True,
                capture_output=True,
                text=True,
            )
            .stdout.strip()
        )
        self.assertEqual(snapshot.branch, actual_branch)
        self.assertEqual(snapshot.modified, 1)
        self.assertEqual(snapshot.untracked, 1)
        self.assertFalse(snapshot.clean)

    def test_build_prompt_label_reflects_repo_status(self):
        snapshot = self.inspector.status_snapshot()
        prompt = build_prompt_label(snapshot, auto_approve=False)
        self.assertIn("ask", prompt)
        self.assertIn(snapshot.branch, prompt)
        self.assertTrue(prompt.startswith("jarvis ["))


if __name__ == "__main__":
    unittest.main()
