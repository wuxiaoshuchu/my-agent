import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime_config import (
    CONFIG_FILENAME,
    load_workspace_runtime_config,
    parse_ollama_list_output,
    save_workspace_runtime_config,
    summarize_command_failure,
)


class RuntimeConfigTests(unittest.TestCase):
    def test_save_workspace_runtime_config_preserves_existing_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            path = workspace / CONFIG_FILENAME
            path.write_text(
                '{\n  "model": "qwen2.5-coder:7b",\n  "custom_note": "keep-me"\n}\n',
                encoding="utf-8",
            )

            save_workspace_runtime_config(workspace, {"model": "qwen2.5-coder:14b"})
            data = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(data["model"], "qwen2.5-coder:14b")
            self.assertEqual(data["custom_note"], "keep-me")

    def test_load_workspace_runtime_config_returns_empty_dict_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            self.assertEqual(load_workspace_runtime_config(workspace), {})

    def test_parse_ollama_list_output_extracts_name_size_and_modified(self):
        text = (
            "NAME                   ID              SIZE      MODIFIED\n"
            "qwen2.5-coder:7b       dae161e27b0e    4.7 GB    20 hours ago\n"
            "deepseek-coder-v2:16b  63fb193b3a9b    8.9 GB    2 days ago\n"
        )

        records = parse_ollama_list_output(text)

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].name, "qwen2.5-coder:7b")
        self.assertEqual(records[0].size, "4.7 GB")
        self.assertEqual(records[1].modified, "2 days ago")

    def test_summarize_command_failure_collapses_crash_trace(self):
        detail = summarize_command_failure(
            returncode=134,
            stdout="SIGABRT: abort\nlots of frames\n",
            stderr="WARNING: something noisy\n",
            tool_name="ollama list",
        )

        self.assertIn("MLX/Metal", detail)


if __name__ == "__main__":
    unittest.main()
