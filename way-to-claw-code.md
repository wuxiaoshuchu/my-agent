# way-to-claw-code

这份文件不是灵感备忘录，而是 `my-agent` 的长期执行路线图。

目标很明确：

- 把 `jarvis` 从“能跑的本地编码 agent”继续推进成“更接近 claw-code / Claude Code / Codex 使用体验”的工具。
- 让未来进入这个仓库的 agent，即使没有完整聊天上下文，也能知道下一步该做什么。

## 当前状态

截至 2026-04-24，这个仓库已经具备：

- `jarvis` CLI 与 REPL
- 多轮 session 与消息历史
- `read_file` / `write_file` / `edit_file` / `apply_patch` / `list_files` / `grep_text` / `run_command`
- Git 可观察性：`/status` `/branch` `/diff` `/patch` `/summary` `/commit`
- patch 审批、逐段审批、终端审批面板、单键操作
- `WorkspaceInspector` 独立模块与 Git 状态轻量缓存
- 最小 `context engine`：message/token 估算、`session memory`、自动 compact 和 `/compact`
- `P1` 现在还有 deterministic context regression harness，可以回归 compact / active goal / fake tool call
- `P1` 现在还有 live context regression harness，可以在真实模型上回归 compact / active goal / full-stack tool use
- `P4` 现在起步有了请求级性能观察：`/perf`、request payload profile、diagnostics 里的 `agent_payload_profile`

但和 `claw-code` / `Codex` 这类成熟 agent 仍有明显差距，主要缺口不是单个功能，而是系统层：

- 上下文管理：已经有最小 compact / session memory，但还没有更高质量摘要、持久 memory 和精确 token budget
- 工具调度：还没有只读工具并发、写操作串行、调度策略
- 恢复能力：还没有 retry / fallback / 中断恢复 / 长任务继续
- 权限系统：还没有 prefix rules / 持久规则 / 更细粒度审批
- 可观测性：还没有 profiling / cost / tool duration / eval
  - 现在已经起步补上了模型请求层的 payload / duration 观察，但工具耗时和更完整 eval 还没接进来
- 多 agent：还没有 worktree / delegation / 背景任务

## 北极星

达到“接近 claw-code”的标准，不是只看模型回答得更聪明，而是至少满足下面这些体验：

- 一个任务可以稳定跑很多轮，而不是几轮后上下文就乱掉
- 能在 repo 级别理解项目，而不是只看几个文件
- 能自动决定哪些工具并发、哪些工具串行
- 写代码前后都可观察、可审批、可恢复
- 有长期记忆和压缩机制，不会越跑越笨
- 有可评估的质量指标，而不是只靠主观感觉

## 使用原则

- 每一轮只推进一个清晰主题，并留下 1 个 commit。
- 优先做“基础设施级”改动，而不是只加表面命令。
- 任何路线图项在开始前，都先看：
  - `HARNESS.md`
  - `CHANGELOG.md`
  - 这份 `way-to-claw-code.md`

## 优先级总览

- P0: 运行时稳定性与模型基线
- P1: context engine
- P2: tool scheduler
- P3: permission system
- P4: observability + eval
- P5: background work + multi-agent

## P0 运行时稳定性与模型基线

### 目标

- 先把本地推理跑稳，再讨论“像不像 Codex”。
- 形成这台机器上的默认推荐模型，而不是靠感觉切模型。

### 待办

- [ ] 修复本机 `ollama` 运行时稳定性，至少保证 `ollama list`、`ollama run`、OpenAI 兼容接口可用
- [x] 新增 `/model` 或等价命令，显示当前模型、base URL、context 设置
- [x] 建一个最小模型 benchmark 文档，记录这台机器上的可用模型与体验
- [x] 给 `jarvis` 增加默认模型切换配置，而不是只能靠命令行参数临时覆盖

### 当前模型建议

- 当前仓库默认继续保持：`qwen2.5-coder:7b`
- 下一位候选：`deepseek-coder-v2:16b`
- 已确认不适合作为这台机器默认本地模型：`qwen2.5-coder:14b`
- 暂不建议作为主力本地模型：`qwen3-coder:30b`
  - 原因：你的机器是 `M1 + 16GB`，这类 `19GB` 级模型对日常本地使用太吃紧

### 最近观察

- `qwen2.5-coder:7b` 在首轮真实 benchmark 下表现为 `0/4`
- benchmark 参数：`--max-turns 3 --request-timeout 20`
- 4 个只读任务都在首轮模型请求阶段超时，还没进入工具调用
- 这说明当前差距不只是 agent 架构，也包括本机运行时响应
- 进一步诊断后发现：
  - `Ollama` 服务本身是健康的
  - 最小直连和最小 OpenAI 兼容请求都能秒级返回
  - 慢点主要出现在 `qwen2.5-coder:7b` 处理真实 agent 任务时的首轮工具规划
  - 同一个任务在 `120s` 超时下可以完成，说明更像“太慢”而不是“完全坏了”
  - 新增的 `agent_payload_profile` 也已经把当前请求画像固定下来了：
    - `messages=2`
    - `est_tokens=1375`
    - `system_chars=5147`
    - `tool_schema_chars=2516`
  - 最新 long-timeout 诊断里，同一轮真实 agent 任务的 turn 1 / turn 2 大约分别是 `45.6s / 23.5s`
  - 进一步把读取类任务缩到 `read_only` 工具画像后：
    - `tool_schema_count: 7 -> 3`
    - `tool_schema_chars: 2516 -> 920`
    - `system_chars: 5147 -> 4939`
  - 但单次 long-timeout 诊断没有立刻变快，这说明“减少工具数”方向是对的，但当前瓶颈不只一层
  - 再继续给只读任务加上 `lean_read_only` prompt 后：
    - `system_chars: 4939 -> 1620`
    - `est_tokens: 1321 -> 459`
    - 同一个 long-timeout 样本里 turn 1 大约 `103s -> 79s`
  - 但 turn 2 反而变长到了大约 `54s`，说明 prompt 瘦身已经开始起效，但“控制最终回答长度”仍然是下一层瓶颈
- `qwen2.5-coder:14b` 这轮也已经补了真实对比：
  - benchmark 仍然是 `0/4`
  - 最小直连 `api/chat` 在 warm/cold 状态下波动很大，大约 `3.9s - 40.7s`
  - 最小 OpenAI 兼容请求大约 `3.2s - 3.8s`
  - 带完整 `jarvis` prompt + tools 的 quick prompt 在 `20s` 下超时
  - 同一个真实 agent 任务也不稳定：一次整轮大约 `169s` 才完成，另一次只输出 fake tool call
  - 这说明 `14b` 在这台机器上仍然不适合作为默认本地模型

### 完成标准

- 至少 2 个候选模型可以稳定运行
- repo 里有明确记录“这台机器上推荐哪个模型”
- 之后 agent 不需要重新猜模型选择

## P1 Context Engine

### 目标

- 让 agent 在长任务里不容易“失忆”。
- 这是最值得优先补的核心能力之一。

### 待办

- [x] 为 `AgentSession` 增加 token / message 数量估算
- [x] 做最小 `compact` 能力：当消息过长时，自动压缩历史为摘要块
- [x] 引入 `session memory` 概念，区分：
  - 短期：当前会话 messages
  - 中期：本轮任务摘要
  - 长期：仓库级规则与路线图
- [x] 给 REPL 增加 `/compact` 命令，允许手动压缩
- [x] 给系统提示组装逻辑增加“压缩后仍保留 HARNESS / 路线图 / 当前任务目标”

### 当前实现说明

- 现在的 compact 是 deterministic 的，不依赖额外模型调用
- 会保留最近几个 turn，把更早对话折叠成 `session memory`
- `session memory` 会和 `HARNESS.md`、`way-to-claw-code.md` 一起重新注入 system prompt
- 已经补了一轮真实回归里暴露的问题：
  - `function_name` 形式的 fake tool call 现在也能被解析
  - compact 摘要会尽量过滤 fake tool call JSON
  - “继续 / continue” 这类低信息 follow-up 不会覆盖 active goal
- 现在还有一套固定 regression cases 和结果目录，后续改 `P1` 时可以持续回归
- 现在还补上了 live model regression：
  - `goal-only` case 先显式关闭工具 schema，再验证 compact 后还能不能沿用当前任务目标
  - `full-stack` case 再继续验证 compact 后还能不能恢复真实工具链
- 当前第一轮 live 结果已经说明：
  - `goal-only` 是接下来最适合持续压实的 `P1` 主样本
  - `goal-only` 在 `qwen2.5-coder:7b` 上已经能通过，但单条大约仍要 `100s`
  - `full-stack` 在 `qwen2.5-coder:7b` 上仍然容易碰到 `120s` 超时，所以它同时也是 `P0 + P1` 的联合压力测试
- 这一版已经够用来避免“消息只会越堆越多”，但摘要质量和跨任务长期记忆还远没到终点

### 完成标准

- 长任务中上下文不会无限膨胀
- 压缩后仍保留任务目标、已完成工作、未完成工作
- 至少有测试覆盖 compact 前后关键信息保留
- 至少有一套 live regression 可以稳定验证 compact 后的目标延续

## P2 Tool Scheduler

### 目标

- 把“一个一个工具线性执行”升级成“有调度策略的工具运行器”。

### 待办

- [ ] 抽离 `ToolRuntime`：拆成文件工具、patch UI、命令工具、调度器
- [~] 给工具增加元数据：
  - 是否只读
  - 是否可并发
  - 是否需要审批
  - 是否会修改上下文状态
- 当前已经先做了最小一步：按任务意图在 `full / read_only` 两个画像之间切换，优先减少本地模型首轮规划时看到的工具数
- 下一步更值得做的是继续压 system prompt，或者把首轮规划和首轮工具选择拆得更轻，而不是只继续加更多 heuristics
- 如果继续留在这一条线上，最值的下一步是给只读回答加更强的“默认简短、不要回贴整段文件”约束，避免 turn 2 把节省下来的时间重新吃回去
- [ ] 实现只读工具批量并发
  - 典型目标：`read_file` / `grep_text` / `list_files`
- [ ] 保持写工具串行
  - `write_file` / `edit_file` / `apply_patch` / `run_command`
- [ ] 记录每个工具的耗时与结果大小

### 完成标准

- 可以安全并发跑只读探索工具
- 写操作仍保持确定性
- 工具执行日志开始具备调度概念
- 模型不必每轮都看到所有工具；至少常见只读任务会自动缩窄到更小工具集

## P3 Permission System

### 目标

- 让审批从“每次问一遍”升级成“有规则、有记忆、有边界”。

### 待办

- [ ] 抽离审批逻辑为独立模块，不再散在工具内部
- [ ] 支持 prefix rule
  - 例如某些 `git status` / `python -m unittest` 可自动放行
- [ ] 支持 session 级审批记忆
- [ ] 支持项目级审批规则文件
- [ ] 为高风险命令保留强制人工确认

### 完成标准

- 安全与流畅性达到更好平衡
- 常用低风险命令不用每次都卡人
- 高风险命令仍然明确可控

## P4 Observability + Eval

### 目标

- 不再只靠“感觉这轮不错”，而是开始量化 agent 的表现。

### 待办

- [x] 为模型调用记录：
  - 轮数
  - 工具数
  - 总耗时
  - timeout / error
- [x] 给 REPL 增加最小 `/perf` 命令，直接看当前请求载荷和最近模型请求轨迹
- [x] 把 `agent_payload_profile` 接进 runtime diagnostics，记录同一轮 agent 请求的 payload 画像
- [ ] 为工具调用记录：
  - 工具名
  - 耗时
  - 输出大小
  - 是否被拒绝
- [ ] 做一个最小 eval 目录，覆盖：
  - 读代码找 TODO
  - 局部 patch 修改
  - 多文件搜索
  - Git 状态判断
- [ ] 建一个“回归任务集”，每次核心架构改动后都能跑

### 完成标准

- 架构改动后有办法判断是更好还是更差
- 至少有一个轻量 benchmark 能比较模型和 agent 版本
- 至少能快速看见“这一轮模型请求到底带了多少上下文、多少 tools、花了多久”

## P5 Background Work + Multi-Agent

### 目标

- 这是“更接近 Codex / claw-code”最有标志性的阶段，但不应过早开始。

### 待办

- [ ] 先做 background task，而不是直接 full multi-agent
- [ ] 支持把长任务挂起、恢复、继续
- [ ] 为每个任务提供独立状态对象或工作目录
- [ ] 在本地加入 worktree / 分支隔离策略
- [ ] 再考虑 sub-agent：
  - planner
  - coder
  - reviewer
  - researcher

### 完成标准

- 至少可以同时维护多个任务上下文
- 不同任务的变更不会互相污染
- sub-agent 是锦上添花，不是用来掩盖单 agent 不稳

## 近期执行顺序

未来几轮如果没有用户明确改方向，默认按这个顺序推进：

1. 先完成 P0：修本地模型运行时，建立模型基线
2. 再完成 P1：做最小 compact / memory
3. 再完成 P2：拆 `ToolRuntime`，加入只读并发调度
4. 再完成 P3：审批规则化
5. 再完成 P4：加 profiling 和 eval
6. 最后再做 P5：background work / multi-agent

## 下一轮推荐动作

如果未来 agent 重新进入这个仓库，不知道先做什么，默认先做下面这 3 件事中的第 1 件：

- [ ] 根据这轮诊断结果，决定是先调 `timeout / warmup / prompt`，还是直接换默认模型
- [ ] 用 benchmark 脚手架继续补数据，比较 `qwen2.5-coder:7b` 与 `deepseek-coder-v2:16b`
- [ ] 在 live model 长任务里继续回归这版 `compact / session memory`，重点看摘要质量和自动 compact 触发阈值

## 不要误判的事情

- 不要以为只要换了更大模型，就会自然接近 Codex。
- 不要过早上多 agent；单 agent 基础设施不稳时，多 agent 只会放大混乱。
- 不要把“UI 更酷”误当成“agent 更强”；真正的差距主要在调度、上下文、恢复、评估。

## 更新规则

每次完成路线图里的一个明确步骤时：

- 更新 `CHANGELOG.md`
- 在这份文件里勾掉对应项或补充状态
- 创建 1 个清晰的 commit
