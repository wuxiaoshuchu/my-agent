# my-agent

本地跑的最小 agent loop。Ollama + Qwen2.5-Coder-7B。

## 前置条件

```bash
brew install ollama
ollama serve &
ollama pull qwen2.5-coder:7b
```

## 安装

```bash
cd ~/Desktop/my-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 运行

```bash
python agent.py "列出当前目录下所有 .py 文件"
python agent.py "读取 tools.py 总结里面有几个函数"
python agent.py "在 /tmp 下创建 hello.txt 写入 hi"
```

## 文件说明

| 文件 | 作用 |
|---|---|
| `agent.py` | 主循环。调模型、分发工具调用、维护 messages。 |
| `tools.py` | 工具定义 + 执行器。目前 3 个：read_file / write_file / run_command。 |

## 架构

```
user task
  ↓
while True:
  response = model.chat(messages, tools)
  messages += assistant_reply
  if no tool_calls: break          ← 任务完成
  for each tool_call:
    result = execute(tool)
    messages += tool_result
```

对照 claw-code/query.ts:241 的 queryLoop，是它的极简版。去掉了流式、压缩、权限、skill。

## 换模型

改 `agent.py` 顶部的 `MODEL` 常量。例如换 14B：

```bash
ollama pull qwen2.5-coder:14b
# 然后把 agent.py 里 MODEL = "qwen2.5-coder:14b"
```

## 换到 MLX

Ollama 稳定后，换 MLX 只需要：

1. `pip install mlx-lm`
2. `python -m mlx_lm.server --model mlx-community/Qwen2.5-Coder-7B-Instruct-4bit`
3. 把 `agent.py` 里 `BASE_URL` 改成 MLX server 的地址（默认 `http://localhost:8080/v1`）

其他代码不用动。
