#!/usr/bin/env bash
# KnowFlow 一键启动：后端 127.0.0.1:8000，前端 127.0.0.1:3000
#   ./start.sh        开发模式
#   ./start.sh prod   生产模式
# 默认使用国内镜像；想用官方源可设置 PIP_INDEX=https://pypi.org/simple NPM_REGISTRY=https://registry.npmjs.org
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
PIP_INDEX="${PIP_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"

need() { command -v "$1" >/dev/null 2>&1; }

if ! need node; then
  echo "没找到 Node.js。请先安装 Node 20 或更新版本：brew install node 或 https://nodejs.org"; exit 1
fi
NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
if [ "$NODE_MAJOR" -lt 20 ]; then echo "Node 版本太旧（$(node -v)），需要 20 或更新"; exit 1; fi

# ---------- 后端 ----------
cd "$ROOT/backend"
if need uv; then
  echo "▸ 检查后端依赖（uv）…"
  UV_DEFAULT_INDEX="${UV_DEFAULT_INDEX:-$PIP_INDEX}" uv sync
  BACKEND=(uv run uvicorn app.main:app --host 127.0.0.1 --port 8000)
else
  # 以 uvicorn 是否存在判断是否装完，中途中断后再运行会继续安装
  if [ ! -x .venv/bin/uvicorn ]; then
    echo "▸ 安装后端依赖（python3 + pip，源：$PIP_INDEX）…"
    [ -d .venv ] || python3 -m venv .venv
    .venv/bin/pip install --disable-pip-version-check -i "$PIP_INDEX" \
      fastapi "uvicorn[standard]" httpx python-multipart "pypdf[crypto]" python-pptx python-docx \
      markdown-it-py mdit-py-plugins latex2mathml pygments
  fi
  BACKEND=(.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000)
fi

# ---------- 前端 ----------
cd "$ROOT/frontend"
if [ ! -x node_modules/.bin/next ]; then
  echo "▸ 安装前端依赖（源：$NPM_REGISTRY，约 1–3 分钟）…"
  npm install --no-audit --no-fund --registry="$NPM_REGISTRY"
fi

echo "▸ 启动后端…"
cd "$ROOT/backend"
"${BACKEND[@]}" &
BACK_PID=$!

echo "▸ 启动前端…"
cd "$ROOT/frontend"
if [ "${1:-dev}" = "prod" ]; then npm run build && npm run start & else npm run dev & fi
FRONT_PID=$!

trap 'kill $BACK_PID $FRONT_PID 2>/dev/null; exit 0' INT TERM

for _ in $(seq 1 90); do
  if curl -s -o /dev/null http://127.0.0.1:3000 2>/dev/null; then break; fi
  sleep 1
done
echo ""
echo "  ✅ KnowFlow 已启动：http://127.0.0.1:3000   （按 Ctrl+C 退出）"
echo ""
(need open && open "http://127.0.0.1:3000") || true
wait
