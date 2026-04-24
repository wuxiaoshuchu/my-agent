"""工具运行时与执行器。"""

from __future__ import annotations

import difflib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

from performance_trace import ToolBatchTrace, ToolExecutionTrace
from tool_registry import (
    DEFAULT_TOOL_NAMES,
    READ_ONLY_TOOL_NAMES,
    TOOL_SPEC_MAP,
    ToolSchedulerSnapshot,
    ToolSpec,
    infer_tool_profile,
    tool_list_label,
)

if TYPE_CHECKING:
    from typing import Callable


MAX_TOOL_OUTPUT_CHARS = 3000
MAX_FILE_PREVIEW_CHARS = 12000
MAX_PATCH_PREVIEW_CHARS = 4000
MAX_CONFIRM_PREVIEW_CHARS = 2000
MAX_FULL_PATCH_DISPLAY_CHARS = 12000
PATCH_REVIEW_PANEL_WIDTH = 78
DEFAULT_LIST_LIMIT = 200
DEFAULT_GREP_LIMIT = 50
MAX_TEXT_FILE_SIZE = 512 * 1024


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head_len = limit * 2 // 3
    tail_len = limit - head_len
    omitted = len(text) - limit
    head = text[:head_len]
    tail = text[-tail_len:]
    return (
        head
        + f"\n\n... [已省略 {omitted} 字符。请缩小范围后再读] ...\n\n"
        + tail
    )


def _read_text_file(path: Path, limit: int = MAX_FILE_PREVIEW_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"文件不是 UTF-8 文本: {path}")
    return _truncate(text, limit=limit)


def _relative_display(path: Path, workspace_root: Path) -> str:
    return str(path.relative_to(workspace_root))


def _build_patch_preview(
    old_text: str,
    new_text: str,
    rel_path: str,
    *,
    existed_before: bool,
    limit: int | None = MAX_PATCH_PREVIEW_CHARS,
) -> str:
    from_file = f"a/{rel_path}" if existed_before else "/dev/null"
    to_file = f"b/{rel_path}"
    diff = "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=from_file,
            tofile=to_file,
        )
    )
    if not diff:
        return "(没有文本变化)"
    if limit is None:
        return diff
    return _truncate(diff, limit=limit)


def _looks_dangerous_command(cmd: str) -> bool:
    patterns = [
        r"\brm\b",
        r"\bsudo\b",
        r"git\s+reset\s+--hard",
        r"git\s+checkout\s+--",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bmkfs\b",
    ]
    return any(re.search(pattern, cmd) for pattern in patterns)


def _supports_color(stream=None) -> bool:
    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", None) and stream.isatty())


def _style_text(text: str, *, color: str, use_color: bool) -> str:
    if not use_color:
        return text
    color_map = {
        "red": "\033[31m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "cyan": "\033[36m",
        "bold": "\033[1m",
        "dim": "\033[2m",
    }
    prefix = color_map.get(color)
    if not prefix:
        return text
    return f"{prefix}{text}\033[0m"


def _format_patch_diff(diff: str, *, use_color: bool) -> str:
    lines = []
    for line in diff.splitlines():
        if line.startswith("@@") or line.startswith("+++") or line.startswith("---"):
            lines.append(_style_text(line, color="cyan", use_color=use_color))
        elif line.startswith("+") and not line.startswith("+++"):
            lines.append(_style_text(line, color="green", use_color=use_color))
        elif line.startswith("-") and not line.startswith("---"):
            lines.append(_style_text(line, color="red", use_color=use_color))
        else:
            lines.append(line)
    return "\n".join(lines)


def _count_patch_stats(diff: str) -> tuple[int, int, int]:
    additions = 0
    deletions = 0
    hunks = 0
    for line in diff.splitlines():
        if line.startswith("@@"):
            hunks += 1
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return additions, deletions, hunks


def _render_panel(title: str, rows: list[str], *, width: int = PATCH_REVIEW_PANEL_WIDTH) -> str:
    content_width = max(20, width - 4)
    use_color = _supports_color()

    def fit(line: str) -> str:
        if len(line) > content_width:
            return line[: content_width - 3] + "..."
        return line

    border = "+" + "-" * (content_width + 2) + "+"
    lines = [border, f"| {fit(f'[ {title} ]').ljust(content_width)} |"]
    for row in rows:
        lines.append(f"| {fit(row).ljust(content_width)} |")
    lines.append(border)
    if use_color:
        lines[0] = _style_text(lines[0], color="dim", use_color=True)
        lines[1] = _style_text(lines[1], color="cyan", use_color=True)
        for index, row in enumerate(rows, start=2):
            if row.startswith("keys:") or row.lstrip().startswith("["):
                lines[index] = _style_text(lines[index], color="yellow", use_color=True)
            elif row.startswith("input:"):
                lines[index] = _style_text(lines[index], color="dim", use_color=True)
            elif row.startswith("status:"):
                lines[index] = _style_text(lines[index], color="bold", use_color=True)
        lines[-1] = _style_text(lines[-1], color="dim", use_color=True)
    return "\n".join(lines)


def _render_patch_review_screen(
    title: str,
    rel_path: str,
    diff: str,
    *,
    preview_label: str,
    meta_lines: list[str],
) -> str:
    additions, deletions, diff_hunks = _count_patch_stats(diff)
    rows = [
        f"file: {rel_path}",
        f"delta: +{additions} / -{deletions} | diff hunks: {diff_hunks}",
        *meta_lines,
    ]
    panel = _render_panel(title, rows)
    body = _format_patch_diff(diff, use_color=_supports_color())
    return f"{panel}\n{preview_label}\n{body}"


def _render_key_hints(actions: list[tuple[str, str]], *, width: int = PATCH_REVIEW_PANEL_WIDTH) -> list[str]:
    content_width = max(20, width - 4)
    prefix = "keys: "
    continuation = " " * len(prefix)
    rows: list[str] = []
    current = prefix
    for key, label in actions:
        chunk = f"[{key}] {label}"
        separator = " | " if current.strip() and current != prefix else ""
        if len(current) + len(separator) + len(chunk) > content_width:
            rows.append(current)
            current = continuation + chunk
            continue
        current += separator + chunk
    if current.strip():
        rows.append(current)
    return rows


@dataclass
class FileToolExecutor:
    runtime: "ToolRuntime"
    workspace_root: Path

    def _resolve_path(self, path: str, *, allow_missing: bool = False) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        resolved = candidate.resolve(strict=False)

        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError(f"路径超出工作区: {path}") from exc

        if not allow_missing and not resolved.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        return resolved

    def _normalize_patch_edits(self, edits: list[dict]) -> list[dict[str, str | bool]]:
        if not edits:
            raise ValueError("edits 不能为空")

        normalized: list[dict[str, str | bool]] = []
        for index, edit in enumerate(edits, start=1):
            if not isinstance(edit, dict):
                raise ValueError(f"patch 第 {index} 项不是对象")
            old_text = edit.get("old_text")
            new_text = edit.get("new_text")
            replace_all = bool(edit.get("replace_all", False))
            if not isinstance(old_text, str) or not isinstance(new_text, str):
                raise ValueError(f"patch 第 {index} 项缺少合法的 old_text/new_text")
            normalized.append(
                {
                    "old_text": old_text,
                    "new_text": new_text,
                    "replace_all": replace_all,
                }
            )
        return normalized

    def _apply_patch_edits(
        self,
        content: str,
        edits: list[dict[str, str | bool]],
    ) -> tuple[str, int]:
        updated = content
        total_replacements = 0
        for index, edit in enumerate(edits, start=1):
            try:
                updated, replaced = self._replace_exact(
                    updated,
                    old_text=str(edit["old_text"]),
                    new_text=str(edit["new_text"]),
                    replace_all=bool(edit["replace_all"]),
                )
            except ValueError as exc:
                raise ValueError(f"patch 第 {index} 项失败: {exc}") from exc
            total_replacements += replaced
        return updated, total_replacements

    def _replace_exact(
        self,
        content: str,
        *,
        old_text: str,
        new_text: str,
        replace_all: bool,
    ) -> tuple[str, int]:
        occurrences = content.count(old_text)
        if occurrences == 0:
            raise ValueError("old_text 没有在目标文件中找到，无法精确编辑")
        if occurrences > 1 and not replace_all:
            raise ValueError(
                f"old_text 出现了 {occurrences} 次。请提供更精确的片段，或显式设置 replace_all=true"
            )

        if replace_all:
            updated = content.replace(old_text, new_text)
            replaced_count = occurrences
        else:
            updated = content.replace(old_text, new_text, 1)
            replaced_count = 1

        if updated == content:
            raise ValueError("编辑后内容没有变化")
        return updated, replaced_count

    def read_file(self, path: str) -> str:
        try:
            file_path = self._resolve_path(path)
        except Exception as exc:
            return f"ERROR: {exc}"

        if file_path.is_dir():
            return f"ERROR: 这是目录不是文件: {_relative_display(file_path, self.workspace_root)}"

        try:
            content = _read_text_file(file_path)
        except Exception as exc:
            return f"ERROR: {exc}"

        rel = _relative_display(file_path, self.workspace_root)
        return f"[file] {rel}\n{content}"

    def write_file(self, path: str, content: str) -> str:
        try:
            file_path = self._resolve_path(path, allow_missing=True)
        except Exception as exc:
            return f"ERROR: {exc}"

        existed_before = file_path.exists()
        old_text = ""
        if existed_before and file_path.is_file():
            try:
                old_text = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return f"ERROR: 目标文件不是 UTF-8 文本: {_relative_display(file_path, self.workspace_root)}"

        rel = _relative_display(file_path, self.workspace_root)
        full_patch = _build_patch_preview(
            old_text,
            content,
            rel,
            existed_before=existed_before,
            limit=None,
        )
        patch = _build_patch_preview(
            old_text,
            content,
            rel,
            existed_before=existed_before,
        )
        preview = _render_patch_review_screen(
            "Write File Review",
            rel,
            patch,
            preview_label="[patch preview before apply]",
            meta_lines=[
                "status: ready to write",
                f"mode: {'create' if not existed_before else 'overwrite'}",
                "input: single-key mode; no Enter needed",
                *_render_key_hints(
                    [("y", "apply"), ("p", "full patch"), ("n", "cancel")]
                ),
            ],
        )
        full_preview_screen = _render_patch_review_screen(
            "Write File Review",
            rel,
            full_patch,
            preview_label="[patch preview before apply]",
            meta_lines=[
                "status: ready to write",
                f"mode: {'create' if not existed_before else 'overwrite'}",
                "input: single-key mode; no Enter needed",
                *_render_key_hints(
                    [("y", "apply"), ("p", "full patch"), ("n", "cancel")]
                ),
            ],
        )
        if not self.runtime._confirm(
            "write_file",
            preview,
            full_preview=full_preview_screen,
            accept_label="应用这个 patch",
        ):
            return f"DENIED: 用户拒绝写入 {rel}"

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"OK: 写入 {len(content)} 字符到 {rel}\n\n[patch preview]\n{patch}"

    def edit_file(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
    ) -> str:
        try:
            file_path = self._resolve_path(path)
        except Exception as exc:
            return f"ERROR: {exc}"

        if file_path.is_dir():
            return f"ERROR: 这是目录不是文件: {_relative_display(file_path, self.workspace_root)}"

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"ERROR: 目标文件不是 UTF-8 文本: {_relative_display(file_path, self.workspace_root)}"

        try:
            updated, replaced_count = self._replace_exact(
                content,
                old_text=old_text,
                new_text=new_text,
                replace_all=replace_all,
            )
        except ValueError as exc:
            return f"ERROR: {exc}"

        rel = _relative_display(file_path, self.workspace_root)
        full_patch = _build_patch_preview(
            content,
            updated,
            rel,
            existed_before=True,
            limit=None,
        )
        patch = _build_patch_preview(
            content,
            updated,
            rel,
            existed_before=True,
        )
        mode_text = "replace_all" if replace_all else "single replace"
        preview = _render_patch_review_screen(
            "Edit File Review",
            rel,
            patch,
            preview_label="[patch preview before apply]",
            meta_lines=[
                "status: ready to edit",
                f"mode: {mode_text}",
                "input: single-key mode; no Enter needed",
                *_render_key_hints(
                    [("y", "apply"), ("p", "full patch"), ("n", "cancel")]
                ),
            ],
        )
        full_preview_screen = _render_patch_review_screen(
            "Edit File Review",
            rel,
            full_patch,
            preview_label="[patch preview before apply]",
            meta_lines=[
                "status: ready to edit",
                f"mode: {mode_text}",
                "input: single-key mode; no Enter needed",
                *_render_key_hints(
                    [("y", "apply"), ("p", "full patch"), ("n", "cancel")]
                ),
            ],
        )
        if not self.runtime._confirm(
            "edit_file",
            preview,
            full_preview=full_preview_screen,
            accept_label="应用这个 patch",
        ):
            return f"DENIED: 用户拒绝编辑 {rel}"

        file_path.write_text(updated, encoding="utf-8")
        return (
            f"OK: 已编辑 {rel}，替换 {replaced_count} 处匹配\n\n"
            f"[patch preview]\n{patch}"
        )

    def apply_patch(self, path: str, edits: list[dict]) -> str:
        try:
            file_path = self._resolve_path(path)
        except Exception as exc:
            return f"ERROR: {exc}"

        if file_path.is_dir():
            return f"ERROR: 这是目录不是文件: {_relative_display(file_path, self.workspace_root)}"

        try:
            original = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"ERROR: 目标文件不是 UTF-8 文本: {_relative_display(file_path, self.workspace_root)}"

        try:
            normalized_edits = self._normalize_patch_edits(edits)
            updated, total_replacements = self._apply_patch_edits(
                original,
                normalized_edits,
            )
        except ValueError as exc:
            return f"ERROR: {exc}"

        rel = _relative_display(file_path, self.workspace_root)
        full_patch = _build_patch_preview(
            original,
            updated,
            rel,
            existed_before=True,
            limit=None,
        )
        patch = _build_patch_preview(
            original,
            updated,
            rel,
            existed_before=True,
        )
        allow_hunk_review = len(normalized_edits) > 1
        preview = _render_patch_review_screen(
            "Patch Review",
            rel,
            patch,
            preview_label="[patch preview before apply]",
            meta_lines=[
                "status: waiting for approval",
                f"planned edits: {len(normalized_edits)}",
                "input: single-key mode; no Enter needed",
                *(
                    _render_key_hints(
                        [
                            ("y", "apply all"),
                            ("h", "review hunks"),
                            ("p", "full patch"),
                            ("n", "cancel"),
                        ]
                    )
                    if allow_hunk_review
                    else _render_key_hints(
                        [("y", "apply"), ("p", "full patch"), ("n", "cancel")]
                    )
                ),
            ],
        )
        full_preview_screen = _render_patch_review_screen(
            "Patch Review",
            rel,
            full_patch,
            preview_label="[patch preview before apply]",
            meta_lines=[
                "status: waiting for approval",
                f"planned edits: {len(normalized_edits)}",
                "input: single-key mode; no Enter needed",
                *(
                    _render_key_hints(
                        [
                            ("y", "apply all"),
                            ("h", "review hunks"),
                            ("p", "full patch"),
                            ("n", "cancel"),
                        ]
                    )
                    if allow_hunk_review
                    else _render_key_hints(
                        [("y", "apply"), ("p", "full patch"), ("n", "cancel")]
                    )
                ),
            ],
        )
        decision = self.runtime._choose_patch_apply_mode(
            "apply_patch",
            preview,
            full_preview=full_preview_screen,
            allow_hunk_review=allow_hunk_review,
        )
        if decision == "deny":
            return f"DENIED: 用户拒绝应用 patch 到 {rel}"
        if decision == "apply_all":
            file_path.write_text(updated, encoding="utf-8")
            return (
                f"OK: 已对 {rel} 应用 {len(normalized_edits)} 个 patch hunk，替换 {total_replacements} 处匹配\n\n"
                f"[patch preview]\n{patch}"
            )

        current = original
        accepted_hunks = 0
        skipped_hunks = 0
        total_replacements = 0
        apply_remaining = False
        notes: list[str] = []

        for index, edit in enumerate(normalized_edits, start=1):
            try:
                candidate, replaced = self._replace_exact(
                    current,
                    old_text=str(edit["old_text"]),
                    new_text=str(edit["new_text"]),
                    replace_all=bool(edit["replace_all"]),
                )
            except ValueError as exc:
                skipped_hunks += 1
                notes.append(f"第 {index} 段在当前内容下无法应用，已跳过: {exc}")
                continue

            hunk_full_patch = _build_patch_preview(
                current,
                candidate,
                rel,
                existed_before=True,
                limit=None,
            )
            hunk_patch = _build_patch_preview(
                current,
                candidate,
                rel,
                existed_before=True,
            )
            hunk_preview = _render_patch_review_screen(
                f"Patch Hunk {index}/{len(normalized_edits)}",
                rel,
                hunk_patch,
                preview_label="[patch hunk preview]",
                meta_lines=[
                    f"status: reviewing hunk {index}/{len(normalized_edits)}",
                    f"progress: accepted {accepted_hunks} | skipped {skipped_hunks} | remaining {len(normalized_edits) - index + 1}",
                    "input: single-key mode; no Enter needed",
                    *_render_key_hints(
                        [
                            ("y", "apply"),
                            ("s", "skip"),
                            ("a", "apply rest"),
                            ("p", "full hunk"),
                            ("q", "stop"),
                        ]
                    ),
                ],
            )
            hunk_full_preview = _render_patch_review_screen(
                f"Patch Hunk {index}/{len(normalized_edits)}",
                rel,
                hunk_full_patch,
                preview_label="[patch hunk preview]",
                meta_lines=[
                    f"status: reviewing hunk {index}/{len(normalized_edits)}",
                    f"progress: accepted {accepted_hunks} | skipped {skipped_hunks} | remaining {len(normalized_edits) - index + 1}",
                    "input: single-key mode; no Enter needed",
                    *_render_key_hints(
                        [
                            ("y", "apply"),
                            ("s", "skip"),
                            ("a", "apply rest"),
                            ("p", "full hunk"),
                            ("q", "stop"),
                        ]
                    ),
                ],
            )
            if apply_remaining:
                hunk_decision = "apply"
            else:
                hunk_decision = self.runtime._choose_patch_hunk_action(
                    rel,
                    index,
                    len(normalized_edits),
                    hunk_preview,
                    full_preview=hunk_full_preview,
                )

            if hunk_decision == "stop":
                remaining = len(normalized_edits) - index + 1
                skipped_hunks += remaining
                notes.append(
                    f"用户在第 {index} 段结束逐段审批，剩余 {remaining} 个 hunk 已跳过"
                )
                break
            if hunk_decision == "skip":
                skipped_hunks += 1
                continue

            current = candidate
            accepted_hunks += 1
            total_replacements += replaced
            if hunk_decision == "apply_rest":
                apply_remaining = True

        if accepted_hunks == 0:
            lines = [f"DENIED: 用户没有应用 {rel} 的任何 patch hunk"]
            if notes:
                lines.extend(["", "[patch notes]"])
                lines.extend(f"- {note}" for note in notes)
            return "\n".join(lines)

        final_patch = _build_patch_preview(
            original,
            current,
            rel,
            existed_before=True,
        )
        file_path.write_text(current, encoding="utf-8")

        summary = (
            f"OK: 已对 {rel} 选择性应用 {accepted_hunks}/{len(normalized_edits)} 个 patch hunk，"
            f"替换 {total_replacements} 处匹配"
        )
        if skipped_hunks:
            summary += f"，跳过 {skipped_hunks} 个"

        lines = [summary, "", "[patch preview]", final_patch]
        if notes:
            lines.extend(["", "[patch notes]"])
            lines.extend(f"- {note}" for note in notes)
        return "\n".join(lines)

    def list_files(
        self,
        path: str = ".",
        glob: str = "**/*",
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> str:
        try:
            base_path = self._resolve_path(path)
        except Exception as exc:
            return f"ERROR: {exc}"

        if limit <= 0:
            limit = DEFAULT_LIST_LIMIT

        try:
            if base_path.is_file():
                matches = [base_path]
            else:
                matches = sorted(base_path.glob(glob))
        except Exception as exc:
            return f"ERROR: list_files 失败: {exc}"

        lines = []
        count = 0
        for match in matches:
            try:
                resolved = match.resolve(strict=False)
                resolved.relative_to(self.workspace_root)
            except Exception:
                continue

            suffix = "/" if match.is_dir() else ""
            lines.append(f"{_relative_display(resolved, self.workspace_root)}{suffix}")
            count += 1
            if count >= limit:
                break

        if not lines:
            return "没有匹配项"

        total_note = ""
        if len(matches) > limit:
            total_note = f"\n... [已截断，只显示前 {limit} 项]"
        return "\n".join(lines) + total_note

    def grep_text(
        self,
        pattern: str,
        path: str = ".",
        limit: int = DEFAULT_GREP_LIMIT,
    ) -> str:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return f"ERROR: 非法正则表达式: {exc}"

        try:
            base_path = self._resolve_path(path)
        except Exception as exc:
            return f"ERROR: {exc}"

        if limit <= 0:
            limit = DEFAULT_GREP_LIMIT

        if base_path.is_file():
            candidates = [base_path]
        else:
            candidates = sorted(p for p in base_path.rglob("*") if p.is_file())

        results = []
        for file_path in candidates:
            try:
                if file_path.stat().st_size > MAX_TEXT_FILE_SIZE:
                    continue
                text = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            for line_no, line in enumerate(text.splitlines(), start=1):
                if not regex.search(line):
                    continue
                rel = _relative_display(file_path, self.workspace_root)
                results.append(f"{rel}:{line_no}: {line.strip()}")
                if len(results) >= limit:
                    return "\n".join(results) + f"\n... [已截断，只显示前 {limit} 条匹配]"

        return "\n".join(results) if results else "没有匹配项"


@dataclass
class CommandToolExecutor:
    runtime: "ToolRuntime"
    workspace_root: Path
    command_timeout: int

    def run_command(self, cmd: str) -> str:
        danger = "高风险命令" if _looks_dangerous_command(cmd) else "执行命令"
        preview = f"{danger}（工作区起点: {self.workspace_root}）\n\n{cmd}"
        if not self.runtime._confirm("run_command", preview):
            return "DENIED: 用户拒绝执行命令"

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
                cwd=self.workspace_root,
            )
        except subprocess.TimeoutExpired:
            return f"ERROR: 命令执行超过 {self.command_timeout} 秒被终止"
        except Exception as exc:
            return f"ERROR: 命令执行失败: {type(exc).__name__}: {exc}"

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        combined = stdout
        if stderr:
            combined += f"\n[stderr]\n{stderr}"
        combined += f"\n[exit code] {result.returncode}"
        return _truncate(combined)


@dataclass
class ToolRuntime:
    workspace_root: Path
    auto_approve: bool = False
    command_timeout: int = 30

    def __post_init__(self) -> None:
        self.workspace_root = self.workspace_root.expanduser().resolve()
        self._tool_specs_by_name = TOOL_SPEC_MAP
        self._file_tools = FileToolExecutor(self, self.workspace_root)
        self._command_tools = CommandToolExecutor(
            self,
            self.workspace_root,
            self.command_timeout,
        )
        self._tool_handlers: dict[str, Callable[..., str]] = {
            "read_file": self._file_tools.read_file,
            "write_file": self._file_tools.write_file,
            "edit_file": self._file_tools.edit_file,
            "apply_patch": self._file_tools.apply_patch,
            "list_files": self._file_tools.list_files,
            "grep_text": self._file_tools.grep_text,
            "run_command": self._command_tools.run_command,
        }
        self.active_tool_profile = "full"
        self.active_tool_names: tuple[str, ...] = ()
        self.active_tool_specs: tuple[ToolSpec, ...] = ()
        self.tool_schemas: list[dict[str, object]] = []
        self.tool_traces: list[ToolExecutionTrace] = []
        self.scheduler_traces: list[ToolBatchTrace] = []
        self.set_tool_profile("full")

    def set_tool_profile(self, profile: str) -> None:
        if profile == "read_only":
            tool_names = READ_ONLY_TOOL_NAMES
        elif profile == "none":
            tool_names = ()
        else:
            profile = "full"
            tool_names = DEFAULT_TOOL_NAMES

        self.active_tool_profile = profile
        self.active_tool_specs = tuple(
            self._tool_specs_by_name[name]
            for name in tool_names
            if name in self._tool_specs_by_name
        )
        self.active_tool_names = tuple(spec.name for spec in self.active_tool_specs)
        self.tool_schemas = [spec.schema for spec in self.active_tool_specs]

    def update_tool_profile_for_task(
        self,
        task_text: str,
        *,
        active_goal: str = "",
    ) -> bool:
        combined = "\n".join(
            part.strip() for part in (task_text, active_goal) if part and part.strip()
        )
        next_profile = infer_tool_profile(combined)
        if next_profile == self.active_tool_profile:
            return False
        self.set_tool_profile(next_profile)
        return True

    def tool_names(self) -> set[str]:
        return set(self.active_tool_names)

    def tool_summary(self) -> str:
        return "\n".join(spec.summary_line for spec in self.active_tool_specs)

    def scheduler_snapshot(self) -> ToolSchedulerSnapshot:
        active = self.active_tool_specs
        return ToolSchedulerSnapshot(
            profile=self.active_tool_profile,
            active_tools=tuple(spec.name for spec in active),
            read_only_tools=tuple(spec.name for spec in active if spec.read_only),
            mutating_tools=tuple(spec.name for spec in active if spec.mutates_workspace),
            approval_tools=tuple(spec.name for spec in active if spec.needs_approval),
            parallel_tools=tuple(spec.name for spec in active if spec.can_parallelize),
            context_tools=tuple(spec.name for spec in active if spec.affects_context),
        )

    def scheduler_brief(self) -> str:
        snapshot = self.scheduler_snapshot()
        return (
            f"active={len(snapshot.active_tools)} "
            f"read_only={len(snapshot.read_only_tools)} "
            f"mutating={len(snapshot.mutating_tools)} "
            f"approval={len(snapshot.approval_tools)} "
            f"parallel={len(snapshot.parallel_tools)} "
            f"context={len(snapshot.context_tools)}"
        )

    def scheduler_summary(self) -> str:
        snapshot = self.scheduler_snapshot()
        lines = [
            f"tool profile: {snapshot.profile}",
            f"- active: {tool_list_label(snapshot.active_tools)}",
            f"- read_only: {tool_list_label(snapshot.read_only_tools)}",
            f"- mutating: {tool_list_label(snapshot.mutating_tools)}",
            f"- needs_approval: {tool_list_label(snapshot.approval_tools)}",
            f"- parallel_candidates: {tool_list_label(snapshot.parallel_tools)}",
            f"- affects_context: {tool_list_label(snapshot.context_tools)}",
        ]
        return "\n".join(lines)

    def tool_catalog_report(self) -> str:
        lines = ["工具目录", "", self.scheduler_summary(), "", "当前可用工具："]
        if not self.active_tool_specs:
            lines.append("(none)")
            return "\n".join(lines)

        for spec in self.active_tool_specs:
            lines.append(spec.summary_line)
            lines.append(
                f"  meta: category={spec.category}; {spec.scheduler_flags()}"
            )
        return "\n".join(lines)

    def tool_trace_summary(self, limit: int = 5) -> list[str]:
        return [
            f"- {trace.tool_name} [{trace.category}] {trace.status} {trace.duration_ms}ms "
            f"output_chars={trace.output_chars}"
            for trace in self.tool_traces[-limit:]
        ]

    def tool_batch_summary(self, limit: int = 5) -> list[str]:
        return [
            f"- {trace.mode} tools={trace.tool_count} {trace.duration_ms}ms "
            f"output_chars={trace.total_output_chars}"
            for trace in self.scheduler_traces[-limit:]
        ]

    def _record_tool_trace(
        self,
        name: str,
        *,
        status: str,
        duration_ms: int,
        output_chars: int,
    ) -> None:
        spec = self._tool_specs_by_name.get(name)
        self.tool_traces.append(
            ToolExecutionTrace(
                tool_name=name,
                category=spec.category if spec else "unknown",
                status=status,
                duration_ms=duration_ms,
                output_chars=output_chars,
                read_only=spec.read_only if spec else False,
                needs_approval=spec.needs_approval if spec else False,
            )
        )

    def _scheduler_mode_for_names(self, tool_names: tuple[str, ...]) -> str:
        if len(tool_names) > 1 and all(
            self._tool_specs_by_name.get(name) and self._tool_specs_by_name[name].read_only
            for name in tool_names
        ):
            return "read_only_batch"
        return "serial"

    def describe_tool_batch(self, tool_calls: list[dict]) -> str:
        tool_names = tuple(str(tool_call.get("name", "")) for tool_call in tool_calls)
        mode = self._scheduler_mode_for_names(tool_names)
        return f"{mode}: {tool_list_label(tool_names)}"

    def _record_scheduler_trace(
        self,
        tool_names: tuple[str, ...],
        *,
        duration_ms: int,
        results: list[str],
    ) -> None:
        specs = [self._tool_specs_by_name.get(name) for name in tool_names]
        self.scheduler_traces.append(
            ToolBatchTrace(
                mode=self._scheduler_mode_for_names(tool_names),
                tool_names=tool_names,
                tool_count=len(tool_names),
                read_only_count=sum(1 for spec in specs if spec and spec.read_only),
                mutating_count=sum(
                    1 for spec in specs if spec and spec.mutates_workspace
                ),
                duration_ms=duration_ms,
                total_output_chars=sum(len(result) for result in results),
                error_count=sum(1 for result in results if result.startswith("ERROR:")),
                denied_count=sum(1 for result in results if result.startswith("DENIED:")),
            )
        )

    def _confirm(
        self,
        action: str,
        preview: str,
        *,
        full_preview: str | None = None,
        accept_label: str = "继续",
    ) -> bool:
        if self.auto_approve:
            return True

        if not sys.stdin.isatty():
            return False

        print(f"\n[permission] {action}")
        print(_truncate(preview, limit=MAX_CONFIRM_PREVIEW_CHARS))
        if full_preview is None:
            answer = self._read_choice("[y] 继续  [n] 取消 > ")
            return answer in {"y", "yes"}

        while True:
            answer = self._read_choice(
                f"[y] {accept_label}  [p] 查看完整 patch  [n] 取消 > "
            )
            if answer in {"y", "yes"}:
                return True
            if answer in {"", "n", "no"}:
                return False
            if answer == "p":
                print("\n[full patch preview]")
                print(_truncate(full_preview, limit=MAX_FULL_PATCH_DISPLAY_CHARS))
                continue
            print("请输入 y / p / n")

    def _read_single_key(self, prompt: str) -> str | None:
        if not sys.stdin.isatty():
            return None

        try:
            import termios
            import tty
        except ImportError:
            return None

        try:
            fd = sys.stdin.fileno()
        except (AttributeError, OSError):
            return None

        sys.stdout.write(prompt)
        sys.stdout.flush()

        previous_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            char = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, previous_settings)

        if char == "\x03":
            raise KeyboardInterrupt
        if char in {"\r", "\n"}:
            shown = "ENTER"
            normalized = ""
        else:
            shown = char
            normalized = char.lower()

        sys.stdout.write(f"{shown}\n")
        sys.stdout.flush()
        return normalized

    def _read_choice(self, prompt: str) -> str:
        choice = self._read_single_key(prompt)
        if choice is not None:
            return choice
        answer = input(prompt).strip().lower()
        if not answer:
            return ""
        return answer[0]

    def _choose_patch_apply_mode(
        self,
        action: str,
        preview: str,
        *,
        full_preview: str,
        allow_hunk_review: bool,
    ) -> str:
        if self.auto_approve:
            return "apply_all"

        if not sys.stdin.isatty():
            return "deny"

        print(f"\n[permission] {action}")
        print(_truncate(preview, limit=MAX_CONFIRM_PREVIEW_CHARS))

        if not allow_hunk_review:
            while True:
                answer = self._read_choice("[y] 应用这个 patch  [p] 查看完整 patch  [n] 取消 > ")
                if answer in {"y", "yes"}:
                    return "apply_all"
                if answer in {"", "n", "no"}:
                    return "deny"
                if answer == "p":
                    print("\n[full patch preview]")
                    print(_truncate(full_preview, limit=MAX_FULL_PATCH_DISPLAY_CHARS))
                    continue
                print("请输入 y / p / n")

        while True:
            answer = self._read_choice(
                "[y] 全部应用  [h] 逐段选择  [p] 查看完整 patch  [n] 取消 > "
            )
            if answer in {"y", "yes"}:
                return "apply_all"
            if answer in {"h", "review"}:
                return "review_hunks"
            if answer in {"", "n", "no"}:
                return "deny"
            if answer == "p":
                print("\n[full patch preview]")
                print(_truncate(full_preview, limit=MAX_FULL_PATCH_DISPLAY_CHARS))
                continue
            print("请输入 y / h / p / n")

    def _choose_patch_hunk_action(
        self,
        rel_path: str,
        hunk_index: int,
        total_hunks: int,
        preview: str,
        *,
        full_preview: str,
    ) -> str:
        if self.auto_approve:
            return "apply"

        if not sys.stdin.isatty():
            return "skip"

        print(f"\n[patch hunk {hunk_index}/{total_hunks}] {rel_path}")
        print(_truncate(preview, limit=MAX_CONFIRM_PREVIEW_CHARS))

        while True:
            answer = self._read_choice(
                "[y] 应用这段  [s] 跳过  [a] 应用剩余  [p] 完整 hunk  [q] 停止并保留已接受内容 > "
            )
            if answer in {"y", "yes"}:
                return "apply"
            if answer in {"", "s", "skip"}:
                return "skip"
            if answer in {"a", "all"}:
                return "apply_rest"
            if answer in {"q", "quit"}:
                return "stop"
            if answer == "p":
                print("\n[full hunk preview]")
                print(_truncate(full_preview, limit=MAX_FULL_PATCH_DISPLAY_CHARS))
                continue
            print("请输入 y / s / a / p / q")

    def read_file(self, path: str) -> str:
        return self._file_tools.read_file(path)

    def write_file(self, path: str, content: str) -> str:
        return self._file_tools.write_file(path, content)

    def edit_file(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
    ) -> str:
        return self._file_tools.edit_file(
            path,
            old_text,
            new_text,
            replace_all=replace_all,
        )

    def apply_patch(self, path: str, edits: list[dict]) -> str:
        return self._file_tools.apply_patch(path, edits)

    def list_files(
        self,
        path: str = ".",
        glob: str = "**/*",
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> str:
        return self._file_tools.list_files(path, glob, limit)

    def grep_text(
        self,
        pattern: str,
        path: str = ".",
        limit: int = DEFAULT_GREP_LIMIT,
    ) -> str:
        return self._file_tools.grep_text(pattern, path, limit)

    def run_command(self, cmd: str) -> str:
        return self._command_tools.run_command(cmd)

    def execute_tool(self, name: str, args: dict) -> str:
        handler = self._tool_handlers.get(name)
        if handler is None:
            result = f"ERROR: 未知工具 {name}"
            self._record_tool_trace(
                name,
                status="error",
                duration_ms=0,
                output_chars=len(result),
            )
            return result
        start = perf_counter()
        try:
            result = handler(**args)
        except TypeError as exc:
            result = f"ERROR: 工具参数不合法 {name}: {exc}"
        duration_ms = int((perf_counter() - start) * 1000)
        if result.startswith("DENIED:"):
            status = "denied"
        elif result.startswith("ERROR:"):
            status = "error"
        else:
            status = "ok"
        self._record_tool_trace(
            name,
            status=status,
            duration_ms=duration_ms,
            output_chars=len(result),
        )
        return result

    def execute_tool_batch(self, tool_calls: list[dict]) -> list[str]:
        if not tool_calls:
            return []

        tool_names = tuple(str(tool_call.get("name", "")) for tool_call in tool_calls)
        start = perf_counter()
        results = [
            self.execute_tool(
                str(tool_call.get("name", "")),
                dict(tool_call.get("args", {})),
            )
            for tool_call in tool_calls
        ]
        duration_ms = int((perf_counter() - start) * 1000)
        self._record_scheduler_trace(
            tool_names,
            duration_ms=duration_ms,
            results=results,
        )
        return results


__all__ = ["ToolRuntime", "infer_tool_profile"]
