"""最小 agent loop。

架构：
    user task
      → 主循环 while True
         → 调模型（本地 Ollama，OpenAI 兼容 API）
         → 如果返回 tool_calls：执行工具 → 把结果塞回 messages → 继续循环
         → 如果只有文本：打印 → 结束

对照 claw-code/query.ts:241 的 queryLoop，只是去掉了：
  - 流式输出（为了代码简短）
  - 消息压缩（7B 模型上下文 32k 应付小任务够用）
  - 权限系统（本地工具全开）
  - skill / memory 注入（后续加）
"""

import json
import os
import re
import subprocess
import sys
import uuid
from openai import OpenAI
from tools import TOOL_SCHEMAS, EXECUTORS, execute_tool


# ===== 配置 =====
MODEL = "qwen2.5-coder:7b"
BASE_URL = "http://localhost:11434/v1"
API_KEY = "ollama"  # Ollama 不校验，随便填

# Ollama 默认 num_ctx=4096，一次 ls /tmp 就能撑爆。
# Qwen2.5 支持 32k，但 KV cache 线性增长。16k 在 M1 16GB 上是甜点。
NUM_CTX = 16384

MAX_TURNS = 20  # 防死循环
SYSTEM_PROMPT_TEMPLATE = """你是一个本地编码助手，用户在 Mac 终端里和你对话。

## 当前工作环境
- 当前目录（cwd）：{cwd}
- 当前目录内容：
{ls_output}

## 可用工具
- read_file(path)              读文件
- write_file(path, content)    写文件。会自动创建父目录。
- run_command(cmd)             执行 shell 命令

## 工具选择铁律
- 创建/修改文件 → **必须**用 write_file。不要用 `echo > file`、`cat << EOF`、`touch` 等 shell 骗招。
- 运行程序、列目录、查找文件 → 用 run_command
- 写完一个文件后运行它 → 分成两步：第 1 步 write_file，第 2 步 run_command。不要试图在一行 shell 里搞定。

## 路径规则
- cwd 是上面列出的那个。用户说"当前目录"就是它
- 用相对路径或基于 cwd 的绝对路径。严禁 /Users/yourusername/...、/path/to/... 这种占位符
- 文件不存在不要直接放弃，先用 run_command("ls") 确认实际路径

## 避免噪音命令（非常重要）
工具输出会进入下一轮对话的上下文，输出太多会挤爆上下文窗口导致任务失败。
- ❌ 不要：`ls /tmp`、`ls /`、`ls ~/Downloads`——这些目录有成千上万个文件
- ❌ 不要：`find /` 这种全盘扫描
- ❌ 不要：`cat` 超过几百行的文件
- ✅ 应该：精确操作。要创建 /tmp/test/hello.py 直接 `mkdir -p /tmp/test` 即可，不需要先 ls
- ✅ 应该：读大文件时用 `head -50 file` 或 `grep 'pattern' file`

## 写测试代码时
- 如果定义了函数（`def foo():`），记得在文件末尾实际调用它（`foo()`），否则 `python file.py` 什么都不会打印
- 或者用 `if __name__ == "__main__":` 块

## 结束条件
- 任务做完后，用**纯文字**总结你做了什么（不要再输出 JSON 格式的 tool call）
- 只有在不需要任何工具时才输出纯文字"""


def build_system_prompt() -> str:
    cwd = os.getcwd()
    try:
        ls = subprocess.run(
            ["ls", "-la", cwd], capture_output=True, text=True, timeout=5
        )
        ls_output = ls.stdout.strip()
        # 截断，避免目录太大吃 token
        lines = ls_output.splitlines()
        if len(lines) > 30:
            ls_output = "\n".join(lines[:30]) + f"\n... (共 {len(lines)} 项，已截断)"
    except Exception as e:
        ls_output = f"(ls 失败: {e})"
    return SYSTEM_PROMPT_TEMPLATE.format(cwd=cwd, ls_output=ls_output)


client = OpenAI(base_url=BASE_URL, api_key=API_KEY)


_TOOL_NAMES = set(EXECUTORS.keys())


def _find_top_level_json_objects(text: str):
    """用 raw_decode 顺序扫描，找出 text 里所有合法的顶层 JSON 对象。

    比大括号配对更健壮——能正确处理字符串里的 `{` `}`、转义等边界情况。
    """
    decoder = json.JSONDecoder()
    results = []
    i = 0
    n = len(text)
    while i < n:
        # 跳到下一个 '{'
        brace = text.find("{", i)
        if brace == -1:
            break
        try:
            obj, end = decoder.raw_decode(text, brace)
            results.append(obj)
            i = end
        except json.JSONDecodeError:
            i = brace + 1  # 不是合法 JSON 开头，继续找
    return results


def extract_fake_tool_calls(content: str):
    """小模型经常把 tool call 伪装成普通文本输出。尝试从 content 里抠出来。

    支持的格式：
      1. <tool_call>{...}</tool_call>      Qwen 官方
      2. ```json\n{...}\n```               代码块包裹
      3. 多个串联的 JSON 对象（可能被普通文字包围）

    返回 [(name, args_dict), ...]。没解析出来返回 []。
    """
    if not content:
        return []

    # 提取候选 JSON 文本片段
    fragments = []

    # 1. <tool_call> 包裹的
    for m in re.finditer(r"<tool_call>(.*?)</tool_call>", content, re.DOTALL):
        fragments.append(m.group(1))

    # 2. 代码块包裹的
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", content, re.DOTALL):
        fragments.append(m.group(1))

    # 3. 如果上面都没命中，把整个 content 当 fragment 扫描
    if not fragments:
        fragments.append(content)

    # 对每个 fragment 找出所有顶层 JSON 对象
    results = []
    for frag in fragments:
        for obj in _find_top_level_json_objects(frag):
            if not isinstance(obj, dict):
                continue
            name = obj.get("name")
            args = obj.get("arguments") or obj.get("parameters") or {}
            if name in _TOOL_NAMES and isinstance(args, dict):
                results.append((name, args))
    return results


def pretty_tool_call(name: str, args: dict) -> str:
    """把工具调用打印成人类能读的一行。"""
    args_str = json.dumps(args, ensure_ascii=False)
    if len(args_str) > 120:
        args_str = args_str[:120] + "..."
    return f"→ {name}({args_str})"


def pretty_tool_result(result: str) -> str:
    """工具结果太长就截断显示。"""
    if len(result) > 300:
        return result[:300] + f"\n... [共 {len(result)} 字符]"
    return result


def run(task: str) -> None:
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": task},
    ]

    for turn in range(1, MAX_TURNS + 1):
        print(f"\n--- turn {turn} ---")

        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            # Ollama 扩展：把 num_ctx 传下去。OpenAI SDK 的 extra_body 会透传。
            extra_body={"options": {"num_ctx": NUM_CTX}},
        )
        msg = response.choices[0].message

        # ----- 标准化 tool_calls -----
        # 优先用模型走 tool_calls 通道返回的。没有就尝试从 content 里抠伪装调用。
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append({"id": tc.id, "name": tc.function.name, "args": args})
        else:
            for name, args in extract_fake_tool_calls(msg.content):
                tool_calls.append(
                    {"id": f"fake_{uuid.uuid4().hex[:8]}", "name": name, "args": args}
                )
            if tool_calls:
                print("[note] 从文本里解析到 tool call（模型没走 tool_calls 通道）")

        # ----- 写回 assistant 消息 -----
        # 构造规范的 tool_calls 结构塞回历史，这样下一轮模型看到的对话是一致的
        assistant_msg = {"role": "assistant", "content": msg.content or ""}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["args"], ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ]
            # 如果工具调用是从 content 抠出来的，清空 content 避免模型重复输出
            if not msg.tool_calls:
                assistant_msg["content"] = ""
        messages.append(assistant_msg)

        # ----- 显示 -----
        if msg.content and not (tool_calls and not msg.tool_calls):
            print(f"[assistant] {msg.content}")

        # 没有工具调用 = 任务结束
        if not tool_calls:
            print("\n=== 任务结束 ===")
            return

        # ----- 执行工具 -----
        for tc in tool_calls:
            print(pretty_tool_call(tc["name"], tc["args"]))
            result = execute_tool(tc["name"], tc["args"])
            print(pretty_tool_result(result))

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )

    print(f"\n=== 达到最大轮数 {MAX_TURNS}，强制停止 ===")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python agent.py \"你的任务描述\"")
        print("例子: python agent.py \"列出当前目录的 py 文件\"")
        sys.exit(1)
    task = " ".join(sys.argv[1:])
    run(task)
