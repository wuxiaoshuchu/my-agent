from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


CONFIG_FILENAME = "jarvis.config.json"


@dataclass(frozen=True)
class RuntimeConfigSources:
    model: str
    base_url: str
    num_ctx: str


@dataclass(frozen=True)
class LocalModelRecord:
    name: str
    identifier: str
    size: str
    modified: str


def workspace_config_path(workspace_root: Path) -> Path:
    return workspace_root / CONFIG_FILENAME


def load_workspace_runtime_config(workspace_root: Path) -> dict[str, object]:
    path = workspace_config_path(workspace_root)
    if not path.exists():
        return {}

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} 必须是 JSON object。")
    return data


def save_workspace_runtime_config(
    workspace_root: Path, updates: dict[str, object]
) -> Path:
    path = workspace_config_path(workspace_root)
    data = load_workspace_runtime_config(workspace_root)

    for key, value in updates.items():
        if value is None:
            data.pop(key, None)
            continue
        data[key] = value

    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def resolve_runtime_value(
    *,
    cli_value: object,
    config_value: object,
    default: object,
    config_label: str,
) -> tuple[object, str]:
    if cli_value is not None:
        return cli_value, "cli"
    if config_value is not None:
        return config_value, config_label
    return default, "default"


def normalize_string_setting(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是字符串。")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} 不能为空。")
    return text


def normalize_positive_int(value: object, *, field_name: str) -> int:
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        raise ValueError(f"{field_name} 必须是正整数。")

    if parsed <= 0:
        raise ValueError(f"{field_name} 必须大于 0。")
    return parsed


def is_local_base_url(base_url: str) -> bool:
    parsed = urlparse(base_url)
    return (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}


def describe_runtime_provider(base_url: str) -> str:
    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").lower()

    if hostname in {"localhost", "127.0.0.1", "::1"}:
        if parsed.port == 11434:
            return "local Ollama (OpenAI-compatible)"
        return "local OpenAI-compatible runtime"
    return "remote OpenAI-compatible API"


def parse_ollama_list_output(text: str) -> list[LocalModelRecord]:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 1:
        return []

    records: list[LocalModelRecord] = []
    for line in lines[1:]:
        parts = re.split(r"\s{2,}", line.strip(), maxsplit=3)
        if len(parts) < 4:
            continue
        name, identifier, size, modified = parts
        records.append(
            LocalModelRecord(
                name=name,
                identifier=identifier,
                size=size,
                modified=modified,
            )
        )
    return records


def summarize_command_failure(
    *, returncode: int, stdout: str, stderr: str, tool_name: str
) -> str:
    combined = "\n".join(part for part in (stderr, stdout) if part).strip()
    normalized = combined.lower()

    if "sigabrt" in normalized or "terminating due to uncaught exception" in normalized:
        return f"`{tool_name}` 异常退出，疑似 MLX/Metal 运行时崩溃。"

    for line in combined.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith("WARNING:"):
            continue
        return text[:200]

    return f"`{tool_name}` 失败，退出码 {returncode}。"


def list_local_models() -> tuple[list[LocalModelRecord], str | None]:
    if shutil.which("ollama") is None:
        return [], "未检测到 `ollama` 可执行文件。"

    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return [], f"`ollama list` 执行失败: {exc}"

    if result.returncode != 0:
        detail = summarize_command_failure(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            tool_name="ollama list",
        )
        return [], f"`ollama list` 失败: {detail}"

    return parse_ollama_list_output(result.stdout), None
