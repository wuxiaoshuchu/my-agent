from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

from context_engine import build_context_stats


@dataclass(frozen=True)
class RequestPayloadProfile:
    turn: int
    prompt_profile: str
    total_messages: int
    non_system_messages: int
    estimated_tokens: int
    system_message_chars: int
    session_memory_chars: int
    tool_schema_count: int
    tool_schema_chars: int
    tools_enabled: bool


@dataclass(frozen=True)
class ModelRequestTrace:
    turn: int
    status: str
    duration_ms: int
    tool_calls: int
    content_chars: int
    payload: RequestPayloadProfile
    error: str = ""


@dataclass(frozen=True)
class ToolExecutionTrace:
    tool_name: str
    category: str
    status: str
    duration_ms: int
    output_chars: int
    read_only: bool
    needs_approval: bool


@dataclass(frozen=True)
class ToolBatchTrace:
    mode: str
    tool_names: tuple[str, ...]
    tool_count: int
    read_only_count: int
    mutating_count: int
    duration_ms: int
    total_output_chars: int
    error_count: int
    denied_count: int


def build_request_payload_profile(
    messages: Sequence[dict[str, object]],
    tool_schemas: Sequence[dict[str, object]] | None,
    *,
    turn: int,
    prompt_profile: str = "full",
) -> RequestPayloadProfile:
    stats = build_context_stats(messages)
    system_messages = [
        str(message.get("content", ""))
        for message in messages
        if message.get("role") == "system"
    ]
    session_memory_chars = sum(
        len(text) for text in system_messages if text.lstrip().startswith("## Session Memory")
    )
    serialized_tools = json.dumps(tool_schemas or [], ensure_ascii=False, sort_keys=True)
    return RequestPayloadProfile(
        turn=turn,
        prompt_profile=prompt_profile,
        total_messages=stats.total_messages,
        non_system_messages=stats.non_system_messages,
        estimated_tokens=stats.estimated_tokens,
        system_message_chars=sum(len(text) for text in system_messages),
        session_memory_chars=session_memory_chars,
        tool_schema_count=len(tool_schemas or []),
        tool_schema_chars=len(serialized_tools),
        tools_enabled=bool(tool_schemas),
    )


def summarize_payload_profile(profile: RequestPayloadProfile) -> str:
    tools_label = (
        f"on ({profile.tool_schema_count} tools / {profile.tool_schema_chars} chars)"
        if profile.tools_enabled
        else "off"
    )
    return (
        f"turn={profile.turn} prompt={profile.prompt_profile} messages={profile.total_messages} "
        f"non_system={profile.non_system_messages} est_tokens={profile.estimated_tokens} "
        f"system_chars={profile.system_message_chars} memory_chars={profile.session_memory_chars} "
        f"tools={tools_label}"
    )


def summarize_request_trace(trace: ModelRequestTrace) -> str:
    prefix = (
        f"turn={trace.turn} {trace.status} {trace.duration_ms}ms "
        f"tool_calls={trace.tool_calls} content_chars={trace.content_chars}"
    )
    if trace.error:
        prefix += f" error={trace.error}"
    return f"{prefix} | {summarize_payload_profile(trace.payload)}"


def summarize_tool_trace(trace: ToolExecutionTrace) -> str:
    mode = "read-only" if trace.read_only else "mutating"
    approval = "approval" if trace.needs_approval else "no-approval"
    return (
        f"{trace.tool_name} [{trace.category}] {trace.status} {trace.duration_ms}ms "
        f"output_chars={trace.output_chars} {mode} {approval}"
    )


def summarize_tool_batch_trace(trace: ToolBatchTrace) -> str:
    names = ", ".join(trace.tool_names)
    return (
        f"{trace.mode} tools={trace.tool_count} read_only={trace.read_only_count} "
        f"mutating={trace.mutating_count} {trace.duration_ms}ms "
        f"output_chars={trace.total_output_chars} errors={trace.error_count} "
        f"denied={trace.denied_count} [{names}]"
    )


def render_payload_profile(profile: RequestPayloadProfile) -> str:
    lines = [
        f"- turn: {profile.turn}",
        f"- prompt_profile: {profile.prompt_profile}",
        f"- total_messages: {profile.total_messages}",
        f"- non_system_messages: {profile.non_system_messages}",
        f"- estimated_tokens: {profile.estimated_tokens}",
        f"- system_message_chars: {profile.system_message_chars}",
        f"- session_memory_chars: {profile.session_memory_chars}",
        f"- tools_enabled: {'yes' if profile.tools_enabled else 'no'}",
        f"- tool_schema_count: {profile.tool_schema_count}",
        f"- tool_schema_chars: {profile.tool_schema_chars}",
    ]
    return "\n".join(lines)
