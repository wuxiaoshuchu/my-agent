# model-baseline

这份文件记录 `jarvis` 在这台机器上的本地模型基线，避免每次都重新猜。

## 当前机器快照

- 日期：2026-04-24
- 机器：MacBook Air
- 芯片：Apple M1
- 内存：16GB unified memory
- 运行时：Ollama 0.21.0

## 当前默认配置

仓库根目录的 [jarvis.config.json](jarvis.config.json) 当前使用：

- `model`: `qwen2.5-coder:7b`
- `base_url`: `http://localhost:11434/v1`
- `num_ctx`: `16384`

这意味着：

- `jarvis` 默认走本地 Ollama 的 OpenAI 兼容接口
- 不传命令行参数也能直接启动
- 默认行为已经落在仓库里，而不是只存在聊天上下文里

## 已确认状态

- `ollama --version` 正常，当前是 `0.21.0`
- `ollama list` 正常
- 当前已安装模型：
  - `qwen2.5-coder:7b`
  - `qwen2.5-coder:14b`

## 下一批候选模型

这些是接下来最值得在这台机器上比较的本地模型：

- `deepseek-coder-v2:16b`

`qwen2.5-coder:14b` 已经实测过了，但结果说明它不适合作为这台机器上的默认本地模型。

## 建议 benchmark 维度

以后做模型比较时，至少记录下面这些维度：

- 首 token 速度体感
- repo 内多文件搜索后的总结质量
- patch 修改成功率
- 长任务里是否容易跑偏
- 发热和卡顿体感

## Benchmark 脚手架

仓库里现在已经有：

- `benchmarks/agent_tasks.json`
- `scripts/benchmark_agent.py`
- `benchmark-results/`

默认先 benchmark 只读任务，这样可以先比较模型在 repo 理解、工具调用和总结质量上的差异。

## 首轮真实结果：`qwen2.5-coder:7b`

首轮真实结果已经保存到：

- [benchmark-results/2026-04-23_233206_qwen2-5-coder-7b.md](/Users/wuxiaoshuchu/Desktop/my-agent/benchmark-results/2026-04-23_233206_qwen2-5-coder-7b.md)
- [benchmark-results/2026-04-23_233206_qwen2-5-coder-7b.json](/Users/wuxiaoshuchu/Desktop/my-agent/benchmark-results/2026-04-23_233206_qwen2-5-coder-7b.json)

运行命令：

```bash
python3 -u scripts/benchmark_agent.py \
  --models qwen2.5-coder:7b \
  --max-turns 3 \
  --request-timeout 20
```

结果：

- `pass_rate`: `0/4`
- `average_duration_ms`: `20548`
- 4 个任务都在首轮模型请求阶段触发 `APITimeoutError`
- 在这个受控 benchmark 下，`qwen2.5-coder:7b` 还没进入工具调用阶段就超时了

这说明当前瓶颈不只是“模型大小”，更可能是：

- 当前 `Ollama` 运行时响应偏慢
- 当前 `7b` 在这个 agent loop 和超时边界下不够稳
- 下一步更值得优先做运行时诊断或直接拉 `14b / 16b` 做对比

## 诊断结论：`qwen2.5-coder:7b`

这轮已经把问题继续往下拆开了：

- 最新诊断报告：
  - [diagnostic-results/2026-04-24_001108_qwen2-5-coder-7b.md](/Users/wuxiaoshuchu/Desktop/my-agent/diagnostic-results/2026-04-24_001108_qwen2-5-coder-7b.md)
  - [diagnostic-results/2026-04-24_001108_qwen2-5-coder-7b.json](/Users/wuxiaoshuchu/Desktop/my-agent/diagnostic-results/2026-04-24_001108_qwen2-5-coder-7b.json)

- `Ollama` 服务本身是健康的：`/api/version`、`/api/tags`、最小直连 chat 都能正常返回
- 最小直连 chat：
  - 冷态大约 `6s`
  - warm 后大约 `0.4s`
- 最小 OpenAI 兼容 chat 也是秒级
- 带完整 `jarvis` prompt + tools 的请求，在 `20s` 短超时下依然可能超时
- 真正慢的是“真实 agent 任务的首轮工具决策”
  - `读取 jarvis.config.json...` 这个 prompt，`qwen2.5-coder:7b` 大约要 `52s` 才产出第一条 fake tool call
  - 同一任务在 `120s` 超时下可以完成，整轮大约 `63s`

所以当前最接近真实根因的结论是：

- 不是 `Ollama` 基础服务挂了
- 不是 `localhost / OpenAI 兼容接口` 本身坏了
- 主要是 `qwen2.5-coder:7b` 在当前 `jarvis` 风格 prompt + tools + 真实 repo 任务下，首轮工具规划太慢
- 当前 benchmark 的 `20s` 超时会系统性错杀这类任务

## 第二轮真实结果：`qwen2.5-coder:14b`

这轮把 `14b` 也跑进了同样的仓库脚手架里。

结果文件：

- [benchmark-results/2026-04-24_005305_qwen2-5-coder-14b.md](/Users/wuxiaoshuchu/Desktop/my-agent/benchmark-results/2026-04-24_005305_qwen2-5-coder-14b.md)
- [benchmark-results/2026-04-24_005305_qwen2-5-coder-14b.json](/Users/wuxiaoshuchu/Desktop/my-agent/benchmark-results/2026-04-24_005305_qwen2-5-coder-14b.json)
- [diagnostic-results/2026-04-24_010037_qwen2-5-coder-14b.md](/Users/wuxiaoshuchu/Desktop/my-agent/diagnostic-results/2026-04-24_010037_qwen2-5-coder-14b.md)
- [diagnostic-results/2026-04-24_010037_qwen2-5-coder-14b.json](/Users/wuxiaoshuchu/Desktop/my-agent/diagnostic-results/2026-04-24_010037_qwen2-5-coder-14b.json)
- [diagnostic-results/2026-04-24_010457_qwen2-5-coder-14b.md](/Users/wuxiaoshuchu/Desktop/my-agent/diagnostic-results/2026-04-24_010457_qwen2-5-coder-14b.md)
- [diagnostic-results/2026-04-24_010457_qwen2-5-coder-14b.json](/Users/wuxiaoshuchu/Desktop/my-agent/diagnostic-results/2026-04-24_010457_qwen2-5-coder-14b.json)

运行命令：

```bash
python3 -u scripts/benchmark_agent.py \
  --models qwen2.5-coder:14b \
  --max-turns 3 \
  --request-timeout 20

python3 scripts/diagnose_runtime.py --model qwen2.5-coder:14b
```

真实观察：

- benchmark 仍然是 `0/4`
- benchmark 的失败形态不是 `7b` 那种 `APITimeoutError`，而是几乎立刻出现 `APIConnectionError`
- 最小直连 `api/chat` 在两次诊断里波动很大：
  - 冷态样本大约 `40.7s`
  - warm 样本大约 `3.9s`
- 最小 OpenAI 兼容 chat 大约 `3.2s - 3.8s`
- 带完整 `jarvis` prompt + tools 的 quick prompt 在 `20s` 下超时
- 同一个真实 agent 任务也不稳定：
  - 一次在大约 `169s` 后真正完成，并调用了 `read_file`
  - 另一次在大约 `111s` 后只输出了一个 JSON 形式的 fake tool call，没有完成任务

这说明：

- `14b` 在这台 `M1 + 16GB` 机器上不是“更强但同样可用”，而是冷启动、工具规划和稳定性都明显不如预期
- 慢点已经前移到了模型加载、首 token 和带工具 prompt 的整体响应
- 当前阶段不应该把默认模型切到 `14b`

## 当前推荐

基于现在手里的真实结果：

- 仓库默认继续保持 `qwen2.5-coder:7b`
- 这不是因为 `7b` 已经很好，而是因为它至少能在长超时下完成真实 agent 任务
- `qwen2.5-coder:14b` 暂时不建议作为这台机器上的默认本地模型
- 下一步更值得做的是：
  - 继续优化 `7b` 的 `timeout / warmup / prompt / context`
  - 或者测试下一位候选 `deepseek-coder-v2:16b`

## 常用命令

```bash
jarvis
jarvis --model qwen2.5-coder:14b
jarvis --num-ctx 24576
./.venv/bin/python scripts/benchmark_agent.py --models qwen2.5-coder:7b
```

在 REPL 里：

```text
/model
/model use qwen2.5-coder:14b
/model set qwen2.5-coder:14b
/model ctx 24576
```
