"""启动 KnowFlow：在本机起一个服务，然后用浏览器或桌面窗口打开。

    python -m app.launcher            用浏览器打开（网页端）
    python -m app.launcher --app      用独立窗口打开（桌面端，需要 pywebview）
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from . import config


def free_port(preferred: int) -> int:
    """优先用指定端口；被占用时让系统分配一个空闲端口。"""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("找不到可用端口")


# 访问本机时不走代理：终端里设了 http_proxy / all_proxy 时，urllib 默认会把 127.0.0.1 也发给代理
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def wait_until_ready(url: str, timeout: float = 30) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with _DIRECT.open(url + "api/health", timeout=1) as resp:
                if resp.status == 200:
                    return True
        except OSError:
            time.sleep(0.2)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="knowflow", description="KnowFlow 智能复习助手")
    parser.add_argument("--app", action="store_true", help="用独立的桌面窗口打开")
    parser.add_argument("--port", type=int, default=8000, help="端口，被占用时自动换一个（默认 8000）")
    parser.add_argument("--no-open", action="store_true", help="只启动服务，不自动打开浏览器")
    args = parser.parse_args(argv)

    if not config.WEB_DIR.exists():
        print("没有找到编译好的前端（backend/app/web）。请在 frontend 目录运行 npm run build，"
              "或者用 npm run dev 单独启动前端。", file=sys.stderr)

    from .main import app

    port = free_port(args.port)
    url = f"http://127.0.0.1:{port}/"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    if args.app:
        try:
            import webview  # pywebview
        except ImportError:
            print("桌面窗口需要 pywebview：pip install -r requirements-desktop.txt", file=sys.stderr)
            return 1
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        if not wait_until_ready(url):
            print("后端没有在 30 秒内启动", file=sys.stderr)
            return 1
        webview.settings["ALLOW_DOWNLOADS"] = True  # 导出 .md / HTML 需要
        webview.create_window("KnowFlow", url, width=1320, height=860, min_size=(980, 640))
        webview.start()  # 关闭窗口后返回
        server.should_exit = True
        thread.join(timeout=5)
        return 0

    def open_browser() -> None:
        if wait_until_ready(url):
            print(f"\n  KnowFlow 已启动：{url}   （按 Ctrl+C 退出）\n", flush=True)
        else:
            print(f"\n  后端启动较慢，如果浏览器没有打开，请手动访问 {url}\n", flush=True)
        if not args.no_open:
            webbrowser.open(url)

    print(f"[KnowFlow] 正在启动：{url}", flush=True)
    threading.Thread(target=open_browser, daemon=True).start()
    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
