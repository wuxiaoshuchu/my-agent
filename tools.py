"""工具定义 + 执行器。

每个工具两部分：
1. SCHEMA：给模型看的，告诉它这个工具能做什么、参数是什么
2. 执行函数：实际被调用时跑的 Python 代码
"""

import subprocess
from pathlib import Path


# ===== 输出截断策略 =====
# 工具输出太长会：
#   1. 撑爆模型上下文窗口（num_ctx）
#   2. 即使塞得下，也浪费 token / 拖慢推理
# 策略：头 + 尾 + 中间省略。保留关键信息（开头是什么，结尾有没有错误）
MAX_TOOL_OUTPUT_CHARS = 3000


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    """超过 limit 就保留前 2/3 + 后 1/3，中间写省略提示。"""
    if len(text) <= limit:
        return text
    head_len = limit * 2 // 3
    tail_len = limit - head_len
    omitted = len(text) - limit
    head = text[:head_len]
    tail = text[-tail_len:]
    return (
        head
        + f"\n\n... [已省略 {omitted} 字符。如需完整内容请用更精确的命令，例如 grep / head / sed] ...\n\n"
        + tail
    )


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定路径文件的内容。返回文件全文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件绝对路径或相对当前目录的路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "把内容写入文件，覆盖已有内容。父目录会自动创建。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "要写入的完整内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "在终端执行 shell 命令，返回 stdout + stderr。"
                "适合：列目录、查找文件、运行脚本、git 操作。"
                "超时 30 秒。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "完整 shell 命令"}
                },
                "required": ["cmd"],
            },
        },
    },
]


def read_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"ERROR: 文件不存在 {p}"
    if p.is_dir():
        return f"ERROR: 这是目录不是文件 {p}"
    try:
        return _truncate(p.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return f"ERROR: 文件不是 UTF-8 文本 {p}"


def write_file(path: str, content: str) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"OK: 写入 {len(content)} 字符到 {p}"


def run_command(cmd: str) -> str:
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "ERROR: 命令执行超过 30 秒被终止"

    out = result.stdout or ""
    err = result.stderr or ""
    combined = out
    if err:
        combined += f"\n[stderr]\n{err}"
    combined += f"\n[exit code] {result.returncode}"
    return _truncate(combined)


EXECUTORS = {
    "read_file": read_file,
    "write_file": write_file,
    "run_command": run_command,
}


def execute_tool(name: str, args: dict) -> str:
    """根据工具名分发到对应执行函数。"""
    if name not in EXECUTORS:
        return f"ERROR: 未知工具 {name}"
    try:
        return EXECUTORS[name](**args)
    except TypeError as e:
        return f"ERROR: 参数错误 {e}"
    except Exception as e:
        return f"ERROR: 执行失败 {type(e).__name__}: {e}"
