#!/usr/bin/env python3
"""KnowFlow 一条命令启动（macOS / Windows / Linux 通用，只需要 Python 3.11+）。

    python run.py          用浏览器打开（网页端）
    python run.py --app    用独立窗口打开（桌面端）

第一次运行会在 backend/.venv 里创建虚拟环境并安装依赖，之后直接启动。
默认用清华 pip 镜像，想用官方源：PIP_INDEX=https://pypi.org/simple python run.py
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
VENV = BACKEND / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = VENV / ".knowflow-deps"
PIP_INDEX = os.environ.get("PIP_INDEX", "https://pypi.tuna.tsinghua.edu.cn/simple")


def say(msg: str) -> None:
    print(f"[KnowFlow] {msg}", flush=True)


def main() -> int:
    if sys.version_info < (3, 11):
        say(f"需要 Python 3.11 或更新版本，当前是 {sys.version.split()[0]}。请从 https://www.python.org 安装。")
        return 1

    desktop = "--app" in sys.argv[1:]
    req_files = [BACKEND / "requirements.txt"] + ([BACKEND / "requirements-desktop.txt"] if desktop else [])
    digest = hashlib.sha256(b"".join(f.read_bytes() for f in req_files)).hexdigest()

    if VENV.exists() and not PY.exists():
        # 从别的系统拷来的虚拟环境（macOS 是 bin/，Windows 是 Scripts/）不能用，重建
        say("发现不是这台电脑创建的虚拟环境，正在重建…")
        shutil.rmtree(VENV)
    if not PY.exists():
        say("第一次运行：创建 Python 虚拟环境…")
        venv.create(VENV, with_pip=True)

    if subprocess.call([str(PY), "-m", "pip", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
        subprocess.call([str(PY), "-m", "ensurepip", "--upgrade"])  # 例如 uv 创建的虚拟环境不带 pip

    installed = STAMP.read_text().split() if STAMP.exists() else []
    if digest not in installed:
        say(f"安装依赖（源：{PIP_INDEX}），第一次大约需要 1–2 分钟…")
        cmd = [str(PY), "-m", "pip", "install", "--disable-pip-version-check", "-i", PIP_INDEX]
        for f in req_files:
            cmd += ["-r", str(f)]
        if subprocess.call(cmd) != 0:
            say("依赖安装失败。检查网络后重新运行；中途失败不要紧，下次会接着装。")
            return 1
        STAMP.write_text(" ".join(installed + [digest]))

    return subprocess.call([str(PY), "-m", "app.launcher", *sys.argv[1:]], cwd=BACKEND)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
