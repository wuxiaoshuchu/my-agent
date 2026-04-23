# CHANGELOG

按时间倒序记录这个仓库的重要迭代，方便回看成长过程、理解每一轮为什么改、出了什么成果。

## 2026-04-23

### 增加局部编辑工具和 patch 预览

- 新增 `edit_file` 工具，支持按精确文本片段做局部编辑。
- 新增 `apply_patch` 工具，支持一次应用多个精确文本替换。
- `write_file` 和 `edit_file` 现在都会返回 `patch preview`，直接显示改了哪些行。
- `write_file`、`edit_file` 和 `apply_patch` 现在在真正应用之前也会先展示 `patch preview before apply`。
- patch 类审批交互现在支持 `y / p / n`，可以更明确地接受、查看或取消 patch。
- 多 hunk patch 现在支持先看总 patch，再逐段接受、跳过或提前结束审批。
- 新增 `/patch [path]` 命令，可以在 REPL 里直接看当前 patch。
- 补充测试，覆盖局部编辑、多 hunk patch、逐段审批、patch 预览和 untracked 文件预览。

### 为什么这样改

- 让 `jarvis` 从“只能整文件写入”走向更像真正的编码助手。
- 让用户不仅知道 agent 做了动作，还能看见具体改动内容。
- 让代码修改过程更可观察，也更适合之后继续加审批或应用 patch 的工作流。
- 让 patch 审批不再只能整份通过或整份取消，而是可以更细颗粒度地控制。

### 验证

- `python3 -m unittest discover -s tests`
- `jarvis --help`

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
