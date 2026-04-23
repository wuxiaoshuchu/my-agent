import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent import parse_args


class CliTests(unittest.TestCase):
    def test_parse_args_accepts_task_and_flags(self):
        args = parse_args(["--cwd", "/tmp/demo", "--auto-approve", "hello", "world"])
        self.assertEqual(args.cwd, "/tmp/demo")
        self.assertTrue(args.auto_approve)
        self.assertEqual(args.task, ["hello", "world"])

    def test_parse_args_can_enter_repl_without_task(self):
        args = parse_args(["--repl"])
        self.assertTrue(args.repl)
        self.assertEqual(args.task, [])


if __name__ == "__main__":
    unittest.main()
