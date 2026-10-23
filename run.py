#!/usr/bin/env python3
"""KnowFlow 一条命令启动（macOS / Windows / Linux 通用，只需要 Python 3.11+）。

    python run.py          用浏览器打开（网页端）
    python run.py --app    用独立窗口打开（桌面端）
    python run.py --dev    同时启动 React 开发前端和 FastAPI（需要 Node.js 20.9+）

第一次运行会在 backend/.venv 里创建虚拟环境并安装依赖，之后直接启动。
默认使用官方 PyPI，可用 PIP_INDEX 环境变量指定其他镜像。
"""

from __future__ import annotations

import hashlib
import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
VENV = BACKEND / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = VENV / ".knowflow-deps"
PIP_INDEX = os.environ.get("PIP_INDEX", "https://pypi.org/simple")


def say(msg: str) -> None:
    print(f"[KnowFlow] {msg}", flush=True)


def free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return sock.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("找不到可用端口")


def run_dev(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="同时启动 KnowFlow 前后端")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--port", type=int, default=8000, help="后端首选端口")
    parser.add_argument("--frontend-port", type=int, default=3000, help="前端首选端口")
    args = parser.parse_args(argv)
    node, npm = shutil.which("node"), shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not node or not npm:
        say("开发模式需要 Node.js 20.9 或更新版本：https://nodejs.org")
        return 1
    version = subprocess.check_output([node, "--version"], text=True).strip().lstrip("v")
    if tuple(map(int, version.split(".")[:2])) < (20, 9):
        say(f"Node.js {version} 太旧，请安装 20.9 或更新版本。")
        return 1
    frontend = ROOT / "frontend"
    next_bin = frontend / "node_modules" / "next" / "dist" / "bin" / "next"
    stamp = frontend / "node_modules" / ".knowflow-deps"
    digest = hashlib.sha256((frontend / "package-lock.json").read_bytes() + (frontend / "package.json").read_bytes()).hexdigest()
    if not next_bin.exists() or not stamp.exists() or stamp.read_text() != digest:
        say("安装前端依赖（首次运行或依赖变更时）…")
        if subprocess.call([npm, "ci", "--no-audit", "--no-fund", "--cache", str(VENV / "npm-cache")], cwd=frontend):
            say("前端依赖安装失败，请检查网络后重试。")
            return 1
        stamp.write_text(digest)
    backend_port = free_port(args.port)
    frontend_port = free_port(args.frontend_port)
    if frontend_port == backend_port:
        frontend_port = free_port(0)
    api_url = f"http://127.0.0.1:{backend_port}"
    url = f"http://127.0.0.1:{frontend_port}"
    env = {**os.environ, "NEXT_PUBLIC_API_BASE": api_url, "PYTHONIOENCODING": "utf-8", "NEXT_TELEMETRY_DISABLED": "1"}
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    processes: list[subprocess.Popen] = []
    try:
        processes.append(subprocess.Popen([str(PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(backend_port)], cwd=BACKEND, env=env))
        processes.append(subprocess.Popen([node, str(next_bin), "dev", "--hostname", "127.0.0.1", "--port", str(frontend_port)], cwd=frontend, env=env))
        say(f"前端 {url} · 后端 {api_url}，正在等待就绪…")
        pending_urls = [api_url + "/api/health", url]
        deadline = time.monotonic() + 120
        while pending_urls and time.monotonic() < deadline:
            if any(p.poll() is not None for p in processes):
                say("服务启动失败，请查看上面的错误。")
                return 1
            try:
                with direct.open(pending_urls[0], timeout=2) as response:
                    if response.status == 200:
                        pending_urls.pop(0)
            except OSError:
                time.sleep(.25)
        if pending_urls:
            say("启动超时，请查看上面的错误后重试。")
            return 1
        say(f"已启动：{url}；按 Ctrl+C 同时停止前后端。")
        if not args.no_open:
            webbrowser.open(url)
        while all(p.poll() is None for p in processes):
            time.sleep(.5)
        say("一个服务已退出，正在停止另一个服务。")
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                if os.name == "nt":
                    # 只结束本脚本创建的进程树，包括 Next.js 的子进程。
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


def main() -> int:
    if PY.exists() and Path(sys.executable).resolve() != PY.resolve():
        return subprocess.call([str(PY), str(Path(__file__).resolve()), *sys.argv[1:]])
    if sys.version_info < (3, 11):
        say(f"需要 Python 3.11 或更新版本，当前是 {sys.version.split()[0]}。请从 https://www.python.org 安装。")
        return 1

    desktop = "--app" in sys.argv[1:]
    dev = "--dev" in sys.argv[1:]
    if desktop and dev:
        say("--app 和 --dev 不能同时使用。")
        return 1
    req_files = [BACKEND / "requirements.txt"] + ([BACKEND / "requirements-desktop.txt"] if desktop else [])
    digest = hashlib.sha256(b"".join(f.read_bytes() for f in req_files)).hexdigest()

    if VENV.exists() and not PY.exists():
        say("backend/.venv 不是当前系统的虚拟环境，请先将它移到别处后重试。")
        return 1
    if not PY.exists():
        say("第一次运行：创建 Python 虚拟环境…")
        venv.create(VENV, with_pip=True)

    if subprocess.call([str(PY), "-m", "pip", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
        subprocess.call([str(PY), "-m", "ensurepip", "--upgrade"])  # 例如 uv 创建的虚拟环境不带 pip

    installed = STAMP.read_text().split() if STAMP.exists() else []
    if digest not in installed:
        say(f"安装依赖（源：{PIP_INDEX}），第一次大约需要 1–2 分钟…")
        cmd = [str(PY), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", "-i", PIP_INDEX]
        for f in req_files:
            cmd += ["-r", str(f)]
        if subprocess.call(cmd) != 0:
            say("依赖安装失败。检查网络后重新运行；中途失败不要紧，下次会接着装。")
            return 1
        STAMP.write_text(" ".join(installed + [digest]))
        installed.append(digest)

    # 文字识别组件是可选的：装不上（比如很老的系统）也不影响其他功能
    ocr_req = BACKEND / "requirements-ocr.txt"
    ocr_digest = "ocr-" + hashlib.sha256(ocr_req.read_bytes()).hexdigest()[:16]
    if ocr_digest not in installed and not dev and not os.environ.get("KNOWFLOW_NO_OCR"):
        say("安装图片文字识别组件（约 110 MB，只需一次）…")
        cmd = [str(PY), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", "-i", PIP_INDEX, "-r", str(ocr_req)]
        if subprocess.call(cmd) == 0:
            STAMP.write_text(" ".join(installed + [ocr_digest]))
        else:
            say("文字识别组件没装上，先跳过：图片里的文字需要手动补充，下次启动会再试。")

    if dev:
        return run_dev([a for a in sys.argv[1:] if a != "--dev"])
    return subprocess.call([str(PY), "-m", "app.launcher", *sys.argv[1:]], cwd=BACKEND)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
