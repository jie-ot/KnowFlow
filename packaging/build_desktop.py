"""把 KnowFlow 打包成桌面应用：macOS 得到 dist/KnowFlow.app，Windows 得到 dist/KnowFlow/KnowFlow.exe。

PyInstaller 只能打包当前系统的版本：在 Mac 上运行得到 Mac 版，在 Windows 上运行得到 Windows 版。
推到 GitHub 后也可以用 .github/workflows/desktop.yml 自动同时打出两个版本。

用法（在仓库根目录）：
    python run.py --app               # 先确认能正常打开窗口，同时会装好依赖
    backend/.venv/bin/pip install pyinstaller==6.22.3      # Windows: backend\\.venv\\Scripts\\pip
    backend/.venv/bin/python packaging/build_desktop.py    # Windows: backend\\.venv\\Scripts\\python
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
HERE = Path(__file__).resolve().parent

sys.path.insert(0, str(HERE))
from make_icon import build as build_icon  # noqa: E402


def main() -> None:
    web = BACKEND / "app" / "web"
    if not (web / "index.html").exists():
        sys.exit("没有找到 backend/app/web/index.html，请先在 frontend 目录运行 npm run build")
    icons = build_icon(ROOT / "build" / "icon")
    icon = icons["icns"] if sys.platform == "darwin" else icons["ico"]
    args = [
        str(HERE / "desktop_entry.py"),
        "--name", "KnowFlow",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--paths", str(BACKEND),
        "--add-data", f"{web}{os.pathsep}app/web",
        "--collect-submodules", "app",
        "--collect-submodules", "uvicorn",
        "--collect-data", "latex2mathml",
        "--collect-data", "pptx",
        "--collect-data", "docx",
        "--icon", str(icon),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
    ]
    if sys.platform == "darwin":
        args += ["--osx-bundle-identifier", "com.knowflow.app"]
    PyInstaller.__main__.run(args)
    target = ROOT / "dist" / ("KnowFlow.app" if sys.platform == "darwin" else "KnowFlow")
    print(f"\n打包完成：{target}")


if __name__ == "__main__":
    main()
