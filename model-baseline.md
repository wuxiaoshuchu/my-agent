# model-baseline

这份文件记录 `jarvis` 在这台机器上的本地模型基线，避免每次都重新猜。

## 当前机器快照

- 日期：2026-04-23
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

## 下一批候选模型

这些是接下来最值得在这台机器上比较的本地模型：

- `qwen2.5-coder:14b`
- `deepseek-coder-v2:16b`

## 建议 benchmark 维度

以后做模型比较时，至少记录下面这些维度：

- 首 token 速度体感
- repo 内多文件搜索后的总结质量
- patch 修改成功率
- 长任务里是否容易跑偏
- 发热和卡顿体感

## 常用命令

```bash
jarvis
jarvis --model qwen2.5-coder:14b
jarvis --num-ctx 24576
```

在 REPL 里：

```text
/model
/model use qwen2.5-coder:14b
/model set qwen2.5-coder:14b
/model ctx 24576
```
