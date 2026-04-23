# CHANGELOG

按时间倒序记录这个仓库的重要迭代，方便回看成长过程、理解每一轮为什么改、出了什么成果。

## 2026-04-23

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
