"""工具元数据与画像推断。"""

from __future__ import annotations

import re
from dataclasses import dataclass


READ_ONLY_PROFILE_MARKERS = (
    "读取",
    "查看",
    "列出",
    "搜索",
    "查找",
    "总结",
    "解释",
    "分析",
    "where",
    "what",
    "list ",
    "read ",
    "search",
    "grep",
    "find ",
    "summarize",
    "explain",
    "show ",
)
WRITE_PROFILE_MARKERS = (
    "修改",
    "编辑",
    "写入",
    "重写",
    "创建",
    "新增",
    "删除",
    "修复",
    "实现",
    "重构",
    "改成",
    "提交",
    "运行",
    "replace",
    "edit ",
    "write ",
    "create ",
    "delete ",
    "fix ",
    "implement",
    "refactor",
    "update ",
    "change ",
    "commit",
    "run ",
    "patch",
)


def build_function_schema(
    name: str,
    description: str,
    *,
    properties: dict[str, object] | None = None,
    required: tuple[str, ...] = (),
) -> dict[str, object]:
    parameters: dict[str, object] = {
        "type": "object",
        "properties": properties or {},
    }
    if required:
        parameters["required"] = list(required)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


@dataclass(frozen=True)
class ToolSpec:
    name: str
    summary_line: str
    schema: dict[str, object]
    category: str
    read_only: bool
    mutates_workspace: bool
    needs_approval: bool
    can_parallelize: bool
    affects_context: bool

    def scheduler_flags(self) -> str:
        flags = ["read-only" if self.read_only else "mutating"]
        flags.append("parallel" if self.can_parallelize else "serial")
        if self.needs_approval:
            flags.append("approval")
        if self.affects_context:
            flags.append("context")
        return ", ".join(flags)


@dataclass(frozen=True)
class ToolSchedulerSnapshot:
    profile: str
    active_tools: tuple[str, ...]
    read_only_tools: tuple[str, ...]
    mutating_tools: tuple[str, ...]
    approval_tools: tuple[str, ...]
    parallel_tools: tuple[str, ...]
    context_tools: tuple[str, ...]


def infer_tool_profile(task_text: str) -> str:
    normalized = re.sub(r"\s+", " ", task_text.strip().lower())
    if not normalized:
        return "full"
    if any(marker in normalized for marker in WRITE_PROFILE_MARKERS):
        return "full"
    if any(marker in normalized for marker in READ_ONLY_PROFILE_MARKERS):
        return "read_only"
    return "full"


def tool_list_label(names: tuple[str, ...]) -> str:
    return ", ".join(names) if names else "(none)"


TOOL_REGISTRY = (
    ToolSpec(
        name="read_file",
        summary_line="- read_file(path): 读取工作区内文本文件",
        schema=build_function_schema(
            "read_file",
            "读取工作区内文本文件内容。",
            properties={
                "path": {"type": "string", "description": "文件路径"},
            },
            required=("path",),
        ),
        category="filesystem",
        read_only=True,
        mutates_workspace=False,
        needs_approval=False,
        can_parallelize=True,
        affects_context=False,
    ),
    ToolSpec(
        name="write_file",
        summary_line="- write_file(path, content): 整文件写入，适合新建或重写文件",
        schema=build_function_schema(
            "write_file",
            "写入文本文件，覆盖已有内容。适合创建新文件或整文件重写。执行前通常会询问用户确认。",
            properties={
                "path": {"type": "string", "description": "文件路径"},
                "content": {
                    "type": "string",
                    "description": "要写入的完整内容",
                },
            },
            required=("path", "content"),
        ),
        category="filesystem",
        read_only=False,
        mutates_workspace=True,
        needs_approval=True,
        can_parallelize=False,
        affects_context=True,
    ),
    ToolSpec(
        name="edit_file",
        summary_line="- edit_file(path, old_text, new_text, replace_all=False): 精确替换文件中的一段文本",
        schema=build_function_schema(
            "edit_file",
            "按精确文本片段编辑文件。适合局部修改，不用整文件重写。",
            properties={
                "path": {"type": "string", "description": "文件路径"},
                "old_text": {
                    "type": "string",
                    "description": "要替换的原始文本片段，必须与文件内容完全匹配",
                },
                "new_text": {
                    "type": "string",
                    "description": "替换后的文本片段",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "是否替换全部匹配项，默认 false",
                },
            },
            required=("path", "old_text", "new_text"),
        ),
        category="filesystem",
        read_only=False,
        mutates_workspace=True,
        needs_approval=True,
        can_parallelize=False,
        affects_context=True,
    ),
    ToolSpec(
        name="apply_patch",
        summary_line="- apply_patch(path, edits): 一次应用多个精确文本替换，适合多处局部改动",
        schema=build_function_schema(
            "apply_patch",
            "对同一个文件按顺序应用多个精确文本替换。适合一次完成多处局部修改。",
            properties={
                "path": {"type": "string", "description": "文件路径"},
                "edits": {
                    "type": "array",
                    "description": "按顺序应用的编辑列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_text": {
                                "type": "string",
                                "description": "要替换的原始文本片段",
                            },
                            "new_text": {
                                "type": "string",
                                "description": "替换后的文本片段",
                            },
                            "replace_all": {
                                "type": "boolean",
                                "description": "是否替换全部匹配项，默认 false",
                            },
                        },
                        "required": ["old_text", "new_text"],
                    },
                },
            },
            required=("path", "edits"),
        ),
        category="patch",
        read_only=False,
        mutates_workspace=True,
        needs_approval=True,
        can_parallelize=False,
        affects_context=True,
    ),
    ToolSpec(
        name="list_files",
        summary_line="- list_files(path='.', glob='**/*', limit=200): 列目录或文件",
        schema=build_function_schema(
            "list_files",
            "列出工作区内的文件或目录，适合做轻量探索。",
            properties={
                "path": {
                    "type": "string",
                    "description": "起始目录或文件，默认当前工作区根目录",
                },
                "glob": {
                    "type": "string",
                    "description": "glob 模式，例如 **/*.py",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回多少项，默认 200",
                },
            },
        ),
        category="discovery",
        read_only=True,
        mutates_workspace=False,
        needs_approval=False,
        can_parallelize=True,
        affects_context=False,
    ),
    ToolSpec(
        name="grep_text",
        summary_line="- grep_text(pattern, path='.', limit=50): 搜索文本",
        schema=build_function_schema(
            "grep_text",
            "在工作区内搜索文本，返回 file:line 片段。",
            properties={
                "pattern": {
                    "type": "string",
                    "description": "Python 正则表达式",
                },
                "path": {
                    "type": "string",
                    "description": "待搜索的目录或文件，默认整个工作区",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回多少条匹配，默认 50",
                },
            },
            required=("pattern",),
        ),
        category="discovery",
        read_only=True,
        mutates_workspace=False,
        needs_approval=False,
        can_parallelize=True,
        affects_context=False,
    ),
    ToolSpec(
        name="run_command",
        summary_line="- run_command(cmd): 在工作区根目录执行 shell，执行前会请求确认",
        schema=build_function_schema(
            "run_command",
            "在工作区根目录执行 shell 命令，返回 stdout/stderr。执行前通常会询问用户确认。",
            properties={
                "cmd": {
                    "type": "string",
                    "description": "完整 shell 命令",
                },
            },
            required=("cmd",),
        ),
        category="command",
        read_only=False,
        mutates_workspace=True,
        needs_approval=True,
        can_parallelize=False,
        affects_context=True,
    ),
)
TOOL_SPEC_MAP = {spec.name: spec for spec in TOOL_REGISTRY}
DEFAULT_TOOL_NAMES = tuple(spec.name for spec in TOOL_REGISTRY)
READ_ONLY_TOOL_NAMES = tuple(spec.name for spec in TOOL_REGISTRY if spec.read_only)


__all__ = [
    "DEFAULT_TOOL_NAMES",
    "READ_ONLY_TOOL_NAMES",
    "TOOL_REGISTRY",
    "TOOL_SPEC_MAP",
    "ToolSchedulerSnapshot",
    "ToolSpec",
    "infer_tool_profile",
    "tool_list_label",
]
