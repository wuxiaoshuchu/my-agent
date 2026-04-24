from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence


DEFAULT_KEEP_RECENT_TURNS = 2
DEFAULT_MAX_MEMORY_BLOCKS = 3
DEFAULT_SUMMARY_ITEMS = 4
DEFAULT_MEMORY_CHAR_LIMIT = 3200
AUTO_COMPACT_TOKEN_RATIO = 0.6
AUTO_COMPACT_MESSAGE_COUNT = 18
AUTO_COMPACT_MIN_TURNS = 4


@dataclass(frozen=True)
class ContextStats:
    total_messages: int
    non_system_messages: int
    turn_count: int
    estimated_tokens: int


@dataclass(frozen=True)
class CompactionBlock:
    timestamp: str
    reason: str
    dropped_turns: int
    dropped_messages: int
    summary: str


@dataclass(frozen=True)
class SessionMemory:
    active_goal: str = ""
    compaction_blocks: tuple[CompactionBlock, ...] = ()


@dataclass(frozen=True)
class CompactionResult:
    compacted: bool
    kept_messages: list[dict[str, object]]
    memory: SessionMemory
    before_stats: ContextStats
    after_stats: ContextStats
    dropped_turns: int = 0
    dropped_messages: int = 0


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def estimate_message_tokens(messages: Sequence[dict[str, object]]) -> int:
    total = 0
    for message in messages:
        payload = json.dumps(message, ensure_ascii=False, sort_keys=True)
        total += max(8, estimate_text_tokens(payload))
    return total


def conversation_messages(messages: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    return [dict(message) for message in messages if message.get("role") != "system"]


def split_conversation_turns(
    messages: Sequence[dict[str, object]],
) -> list[list[dict[str, object]]]:
    turns: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    for message in messages:
        item = dict(message)
        if item.get("role") == "user" and current:
            turns.append(current)
            current = [item]
        else:
            current.append(item)
    if current:
        turns.append(current)
    return turns


def build_context_stats(messages: Sequence[dict[str, object]]) -> ContextStats:
    non_system = [message for message in messages if message.get("role") != "system"]
    return ContextStats(
        total_messages=len(messages),
        non_system_messages=len(non_system),
        turn_count=len(split_conversation_turns(non_system)),
        estimated_tokens=estimate_message_tokens(messages),
    )


def should_auto_compact(
    messages: Sequence[dict[str, object]],
    *,
    num_ctx: int,
) -> bool:
    stats = build_context_stats(conversation_messages(messages))
    if stats.turn_count < AUTO_COMPACT_MIN_TURNS:
        return False
    return (
        stats.estimated_tokens >= int(num_ctx * AUTO_COMPACT_TOKEN_RATIO)
        or stats.non_system_messages >= AUTO_COMPACT_MESSAGE_COUNT
    )


def render_session_memory(
    memory: SessionMemory,
    *,
    max_chars: int = DEFAULT_MEMORY_CHAR_LIMIT,
) -> str:
    if not memory.active_goal and not memory.compaction_blocks:
        return ""

    lines = ["## Session Memory"]
    if memory.active_goal:
        lines.append(f"- 当前任务目标：{_truncate(memory.active_goal, 240)}")
    if memory.compaction_blocks:
        lines.append(f"- 已 compact：{len(memory.compaction_blocks)} 次")
        for index, block in enumerate(reversed(memory.compaction_blocks), start=1):
            lines.append(
                f"### 历史摘要 {index} ({block.timestamp}, {block.reason})"
            )
            lines.append(
                f"- 压缩了 {block.dropped_turns} 个 turn / {block.dropped_messages} 条消息"
            )
            lines.append(block.summary)

    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... [session memory 过长，已截断，共 {len(text)} 字符]"


def compact_messages(
    messages: Sequence[dict[str, object]],
    *,
    memory: SessionMemory,
    reason: str,
    keep_recent_turns: int = DEFAULT_KEEP_RECENT_TURNS,
    now: datetime | None = None,
) -> CompactionResult:
    full_messages = [dict(message) for message in messages]
    before_stats = build_context_stats(full_messages)
    convo_messages = conversation_messages(full_messages)
    turns = split_conversation_turns(convo_messages)

    if len(turns) <= keep_recent_turns:
        return CompactionResult(
            compacted=False,
            kept_messages=convo_messages,
            memory=memory,
            before_stats=before_stats,
            after_stats=before_stats,
        )

    dropped_turn_groups = turns[:-keep_recent_turns]
    kept_turn_groups = turns[-keep_recent_turns:]
    dropped_messages = [message for turn in dropped_turn_groups for message in turn]
    kept_messages = [message for turn in kept_turn_groups for message in turn]
    block = CompactionBlock(
        timestamp=(now or datetime.now()).strftime("%H:%M:%S"),
        reason=reason,
        dropped_turns=len(dropped_turn_groups),
        dropped_messages=len(dropped_messages),
        summary=summarize_messages(dropped_messages, active_goal=memory.active_goal),
    )
    blocks = list(memory.compaction_blocks[-(DEFAULT_MAX_MEMORY_BLOCKS - 1) :])
    blocks.append(block)
    new_memory = SessionMemory(
        active_goal=memory.active_goal,
        compaction_blocks=tuple(blocks),
    )

    return CompactionResult(
        compacted=True,
        kept_messages=kept_messages,
        memory=new_memory,
        before_stats=before_stats,
        after_stats=build_context_stats(kept_messages),
        dropped_turns=len(dropped_turn_groups),
        dropped_messages=len(dropped_messages),
    )


def summarize_messages(
    messages: Sequence[dict[str, object]],
    *,
    active_goal: str,
    max_items: int = DEFAULT_SUMMARY_ITEMS,
) -> str:
    user_requests = _unique_snippets(
        [
            _truncate(str(message.get("content", "")).strip(), 160)
            for message in messages
            if message.get("role") == "user" and str(message.get("content", "")).strip()
        ],
        limit=max_items,
    )
    assistant_notes = _unique_snippets(
        [
            _truncate(str(message.get("content", "")).strip(), 160)
            for message in messages
            if message.get("role") == "assistant"
            and str(message.get("content", "")).strip()
            and not looks_like_tool_call_text(str(message.get("content", "")).strip())
        ],
        limit=max_items,
    )
    tool_names = _unique_snippets(_extract_tool_names(messages), limit=max_items)
    tool_results = _unique_snippets(
        [
            _truncate(str(message.get("content", "")).strip(), 140)
            for message in messages
            if message.get("role") == "tool" and str(message.get("content", "")).strip()
        ],
        limit=max_items,
    )

    lines = []
    if active_goal:
        lines.append(f"- 任务主线：{_truncate(active_goal, 220)}")
    if user_requests:
        lines.append("- 较早用户请求：")
        lines.extend(f"  - {item}" for item in user_requests)
    if tool_names:
        lines.append(f"- 较早工具轨迹：{', '.join(tool_names)}")
    if tool_results:
        lines.append("- 较早工具结果：")
        lines.extend(f"  - {item}" for item in tool_results)
    if assistant_notes:
        lines.append("- 较早 assistant 输出：")
        lines.extend(f"  - {item}" for item in assistant_notes)
    if not lines:
        lines.append("- 早前对话没有可保留的高价值文本。")
    return "\n".join(lines)


def _extract_tool_names(messages: Sequence[dict[str, object]]) -> list[str]:
    names: list[str] = []
    for message in messages:
        for tool_call in message.get("tool_calls", []) or []:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function") or {}
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    return names


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _unique_snippets(items: Sequence[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        results.append(normalized)
        if len(results) >= limit:
            break
    return results


def looks_like_tool_call_text(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    if normalized.startswith("<tool_call>") and normalized.endswith("</tool_call>"):
        return True
    if normalized.startswith("```") and '"function_name"' not in normalized and '"name"' not in normalized:
        return False

    decoder = json.JSONDecoder()
    candidates = [normalized]
    if normalized.startswith("```"):
        stripped = normalized.strip("`").strip()
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
        candidates.append(stripped)

    for candidate in candidates:
        try:
            obj, _ = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        name = obj.get("name") or obj.get("function_name")
        args = (
            obj.get("arguments")
            or obj.get("parameters")
            or obj.get("input")
            or obj.get("args")
        )
        if isinstance(name, str) and name.strip() and isinstance(args, dict):
            return True
    return False
