# CHANGELOG

按时间倒序记录这个仓库的重要迭代，方便回看成长过程、理解每一轮为什么改、出了什么成果。

## 2026-04-24

### 回归 P1 长任务问题，修 fake tool call 和 active goal 漂移

- 更新 [agent.py](agent.py)，让 fake tool call 解析器支持 `function_name` 这种真实回归里出现过的变体。
- 更新 [agent.py](agent.py)，让 `active goal` 在用户只输入“继续 / continue”这类低信息 follow-up 时继续沿用原任务主线。
- 更新 [context_engine.py](context_engine.py)，让 compact 摘要尽量过滤 fake tool call JSON，减少历史摘要污染后续模型行为。
- 补充 [tests/test_agent.py](tests/test_agent.py) 和 [tests/test_context_engine.py](tests/test_context_engine.py)，把这轮真实回归样本固化成测试。
- 更新 [README.md](README.md) 和 [way-to-claw-code.md](way-to-claw-code.md)，把这轮回归修正写回仓库。

### 为什么这样改

- `P1` 第一版虽然已经能 compact，但真实长任务里还是暴露了两类很典型的问题：模型会吐另一种 fake tool call 形态，低信息 follow-up 会把任务主线冲淡。
- 这轮修的不是“新功能”，而是让 `compact / session memory` 这条链更可靠，更接近真正长任务里能用的状态。

### 验证

- `python3 -m unittest discover -s tests`
- `python3 - <<'PY' ... extract_fake_tool_calls(function_name 变体) ... PY`

### 进入 P1，给 jarvis 增加最小 context engine

- 新增 [context_engine.py](context_engine.py)，把会话长度估算、自动 compact、`session memory` 和 memory 渲染逻辑从 [agent.py](agent.py) 里拆出来。
- 更新 [agent.py](agent.py)，让 REPL 支持 `/compact`，在长对话里自动压缩较早 turn，并把 `HARNESS.md`、[way-to-claw-code.md](way-to-claw-code.md) 和 compact 后的 active goal 一起重新注入 system prompt。
- 更新 [README.md](README.md) 和 [way-to-claw-code.md](way-to-claw-code.md)，把 `P1` 的第一版落地能力和后续缺口写回仓库。
- 更新 [setup.cfg](setup.cfg)，把新的 `context_engine` 模块纳入安装元数据。
- 新增 [tests/test_context_engine.py](tests/test_context_engine.py)，并补充 [tests/test_agent.py](tests/test_agent.py)，覆盖 compact 后 goal 保留、路线图注入和 context 统计。

### 为什么这样改

- 这是 `P1` 的真正起点：先让 `jarvis` 具备“会话不会只会越堆越长”的最小能力。
- 这版 compact 还不聪明，但已经把最关键的架子搭起来了：短期 messages、中期 session memory、长期仓库规则与路线图开始分层。
- 也顺手把这层逻辑从 `agent.py` 里拆开，为后面继续做更高质量摘要、持久 memory 和更复杂调度留出空间。

### 验证

- `python3 -m unittest discover -s tests`
- `./.venv/bin/jarvis --help`
- `printf '/compact\n/quit\n' | python3 agent.py --repl`

### 补齐 14b 基线，并把本机默认模型建议收紧回 7b

- 跑完 `qwen2.5-coder:14b` 的官方 benchmark 和 runtime diagnostics，并把结果落进 [benchmark-results/](benchmark-results/) 和 [diagnostic-results/](diagnostic-results/)。
- 更新 [model-baseline.md](model-baseline.md)、[README.md](README.md)、[way-to-claw-code.md](way-to-claw-code.md)，把 `7b vs 14b` 的真实对比写回仓库。
- 更新 [runtime_diagnostics.py](runtime_diagnostics.py)，让诊断器能识别“大模型在本机冷启动和 agent prompt 下整体过慢”的模式。
- 更新 [scripts/diagnose_runtime.py](scripts/diagnose_runtime.py)，给 `ollama ps` 这类子进程加默认超时，避免诊断脚本自己被运行时拖死。
- 补充 [tests/test_runtime_diagnostics.py](tests/test_runtime_diagnostics.py)，覆盖新的根因推断分支。

### 为什么这样改

- 这轮最关键的价值不是“把 14b 装上了”，而是确认了它在这台 `M1 + 16GB` 机器上并不适合作为默认本地模型。
- 现在我们对本机模型选择不再只靠直觉，而是已经有了 `7b` 和 `14b` 的真实对比。
- 也顺手把诊断工具链补强了，避免以后排查时被 `ollama ps` 这种慢子进程反过来卡住。

### 验证

- `python3 -m unittest discover -s tests`
- `python3 -u scripts/benchmark_agent.py --models qwen2.5-coder:14b --max-turns 3 --request-timeout 20`
- `python3 scripts/diagnose_runtime.py --model qwen2.5-coder:14b`
- `./.venv/bin/jarvis --help`

## 2026-04-23

### 增加运行时诊断脚本，并定位 7b 超时根因

- 新增 [runtime_diagnostics.py](runtime_diagnostics.py)，抽出运行时诊断结果结构、根因归纳和 markdown 报告渲染。
- 新增 [scripts/diagnose_runtime.py](scripts/diagnose_runtime.py)，可以顺序诊断 `Ollama` 服务、最小直连请求、OpenAI 兼容请求，以及真实 agent 任务在短超时/长超时下的表现。
- 新增 [tests/test_runtime_diagnostics.py](tests/test_runtime_diagnostics.py)，覆盖诊断摘要和根因推断。
- 更新 [README.md](README.md) 和 [model-baseline.md](model-baseline.md)，把这轮真实诊断结论写回仓库。

### 为什么这样改

- 现在我们已经不只是“知道 7b benchmark 会超时”，而是开始知道“超时到底发生在哪一层”。
- 这能帮助后面决定到底该优先修 `Ollama`、调 timeout、收 prompt，还是直接换模型。
- 也把这次排查方法留在仓库里，避免以后每次都从头排。

### 验证

- `python3 -m unittest discover -s tests`
- `python3 scripts/diagnose_runtime.py --model qwen2.5-coder:7b`
- `./.venv/bin/jarvis --help`

### 增加模型 benchmark 脚手架和首轮结果目录

- 新增 [benchmark_harness.py](benchmark_harness.py)，把 benchmark 任务加载、结果评估、markdown 报告渲染和结果序列化抽成独立模块。
- 新增 [benchmarks/agent_tasks.json](benchmarks/agent_tasks.json)，定义默认只读 benchmark 任务集。
- 新增 [scripts/benchmark_agent.py](scripts/benchmark_agent.py)，可以直接对一个或多个模型跑真实 agent benchmark。
- 新增首份真实结果 [benchmark-results/2026-04-23_233206_qwen2-5-coder-7b.md](benchmark-results/2026-04-23_233206_qwen2-5-coder-7b.md) 和对应 json。
- 新增 [tests/test_benchmark_harness.py](tests/test_benchmark_harness.py)，覆盖 benchmark 结果评估和报告渲染。
- 更新 [README.md](README.md) 和 [model-baseline.md](model-baseline.md)，把 benchmark 工作流真正写进仓库。

### 为什么这样改

- 这是 `P0` 和 `P4` 之间很值的一座桥：先让仓库具备“能跑 benchmark”的能力，再逐步积累真实数据。
- 让后面比较 `7b / 14b / 16b` 时不再靠主观体感，而是有统一任务集和固定输出格式。
- 让模型优化开始沉淀成结果文件，而不是每次重新聊一遍。
- 让当前本机 `7b` 的真实基线被记录下来，而不是停留在“感觉有点慢”。

### 验证

- `python3 -m unittest discover -s tests`
- `python3 -u scripts/benchmark_agent.py --models qwen2.5-coder:7b --max-turns 3 --request-timeout 20`
- `./.venv/bin/jarvis --help`

### 增加模型可观察性、默认模型配置和本机基线

- 新增 [jarvis.config.json](jarvis.config.json)，把默认 `model / base_url / num_ctx` 真正写进仓库。
- 新增 [model-baseline.md](model-baseline.md)，记录这台 `M1 + 16GB` 机器上的本地模型基线和下一批候选模型。
- 新增 [runtime_config.py](runtime_config.py)，把运行时配置解析、本地模型发现和配置写回逻辑从 `agent.py` 里拆了出来。
- `jarvis` 现在支持 `/model`、`/model use <name>`、`/model set <name>`、`/model ctx <N>`。
- CLI 参数现在会按“命令行 > 工作区配置 > 内置默认值”的顺序解析。
- 补充测试，覆盖工作区运行时配置优先级、配置写回和 `ollama list` 输出解析。
- 顺手修正安装元数据，把新增模块纳入打包清单。

### 为什么这样改

- 这是 `way-to-claw-code.md` 里 `P0` 最值的一步：先让 agent 看清自己在用什么模型，再让默认模型切换变成仓库能力。
- 让模型选择不再只是临时 CLI 参数，而是项目级、可追踪、可继承的配置。
- 让以后做本地 benchmark 时，有地方记录“这台机器上什么模型值得继续试”。
- 顺手把运行时配置从 `agent.py` 拆出去，也是在为后面的架构演进做准备。

### 验证

- `python3 -m unittest discover -s tests`
- `./.venv/bin/jarvis --help`
- `python3 agent.py --repl </dev/null`

### 增加局部编辑工具和 patch 预览

- 新增 `edit_file` 工具，支持按精确文本片段做局部编辑。
- 新增 `apply_patch` 工具，支持一次应用多个精确文本替换。
- `write_file` 和 `edit_file` 现在都会返回 `patch preview`，直接显示改了哪些行。
- `write_file`、`edit_file` 和 `apply_patch` 现在在真正应用之前也会先展示 `patch preview before apply`。
- patch 类审批交互现在支持 `y / p / n`，可以更明确地接受、查看或取消 patch。
- 多 hunk patch 现在支持先看总 patch，再逐段接受、跳过或提前结束审批。
- patch 审批现在会显示更像 TUI 的终端状态面板，包含文件、增删统计、逐段进度和动作提示。
- patch 审批现在支持单键操作，不需要每次输入后再按回车。
- `WorkspaceInspector` 现在拆成了独立模块，并对高频 Git 状态查询增加了轻量缓存。
- 新增 `way-to-claw-code.md` 长期路线图，并在 `HARNESS.md` 中挂上入口，方便后续 agent 在上下文压缩后继续推进。
- 新增 `/patch [path]` 命令，可以在 REPL 里直接看当前 patch。
- 补充测试，覆盖局部编辑、多 hunk patch、逐段审批、终端审批面板、单键提示、Git 状态缓存和 untracked 文件预览。

### 为什么这样改

- 让 `jarvis` 从“只能整文件写入”走向更像真正的编码助手。
- 让用户不仅知道 agent 做了动作，还能看见具体改动内容。
- 让代码修改过程更可观察，也更适合之后继续加审批或应用 patch 的工作流。
- 让 patch 审批不再只能整份通过或整份取消，而是可以更细颗粒度地控制。
- 让终端审批体验更像一个真正的开发工具界面，而不是简单的 `input()` 问答。
- 让高频 patch 审批动作更顺手，减少确认时的操作摩擦。
- 让 `agent.py` 的职责更集中，也减少 REPL 高频显示场景下重复拉起 Git 子进程的次数。
- 让项目的长期方向从“只存在聊天里”变成“存在仓库里”，方便未来每一轮按路线图推进。

### 验证

- `python3 -m unittest discover -s tests`
- `jarvis --help`
- 文档一致性检查：`HARNESS.md` / `README.md` / `way-to-claw-code.md`

### 增加启动 banner、状态头和动态提示符

- 给 `jarvis` 的 REPL 增加了 ASCII banner。
- 启动时会展示 workspace、model、审批模式和当前 Git 状态头。
- 输入提示符现在会动态显示分支、ahead/behind、dirty 状态和审批模式。
- 继续补充测试，覆盖 Git 状态快照和动态提示符格式。

### 为什么这样改

- 让工具一启动就更像成熟 CLI，而不是普通脚本。
- 把“当前在哪个分支、仓库脏不脏、现在是 ask 还是 auto”这些关键上下文放到眼前。
- 让你在 IDE 和终端里都更容易感受到它是一个真正的工具。

### 验证

- `python3 -m unittest discover -s tests`
- `jarvis --help`

### 增加提交与摘要命令，并补 IDE 启动配置

- 新增 `/summary`，可以直接回看本轮动作、Git 状态和 diff 摘要。
- 新增 `/commit [message]`，会先展示将提交的内容，再确认并创建 commit。
- 给仓库补了 `.vscode/launch.json` 和 `.vscode/tasks.json`，方便在 VS Code 里一键启动 `jarvis`。
- 补充了对应测试，覆盖项目规则注入、CLI 参数，以及 Git 摘要/提交能力。

### 为什么这样改

- 让 `jarvis` 更像一个真实长期使用的开发工具，而不是只能手动跑脚本。
- 让每一轮修改都更容易被回看、总结和提交。
- 让 IDE 里的启动体验更接近 `claude` 这类现成工具。

### 验证

- `python3 -m unittest discover -s tests`
- `jarvis --help`

### 把 my-agent 推进成可安装的 `jarvis` CLI

- 把原本的最小脚本演进成了 session 驱动的本地编码 agent。
- 增加了 REPL、多轮消息历史、工具调用日志和 Git 可观察性命令。
- 新增了 `list_files`、`grep_text`、工作区约束、写文件/执行命令确认。
- 把项目打包成可安装 CLI，并提供 `jarvis` 命令入口和开发安装脚本。
- 让 agent 在启动时自动读取仓库根目录的 `HARNESS.md`，从而继承项目级规则。
- 补充了测试、`.gitignore`、项目安装文件和文档。

### 为什么这样改

- 让项目从“能跑的 demo”变成“更像真实工具”的雏形。
- 让用户能像使用 `claude` 一样直接运行 `jarvis`。
- 让之后每一轮迭代都有可追踪的规则和历史，而不是只留在聊天上下文里。

### 验证

- `python3 -m unittest discover -s tests`
- `jarvis --help`
