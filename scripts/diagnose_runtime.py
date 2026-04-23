#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI

from agent import AgentSession, build_config, build_system_prompt, parse_args
from runtime_diagnostics import (
    DiagnosticCaseResult,
    DiagnosticRun,
    diagnostics_output_prefix,
    pretty_json,
    render_markdown_report,
    serialize_run,
)
from tools import ToolRuntime


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "diagnostic-results"


def parse_script_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose local Ollama runtime behavior.")
    parser.add_argument("--model", required=True, help="Model name to diagnose.")
    parser.add_argument(
        "--cwd",
        default=str(PROJECT_ROOT),
        help="Workspace root for agent-style diagnostic prompts.",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:11434/v1",
        help="OpenAI-compatible base URL used by jarvis.",
    )
    parser.add_argument(
        "--short-timeout",
        type=int,
        default=20,
        help="Short timeout used to reproduce benchmark failures.",
    )
    parser.add_argument(
        "--long-timeout",
        type=int,
        default=120,
        help="Long timeout used to confirm whether the model can eventually finish.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory used to write markdown/json diagnostic reports.",
    )
    return parser.parse_args(argv)


def run_subprocess_case(
    case_id: str,
    command: list[str],
    *,
    timeout_s: int = 15,
) -> DiagnosticCaseResult:
    start = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        return DiagnosticCaseResult(
            case_id=case_id,
            status="timeout",
            duration_ms=duration_ms,
            summary=f"subprocess timed out after {timeout_s}s",
            detail=(exc.stdout or exc.stderr or "").strip(),
        )
    except OSError as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        return DiagnosticCaseResult(
            case_id=case_id,
            status="error",
            duration_ms=duration_ms,
            summary=str(exc),
        )

    duration_ms = int((time.perf_counter() - start) * 1000)
    detail = (result.stdout or result.stderr).strip()
    status = "ok" if result.returncode == 0 else "error"
    summary = f"returncode={result.returncode}"
    return DiagnosticCaseResult(
        case_id=case_id,
        status=status,
        duration_ms=duration_ms,
        summary=summary,
        detail=detail,
    )


def run_http_json_case(
    *,
    case_id: str,
    url: str,
    payload: dict[str, object] | None,
    timeout_s: int,
) -> DiagnosticCaseResult:
    request = urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        reason = getattr(exc, "reason", exc)
        status = "timeout" if isinstance(reason, TimeoutError) else "error"
        return DiagnosticCaseResult(
            case_id=case_id,
            status=status,
            duration_ms=duration_ms,
            summary=str(reason),
        )

    duration_ms = int((time.perf_counter() - start) * 1000)
    return DiagnosticCaseResult(
        case_id=case_id,
        status="ok",
        duration_ms=duration_ms,
        summary="HTTP request completed",
        detail=body,
    )


def latest_assistant_output(messages: list[dict[str, object]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "assistant" and message.get("content"):
            return str(message["content"])
    return ""


def run_openai_case(
    *,
    case_id: str,
    client: OpenAI,
    payload: dict[str, object],
    extract_summary,
) -> DiagnosticCaseResult:
    start = time.perf_counter()
    try:
        response = client.chat.completions.create(**payload)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        status = "timeout" if type(exc).__name__ == "APITimeoutError" else "error"
        return DiagnosticCaseResult(
            case_id=case_id,
            status=status,
            duration_ms=duration_ms,
            summary=f"{type(exc).__name__}: {exc}",
        )

    duration_ms = int((time.perf_counter() - start) * 1000)
    summary, detail = extract_summary(response)
    return DiagnosticCaseResult(
        case_id=case_id,
        status="ok",
        duration_ms=duration_ms,
        summary=summary,
        detail=detail,
    )


def run_agent_case(
    *,
    case_id: str,
    config_args: list[str],
    prompt: str,
    timeout_s: int,
) -> DiagnosticCaseResult:
    config = build_config(parse_args(config_args))
    session = AgentSession(config)
    session.client = OpenAI(
        base_url=config.base_url,
        api_key=config.api_key,
        timeout=timeout_s,
        max_retries=0,
    )

    start = time.perf_counter()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            session.handle_user_turn(prompt)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        status = "timeout" if type(exc).__name__ == "APITimeoutError" else "error"
        return DiagnosticCaseResult(
            case_id=case_id,
            status=status,
            duration_ms=duration_ms,
            summary=f"{type(exc).__name__}: {exc}",
        )

    duration_ms = int((time.perf_counter() - start) * 1000)
    tool_calls = [entry for entry in session.activity_log if entry.kind == "tool_call"]
    final_output = latest_assistant_output(session.messages)
    summary = (
        f"completed with {len(tool_calls)} tool calls; final_output={final_output[:120]!r}"
    )
    detail = "\n".join(
        f"{entry.timestamp} [{entry.kind}] {entry.summary}" for entry in session.activity_log
    )
    return DiagnosticCaseResult(
        case_id=case_id,
        status="ok",
        duration_ms=duration_ms,
        summary=summary,
        detail=detail,
    )


def write_outputs(output_dir: Path, run: DiagnosticRun) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = diagnostics_output_prefix(run.model)
    json_path = output_dir / f"{prefix}.json"
    md_path = output_dir / f"{prefix}.md"
    json_path.write_text(pretty_json(serialize_run(run)) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown_report(run) + "\n", encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    args = parse_script_args(argv)
    workspace_root = str(Path(args.cwd).expanduser().resolve())
    started_at = datetime.now().isoformat(timespec="seconds")

    config_args = [
        "--cwd",
        workspace_root,
        "--model",
        args.model,
        "--base-url",
        args.base_url,
        "--auto-approve",
        "--max-turns",
        "3",
    ]
    config = build_config(parse_args(config_args))
    runtime = ToolRuntime(config.workspace_root, auto_approve=True, command_timeout=30)
    system_prompt = build_system_prompt(config, runtime)
    base_url_root = args.base_url.removesuffix("/v1")
    short_client = OpenAI(
        base_url=config.base_url,
        api_key=config.api_key,
        timeout=args.short_timeout,
        max_retries=0,
    )
    quick_prompt = "Reply with OK only."
    runtime_prompt = (
        "读取 jarvis.config.json，告诉我默认 model、base_url 和 num_ctx。请尽量简短，并保留原值。"
    )

    results = [
        run_subprocess_case("ollama_ps_before", ["ollama", "ps"]),
        run_http_json_case(
            case_id="api_version",
            url=f"{base_url_root}/api/version",
            payload=None,
            timeout_s=5,
        ),
        run_http_json_case(
            case_id="api_tags",
            url=f"{base_url_root}/api/tags",
            payload=None,
            timeout_s=5,
        ),
        run_http_json_case(
            case_id="direct_chat_minimal",
            url=f"{base_url_root}/api/chat",
            payload={
                "model": args.model,
                "stream": False,
                "messages": [{"role": "user", "content": quick_prompt}],
            },
            timeout_s=90,
        ),
        run_openai_case(
            case_id="openai_minimal",
            client=short_client,
            payload={
                "model": args.model,
                "messages": [{"role": "user", "content": quick_prompt}],
                "stream": False,
            },
            extract_summary=lambda response: (
                f"content={response.choices[0].message.content!r}",
                pretty_json(response.model_dump()),
            ),
        ),
        run_openai_case(
            case_id="openai_agent_quick_prompt",
            client=short_client,
            payload={
                "model": args.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": quick_prompt},
                ],
                "tools": runtime.tool_schemas,
                "tool_choice": "auto",
                "extra_body": {"options": {"num_ctx": config.num_ctx}},
            },
            extract_summary=lambda response: (
                f"content={response.choices[0].message.content!r}; tool_calls={bool(response.choices[0].message.tool_calls)}",
                pretty_json(response.model_dump()),
            ),
        ),
        run_agent_case(
            case_id="agent_runtime_defaults_short",
            config_args=config_args,
            prompt=runtime_prompt,
            timeout_s=args.short_timeout,
        ),
        run_agent_case(
            case_id="agent_runtime_defaults_long",
            config_args=config_args,
            prompt=runtime_prompt,
            timeout_s=args.long_timeout,
        ),
        run_subprocess_case("ollama_ps_after", ["ollama", "ps"]),
    ]

    finished_at = datetime.now().isoformat(timespec="seconds")
    run = DiagnosticRun(
        model=args.model,
        workspace_root=workspace_root,
        started_at=started_at,
        finished_at=finished_at,
        short_timeout_s=args.short_timeout,
        long_timeout_s=args.long_timeout,
        results=results,
    )
    output_dir = Path(args.output_dir).expanduser().resolve()
    json_path, md_path = write_outputs(output_dir, run)
    print(f"json: {json_path}")
    print(f"markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
