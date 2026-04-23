# my-agent

本地跑的最小编码 agent。默认走 Ollama 的 OpenAI 兼容接口，也可以换成别的兼容服务。

现在它已经可以被安装成一个真正的命令：`jarvis`。

这一版已经不只是“一次性脚本”了，而是一个带基础产品感的 CLI：

- 支持 `REPL` 多轮会话
- 有 `Session` 级别的消息历史
- 增加了 `list_files` / `grep_text`，更像真正的代码助手
- 写文件、执行命令前会先请求确认
- 文件读写限制在工作区里，减少误操作
- 增加了 `/status` `/branch` `/diff` `/history`，终于能看见自己做了什么
- 增加了 `/summary` 和 `/commit`，可以回看本轮成果并直接提交
- 仓库现在有 [HARNESS.md](HARNESS.md) 和 [CHANGELOG.md](CHANGELOG.md)，方便 agent 继承规则和回看成长史
- 自带 `.vscode` 配置，可以在 VS Code 里一键启动 `jarvis`
- REPL 现在有启动 banner、Git 状态头和动态提示符，更接近真正的 CLI 工具
- 增加了 `edit_file` 工具和 `/patch` 命令，可以做局部编辑并直接预览改动
- 增加了 `apply_patch` 工具，并且多 hunk patch 现在支持带状态面板的逐段审批

## 前置条件

```bash
brew install ollama
ollama serve &
ollama pull qwen2.5-coder:7b
```

## 安装

```bash
cd ~/Desktop/my-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

或者直接运行开发安装脚本：

```bash
./scripts/dev-install.sh
```

## 运行

### 1. 进入 REPL

```bash
jarvis
```

启动后你会直接看到：

- `jarvis` banner
- 当前 `workspace`
- 当前 `model`
- 当前 Git 分支 / ahead / dirty 状态
- 输入提示符里的仓库状态，例如：

```text
jarvis [main +2 m3 ask]>
```

或指定工作区：

```bash
jarvis --cwd ~/Desktop/claw-code
```

### 2. 执行一次性任务

```bash
jarvis "列出当前工作区里所有 ts 文件"
jarvis "搜索 queryLoop 在哪里定义"
jarvis "读取 tools.py，总结里面有哪些工具"
```

### 3. 跳过确认提示

```bash
jarvis --auto-approve "创建 hello.py 并运行它"
```

### 4. 如果你想继续保留脚本方式

```bash
python agent.py
```

## REPL 命令

```text
/help   查看帮助
/tools  查看工具说明
/pwd    显示当前工作区根目录
/status 查看当前 Git 状态
/branch 查看当前分支
/diff   查看当前 diff
/diff --stat 查看 diff 摘要
/diff path/to/file 查看单文件 diff
/patch [path] 预览当前 patch
/summary [N] 查看本轮摘要
/commit [message] 提交当前变更
/history [N] 查看最近会话动作
/approve [on|off|status] 切换或查看审批模式
/clear  清空会话历史
/quit   退出
```

## VS Code 里启动

仓库已经带了：

- [.vscode/launch.json](.vscode/launch.json)
- [.vscode/tasks.json](.vscode/tasks.json)

在 VS Code 里你可以直接：

1. `Run and Debug` 里选择 `Jarvis REPL`
2. 或者运行任务 `Jarvis: REPL`
3. 首次没装依赖时，先运行任务 `Jarvis: Install`

## 当前工具

| 工具 | 用途 |
|---|---|
| `read_file(path)` | 读取文本文件 |
| `write_file(path, content)` | 整文件写入 |
| `edit_file(path, old_text, new_text, replace_all=False)` | 精确替换文件中的一段文本 |
| `apply_patch(path, edits)` | 一次应用多个精确文本替换 |
| `list_files(path='.', glob='**/*', limit=200)` | 列出文件 / 目录 |
| `grep_text(pattern, path='.', limit=50)` | 搜索文本 |
| `run_command(cmd)` | 执行 shell 命令 |

## 什么是 patch 预览

`patch preview` 不是“模型脑内推理过程可视化”，而是更实用的一层：

- 它会直接展示文件哪些行被删掉、哪些行被新增
- 你能看到 agent 这次到底改了什么
- 它和 `/history` 配合起来，就能同时回答：
  - agent 做了哪些动作
  - 每次动作具体改了哪些代码
- 现在编辑工具在真正写入前也会先展示 `patch preview before apply`，你确认后才会落盘
- 对多 hunk patch，现在会先提示：
  - `y`：一次性应用全部 patch
  - `h`：进入逐段审批
  - `p`：查看更完整的 patch
  - `n`：取消
- 进入逐段审批后，每一段 hunk 都可以：
  - `y`：应用这段
  - `s`：跳过这段
  - `a`：应用这段和剩余 hunk
  - `p`：查看这一段更完整的 patch
  - `q`：结束审批并保留已经接受的改动
- 审批界面现在会先显示一个终端状态面板，里面有：
  - 当前文件
  - `+/-` 增删统计
  - 计划编辑数或当前进度
  - 这一屏可用的动作提示
- 逐段审批是按顺序计算的，后续 hunk 会基于当前已经接受的结果继续判断是否还能应用

常用方式：

```bash
/patch
/patch agent.py
```

## 架构

```text
user input
  ↓
AgentSession
  ↓
while loop:
  call model(messages, tools)
  append assistant reply
  if tool_calls:
    execute tool
    append tool result
    continue
  else:
    finish turn
```

## 从 claw-code 借鉴了什么

这版明确借了 `claw-code` 的几个方向，但还没做那么重：

- 主循环和工具执行分离
- 不依赖 `stop_reason == "tool_use"` 作为唯一判断
- 给模型更多“少噪音、先用专用工具”的约束
- 把 agent 做成一个会持续持有 `messages` 的 session，而不是一次性函数

还没做的包括：流式输出、真正的权限系统、并发工具调度、compact、memory、sub-agent。

## 像 Claude 那样启动

如果你在 IDE 的集成终端里：

```bash
cd ~/Desktop/my-agent
source .venv/bin/activate
jarvis
```

那么体验就已经会很接近 `claude` 这种命令行工具了。

如果你想做到“任何终端里都能直接输入 `jarvis`，甚至不用手动激活 venv”，下一步可以再加一个全局安装或 shell alias。

## 换模型

```bash
jarvis --model qwen2.5-coder:14b
```

如果换成别的 OpenAI 兼容服务，也可以改：

```bash
jarvis \
  --base-url http://localhost:8080/v1 \
  --api-key dummy \
  --model your-model-name
```
