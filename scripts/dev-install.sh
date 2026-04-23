#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install -e "${ROOT_DIR}"

cat <<EOF
Jarvis 安装完成。

下一步：
  source "${VENV_DIR}/bin/activate"
  jarvis

如果你在 IDE 里打开这个项目，把终端切到项目根目录后激活这个 venv，就能直接输入 jarvis。
EOF
