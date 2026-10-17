#!/bin/bash
# KnowFlow 一键安装 / 更新（macOS，Apple 芯片）
#
#   curl -fsSL https://raw.githubusercontent.com/jie-ot/KnowFlow/main/install.sh | bash
#
# 用 curl 下载的文件不带隔离标记，打开时不会提示“无法验证开发者”或“已损坏”。
# 重复运行即更新到最新版，数据（~/Library/Application Support/KnowFlow）不受影响。
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "这个脚本只用于 macOS。其他系统请用源码运行：python3 run.py --app" >&2
  exit 1
fi
if [ "$(uname -m)" != "arm64" ]; then
  echo "目前只打包了 Apple 芯片版本。Intel Mac 请用源码运行：python3 run.py --app" >&2
  exit 1
fi

url="https://github.com/jie-ot/KnowFlow/releases/latest/download/KnowFlow-macOS.zip"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "[KnowFlow] 正在下载最新版本…"
curl -fL --progress-bar "$url" -o "$tmp/KnowFlow.zip"
ditto -x -k "$tmp/KnowFlow.zip" "$tmp"

dest="/Applications"
[ -w "$dest" ] || dest="$HOME/Applications"
mkdir -p "$dest"
pkill -x KnowFlow 2>/dev/null || true
rm -rf "$dest/KnowFlow.app"
mv "$tmp/KnowFlow.app" "$dest/"
xattr -dr com.apple.quarantine "$dest/KnowFlow.app" 2>/dev/null || true

echo "[KnowFlow] 已安装到 $dest/KnowFlow.app"
open "$dest/KnowFlow.app"
