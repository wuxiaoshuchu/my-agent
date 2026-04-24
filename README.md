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
- 增加了最小 `context engine`：会估算消息/tokens，并支持 `session memory + /compact`
- 仓库现在有 [HARNESS.md](HARNESS.md) 和 [CHANGELOG.md](CHANGELOG.md)，方便 agent 继承规则和回看成长史
- 仓库现在还有 [way-to-claw-code.md](way-to-claw-code.md)，用于记录长期路线图和后续待办
- 仓库现在还有 [jarvis.config.json](jarvis.config.json) 和 [model-baseline.md](model-baseline.md)，用于固定默认模型和记录本机模型基线
- 仓库现在还有 benchmark 任务集和结果目录，可以开始比较不同本地模型的实际表现
- 仓库现在还有 context regression harness，可以稳定回归 `compact / session memory` 行为
- 仓库现在还有 live context regression harness，可以在真实模型上回归 `compact / active goal / full-stack tool use`
- 自带 `.vscode` 配置，可以在 VS Code 里一键启动 `jarvis`
- REPL 现在有启动 banner、Git 状态头和动态提示符，更接近真正的 CLI 工具
- 增加了 `edit_file` 工具和 `/patch` 命令，可以做局部编辑并直接预览改动
- 增加了 `apply_patch` 工具，并且多 hunk patch 现在支持带状态面板和单键操作的逐段审批
- Git 工作区检查现在拆成了独立模块，并对高频状态查询做了轻量缓存

## 前置条件

```bash
brew install ollama
ollama serve &
ollama pull qwen2.5-coder:7b
```

## 默认运行时配置

仓库根目录现在有一个 [jarvis.config.json](jarvis.config.json)：

```json
{
  "model": "qwen2.5-coder:7b",
  "base_url": "http://localhost:11434/v1",
  "num_ctx": 16384
}
```

`jarvis` 启动时会按这个顺序解析运行时设置：

1. CLI 参数
2. 工作区里的 `jarvis.config.json`
3. 内置默认值

所以现在你不需要每次都手写 `--model`，而且也能把默认模型真正保存进仓库。

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

如果你只想看当前模型配置和本地已安装模型：

```text
/model
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
/model  查看或切换模型配置
/compact 压缩较早会话历史
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

`/model` 支持：

```text
/model
/model use qwen2.5-coder:14b
/model set qwen2.5-coder:14b
/model ctx 24576
```

- `/model`：显示当前模型、来源、base URL、num_ctx 和本地 Ollama 模型列表
- `/model use`：只切换当前会话
- `/model set`：切换并写入 `jarvis.config.json`
- `/model ctx`：更新默认上下文窗口并写入 `jarvis.config.json`

`/compact` 会做一版最小上下文压缩：

- 保留最近几个 turn 不动
- 把更早的对话折叠成 `session memory`
- 保留当前 active goal
- 重新把 `HARNESS.md`、`way-to-claw-code.md` 和 compact 后的记忆一起注入 system prompt
- 会尽量过滤掉 fake tool call JSON 这类容易污染后续模型行为的摘要内容
- 如果用户只说“继续 / continue”这类低信息 follow-up，会沿用原任务主线，而不是把 active goal 覆盖成“继续”

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
- patch 审批现在默认是单键操作，不需要每次再按一次回车
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
- Git 观察层开始独立成模块，不再全部堆在 `agent.py`
- 不依赖 `stop_reason == "tool_use"` 作为唯一判断
- 给模型更多“少噪音、先用专用工具”的约束
- 把 agent 做成一个会持续持有 `messages` 的 session，而不是一次性函数
- 增加了最小 `context engine`，让长任务不再只能无限堆消息

还没做的包括：流式输出、更高质量的 compact 摘要、真正的权限系统、并发工具调度、sub-agent。

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

如果你希望把默认模型直接改进仓库，不想每次都传 CLI 参数，直接在 REPL 里：

```text
/model set qwen2.5-coder:14b
```

不过基于当前这台 `M1 + 16GB` 机器的真实结果，`qwen2.5-coder:14b` 现在不建议直接设成默认模型。更具体的结论已经写在 [model-baseline.md](model-baseline.md)。

如果换成别的 OpenAI 兼容服务，也可以改：

```bash
jarvis \
  --base-url http://localhost:8080/v1 \
  --api-key dummy \
  --model your-model-name
```

如果你想回看这台机器的模型建议和当前基线，直接看 [model-baseline.md](model-baseline.md)。

## 模型 benchmark

仓库现在自带：

- [benchmarks/agent_tasks.json](benchmarks/agent_tasks.json)：默认 benchmark 任务集
- [scripts/benchmark_agent.py](scripts/benchmark_agent.py)：运行 benchmark 的脚本
- [benchmark-results/](benchmark-results/)：保存每次 benchmark 输出的 json / markdown

跑一轮当前模型：

```bash
./.venv/bin/python scripts/benchmark_agent.py --models qwen2.5-coder:7b
```

如果你想比较多个模型：

```bash
./.venv/bin/python scripts/benchmark_agent.py \
  --models qwen2.5-coder:7b qwen2.5-coder:14b deepseek-coder-v2:16b
```

如果你担心某个模型卡住，可以加：

```bash
./.venv/bin/python scripts/benchmark_agent.py \
  --models qwen2.5-coder:7b \
  --request-timeout 45
```

这个 benchmark 目前测的是“agent 在真实仓库里完成只读任务的表现”，会记录：

- 每个任务总耗时
- 通过 / 未通过
- 用了多少次工具
- 用了哪些工具
- 最终回答文本

所以它不是只测裸模型，而是测“当前 jarvis + 当前模型 + 当前任务集”的组合。

脚本现在也会逐任务打印进度，方便看见它到底卡在哪一类任务上。

当前首份真实结果已经在：

- [benchmark-results/2026-04-23_233206_qwen2-5-coder-7b.md](/Users/wuxiaoshuchu/Desktop/my-agent/benchmark-results/2026-04-23_233206_qwen2-5-coder-7b.md)
- [benchmark-results/2026-04-24_005305_qwen2-5-coder-14b.md](/Users/wuxiaoshuchu/Desktop/my-agent/benchmark-results/2026-04-24_005305_qwen2-5-coder-14b.md)

当前最重要的对比结论是：

- `7b` 在 `20s` benchmark 下会超时，但在更长超时里至少能完成真实 agent 任务
- `14b` 在这台机器上的直连延迟波动很大：warm 大约 `3.9s`，冷态样本到过 `40s`
- `14b` 带完整 `jarvis` prompt + tools 时，长任务有时要 `169s` 才能完成，有时只吐一个 fake tool call
- 所以当前不建议把默认模型切到 `14b`

## Context 回归

仓库现在还自带：

- [benchmarks/context_regression_cases.json](benchmarks/context_regression_cases.json)：`P1` 上下文层的固定回归样本
- [context_regression_harness.py](context_regression_harness.py)：回归运行与报告逻辑
- [scripts/regress_context_engine.py](scripts/regress_context_engine.py)：一键运行脚本
- [context-regression-results/](context-regression-results/)：保存每次回归产出的 json / markdown

跑一轮当前回归集：

```bash
python3 scripts/regress_context_engine.py
```

它现在重点盯三类风险：

- fake tool call 变体还能不能被接住
- `continue / 继续` 这类低信息 follow-up 会不会把 active goal 冲掉
- 自动 compact 的阈值和 kept recent turns 有没有按预期工作

当前首份正式结果已经在：

- [context-regression-results/2026-04-24_093644.md](/Users/wuxiaoshuchu/Desktop/my-agent/context-regression-results/2026-04-24_093644.md)

## Live Context 回归

为了把 `P1` 从 deterministic harness 推进到真实模型，这个仓库现在还带了：

- [benchmarks/context_live_tasks.json](benchmarks/context_live_tasks.json)：live model 的 `P1` 回归样本
- [context_live_regression.py](context_live_regression.py)：live 回归运行与报告逻辑
- [scripts/regress_context_live.py](scripts/regress_context_live.py)：一键运行 live context 回归
- [context-live-results/](context-live-results/)：保存 live 回归的 json / markdown 结果

跑一轮当前 live 回归集：

```bash
python3 scripts/regress_context_live.py --model qwen2.5-coder:7b
```

这套 live case 现在分成两层：

- `goal-only`：显式关闭工具 schema，只验证 compact 后还能不能沿用当前任务目标
- `full-stack`：继续保留真实工具链任务，验证 compact 后还能不能继续 `read_file`

这样我们就能把“上下文层真的坏了”和“模型在这台机器上太慢”分开看。

当前第一轮 live 结果已经说明了两件事：

- `goal-only` case 是接下来最适合继续压实 `P1` 的主样本
- `full-stack` case 在 `qwen2.5-coder:7b` 上目前仍然容易碰到 `120s` 超时，所以它更像 `P1 + P0` 的联合压力测试

当前已经沉淀了 3 份代表性结果：

- [context-live-results/2026-04-24_101142_qwen2.5-coder-7b.md](/Users/wuxiaoshuchu/Desktop/my-agent/context-live-results/2026-04-24_101142_qwen2.5-coder-7b.md)
  - `compacted_goal_resume_direct`
  - 触发了 `1` 次 compact
  - 不走 tools
  - 在 `101s` 左右成功输出 `CONTEXT_GOAL_OK`
- [context-live-results/2026-04-24_101333_qwen2.5-coder-7b.md](/Users/wuxiaoshuchu/Desktop/my-agent/context-live-results/2026-04-24_101333_qwen2.5-coder-7b.md)
  - `compacted_goal_resume_continue`
  - 触发了 `1` 次 compact
  - 用户只说“继续”时仍然沿用了 active goal
  - 在 `100s` 左右成功输出 `CONTEXT_GOAL_OK`
- [context-live-results/2026-04-24_100052_qwen2.5-coder-7b.md](/Users/wuxiaoshuchu/Desktop/my-agent/context-live-results/2026-04-24_100052_qwen2.5-coder-7b.md)
  - 两条 `full-stack` 任务都在 `120s` 左右超时
  - compact 能触发，但还没进入 `read_file`

## 运行时诊断

如果你怀疑问题不是 benchmark 脚手架，而是本机 `Ollama`、`OpenAI` 兼容层或者 agent loop 本身，可以直接跑：

```bash
python3 scripts/diagnose_runtime.py --model qwen2.5-coder:7b
```

这个脚本会顺序检查：

- `ollama ps`
- `api/version`
- `api/tags`
- 最小直连 chat
- 最小 OpenAI 兼容 chat
- `jarvis` 风格的 quick prompt
- 同一个真实 agent 任务在短超时和长超时下的表现

结果会写到 `diagnostic-results/`，方便以后回看“到底是运行时坏了，还是模型太慢，还是 agent prompt/tool 形态触发了延迟”。

当前已经有两份官方诊断结果：

- [diagnostic-results/2026-04-24_001108_qwen2-5-coder-7b.md](/Users/wuxiaoshuchu/Desktop/my-agent/diagnostic-results/2026-04-24_001108_qwen2-5-coder-7b.md)
- [diagnostic-results/2026-04-24_010457_qwen2-5-coder-14b.md](/Users/wuxiaoshuchu/Desktop/my-agent/diagnostic-results/2026-04-24_010457_qwen2-5-coder-14b.md)
