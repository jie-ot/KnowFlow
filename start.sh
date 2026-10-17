#!/usr/bin/env bash
# macOS / Linux 启动：等同于 python3 run.py；加 --app 用桌面窗口打开
cd "$(dirname "$0")"
for py in python3.13 python3.12 python3.11 python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    exec "$py" run.py "$@"
  fi
done
echo "需要 Python 3.11 或更新版本：https://www.python.org/downloads/"
exit 1
