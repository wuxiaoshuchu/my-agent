"""Git workspace inspection helpers for Jarvis."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


STATUS_CACHE_TTL_SECONDS = 0.35
UNTRACKED_PREVIEW_CHARS = 3000


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [输出过长，已截断，共 {len(text)} 字符]"


@dataclass
class RepoStatusSnapshot:
    in_repo: bool
    branch: str = "-"
    tracking: str | None = None
    ahead: int = 0
    behind: int = 0
    staged: int = 0
    modified: int = 0
    untracked: int = 0

    @property
    def clean(self) -> bool:
        return self.in_repo and self.staged == 0 and self.modified == 0 and self.untracked == 0

    @property
    def total_changes(self) -> int:
        return self.staged + self.modified + self.untracked


@dataclass
class GitStatusData:
    in_repo: bool
    raw_output: str = ""
    branch_line: str = "## unknown"
    tracking: str | None = None
    ahead: int = 0
    behind: int = 0
    status_lines: list[str] | None = None
    staged: int = 0
    modified: int = 0
    untracked: int = 0

    def snapshot(self) -> RepoStatusSnapshot:
        return RepoStatusSnapshot(
            in_repo=self.in_repo,
            branch=self.branch,
            tracking=self.tracking,
            ahead=self.ahead,
            behind=self.behind,
            staged=self.staged,
            modified=self.modified,
            untracked=self.untracked,
        )

    @property
    def branch(self) -> str:
        text = self.branch_line.removeprefix("## ").strip()
        if "..." in text:
            return text.split("...", 1)[0] or "unknown"
        return text or "unknown"


class WorkspaceInspector:
    def __init__(self, workspace_root: Path, *, status_cache_ttl: float = STATUS_CACHE_TTL_SECONDS):
        self.workspace_root = workspace_root
        self.status_cache_ttl = status_cache_ttl
        self._status_cache: tuple[float, GitStatusData] | None = None
        self._remote_cache: tuple[float, str | None] | None = None

    def invalidate_cache(self) -> None:
        self._status_cache = None
        self._remote_cache = None

    def _run_git(self, *args: str) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except FileNotFoundError:
            return False, "git 不可用"
        except subprocess.TimeoutExpired:
            return False, "git 命令超时"

        output = (result.stdout or "").rstrip("\n")
        error = (result.stderr or "").strip()
        if result.returncode != 0:
            return False, error or output or f"git 命令失败: {' '.join(args)}"
        return True, output

    def _parse_branch_line(self, branch_line: str) -> tuple[str, str | None, int, int]:
        text = branch_line.removeprefix("## ").strip()
        ahead = 0
        behind = 0

        bracket = ""
        if " [" in text and text.endswith("]"):
            text, bracket = text.rsplit(" [", 1)
            bracket = bracket[:-1]

        tracking = None
        branch = text
        if "..." in text:
            branch, tracking = text.split("...", 1)

        if bracket:
            for chunk in bracket.split(","):
                chunk = chunk.strip()
                if chunk.startswith("ahead "):
                    try:
                        ahead = int(chunk.split()[1])
                    except (IndexError, ValueError):
                        ahead = 0
                if chunk.startswith("behind "):
                    try:
                        behind = int(chunk.split()[1])
                    except (IndexError, ValueError):
                        behind = 0

        return branch or "unknown", tracking, ahead, behind

    def _status_data(self, *, force: bool = False) -> GitStatusData:
        now = time.monotonic()
        if not force and self._status_cache is not None:
            cached_at, cached = self._status_cache
            if now - cached_at <= self.status_cache_ttl:
                return cached

        ok, output = self._run_git("status", "--short", "--branch")
        if not ok:
            data = GitStatusData(in_repo=False)
            self._status_cache = (now, data)
            return data

        lines = output.splitlines()
        branch_line = lines[0] if lines else "## unknown"
        _, tracking, ahead, behind = self._parse_branch_line(branch_line)

        status_lines = lines[1:]
        staged = 0
        modified = 0
        untracked = 0
        for line in status_lines:
            code = line[:2]
            if code == "??":
                untracked += 1
                continue
            if code and code[0] != " ":
                staged += 1
            if len(code) > 1 and code[1] != " ":
                modified += 1

        data = GitStatusData(
            in_repo=True,
            raw_output=output,
            branch_line=branch_line,
            tracking=tracking,
            ahead=ahead,
            behind=behind,
            status_lines=status_lines,
            staged=staged,
            modified=modified,
            untracked=untracked,
        )
        self._status_cache = (now, data)
        return data

    def _remote_url(self, *, force: bool = False) -> str | None:
        if not self.is_git_repo():
            return None

        now = time.monotonic()
        if not force and self._remote_cache is not None:
            cached_at, cached = self._remote_cache
            if now - cached_at <= self.status_cache_ttl:
                return cached

        ok, remote = self._run_git("remote", "get-url", "origin")
        value = remote if ok and remote else None
        self._remote_cache = (now, value)
        return value

    def is_git_repo(self) -> bool:
        return self._status_data().in_repo

    def status_lines(self) -> list[str]:
        data = self._status_data()
        if not data.in_repo or not data.status_lines:
            return []
        return list(data.status_lines)

    def is_clean(self) -> bool:
        return self.status_snapshot().clean

    def changed_paths(self) -> list[str]:
        paths: list[str] = []
        for line in self.status_lines():
            body = line[3:].strip() if len(line) >= 4 else line.strip()
            if " -> " in body:
                body = body.split(" -> ", 1)[1]
            if body:
                paths.append(body)
        return paths

    def status_snapshot(self) -> RepoStatusSnapshot:
        return self._status_data().snapshot()

    def branch_report(self) -> str:
        data = self._status_data()
        if not data.in_repo:
            return "当前工作区不是 Git 仓库。"

        remote = self._remote_url()
        if remote:
            return f"{data.branch_line}\norigin: {remote}"
        return data.branch_line

    def status_report(self) -> str:
        data = self._status_data()
        if not data.in_repo:
            return "当前工作区不是 Git 仓库。"

        remote = self._remote_url()
        if remote:
            return f"{data.raw_output}\norigin: {remote}"
        return data.raw_output

    def diff_report(self, *, target: str | None = None, stat_only: bool = False) -> str:
        if not self.is_git_repo():
            return "当前工作区不是 Git 仓库。"

        args = ["diff"]
        if stat_only:
            args.append("--stat")
        if target:
            args.extend(["--", target])

        ok, diff = self._run_git(*args)
        if ok and diff:
            return diff

        if target and self._is_untracked_path(target):
            preview_path = (self.workspace_root / target).resolve(strict=False)
            if preview_path.exists() and preview_path.is_file():
                preview = preview_path.read_text(encoding="utf-8")
                return (
                    f"{target} 还没有被 Git 跟踪，所以 `git diff` 不会显示它。\n\n"
                    f"[untracked file preview]\n{preview[:4000]}"
                )
            return f"{target} 还没有被 Git 跟踪，所以 `git diff` 不会显示它。"

        if stat_only:
            short_status = self.status_lines()
            if short_status:
                return f"(git diff --stat 没有输出)\n\n当前工作区变更：\n" + "\n".join(short_status)
        return diff or "没有可显示的 diff。"

    def patch_report(self, target: str | None = None) -> str:
        if not self.is_git_repo():
            return "当前工作区不是 Git 仓库。"

        if target:
            return self.diff_report(target=target, stat_only=False)

        tracked = self.diff_report(stat_only=False)
        sections: list[str] = []
        if tracked and tracked != "没有可显示的 diff。":
            sections.append(tracked)

        for line in self.status_lines():
            if not line.startswith("?? "):
                continue
            path = line[3:].strip()
            preview_path = (self.workspace_root / path).resolve(strict=False)
            if preview_path.is_dir():
                sections.append(f"[untracked dir] {path}/")
                continue
            try:
                preview = preview_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                sections.append(f"[untracked file] {path}")
                continue

            sections.append(
                "\n".join(
                    [
                        "--- /dev/null",
                        f"+++ b/{path}",
                        "[untracked file preview]",
                        _truncate_text(preview, limit=UNTRACKED_PREVIEW_CHARS),
                    ]
                )
            )

        if not sections:
            return "当前没有 patch 可预览。"
        return "\n\n".join(sections)

    def suggest_commit_message(self) -> str:
        paths = self.changed_paths()
        if not paths:
            return "chore: update project"

        if any(path.startswith(".vscode/") for path in paths):
            return "chore: add vscode jarvis workflow"
        if "agent.py" in paths and any(path.startswith("tests/") for path in paths):
            return "feat: improve jarvis workflow"
        if any(path in {"README.md", "CHANGELOG.md", "HARNESS.md"} for path in paths):
            return "docs: update project guidance"
        if len(paths) == 1:
            stem = Path(paths[0]).stem.replace("_", " ")
            return f"chore: update {stem}"
        return "chore: update project"

    def commit_all(self, message: str) -> tuple[bool, str]:
        if not self.is_git_repo():
            return False, "当前工作区不是 Git 仓库。"
        if self.is_clean():
            return False, "当前没有可提交的变更。"

        ok, output = self._run_git("add", "-A")
        self.invalidate_cache()
        if not ok:
            return False, output

        ok, output = self._run_git("commit", "-m", message)
        self.invalidate_cache()
        if not ok:
            return False, output
        return True, output

    def _is_untracked_path(self, target: str) -> bool:
        normalized = target.rstrip("/")
        for line in self.status_lines():
            if not line.startswith("?? "):
                continue
            path = line[3:].strip().rstrip("/")
            if path == normalized:
                return True
        return False
