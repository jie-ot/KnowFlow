"""启动 KnowFlow：在本机起一个服务，然后用浏览器或桌面窗口打开。

    python -m app.launcher            用浏览器打开（网页端）
    python -m app.launcher --app      用独立窗口打开（桌面端，需要 pywebview）
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
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


def open_window(url: str) -> None:
    """用 pywebview 打开独立窗口，关闭窗口后返回。"""
    import webview

    if sys.platform == "win32":
        from webview.platforms import winforms  # 先加载 .NET 组件，失败直接抛出

        if winforms.renderer != "edgechromium":  # 没有 WebView2 时会退回 IE 内核，页面跑不起来
            raise RuntimeError(f"WebView2 不可用（{winforms.renderer}）")
    webview.settings["ALLOW_DOWNLOADS"] = True  # 导出 .md / HTML 需要
    webview.create_window("KnowFlow", url, width=1320, height=860, min_size=(980, 640))
    webview.start()


def _chromium() -> str | None:
    """找一个支持 --app 窗口模式的浏览器：Windows 自带的 Edge，或者 Chrome。"""
    found: list[str] = []
    if sys.platform == "win32":
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                found += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                          os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    elif sys.platform == "darwin":
        found += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    else:
        found += [p for p in map(shutil.which, ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")) if p]
    return next((p for p in found if os.path.exists(p)), None)


def browser_window(url: str) -> None:
    """备用方案：用 Edge / Chrome 的“应用窗口”打开（没有地址栏），关闭窗口后返回。"""
    exe = _chromium()
    if exe:
        profile = config.DATA_DIR / "window-profile"  # 独立配置目录，保证是一个单独的进程，能等到它关闭
        try:
            subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1320,860",
                              "--no-first-run", "--no-default-browser-check"]).wait()
            return
        except OSError:
            pass
    webbrowser.open(url)
    notify(f"KnowFlow 已在浏览器中打开：\n{url}\n\n用完后点「确定」退出。")


def notify(text: str) -> None:
    """打包后的程序没有控制台，用系统对话框提示；源码运行时打印到终端并等待。"""
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text, "KnowFlow", 0x40)
    else:
        print(text, file=sys.stderr, flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="knowflow", description="KnowFlow 智能复习助手")
    parser.add_argument("--app", action="store_true", help="用独立的桌面窗口打开")
    parser.add_argument("--port", type=int, default=8000, help="端口，被占用时自动换一个（默认 8000）")
    parser.add_argument("--no-open", action="store_true", help="只启动服务，不自动打开浏览器")
    args = parser.parse_args(argv)

    # Windows 打包成无控制台程序后 sys.stdout / sys.stderr 是 None，uvicorn 的日志会因此崩溃
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    if not config.WEB_DIR.exists():
        print("没有找到编译好的前端（backend/app/web）。请在 frontend 目录运行 npm run build，"
              "或者用 npm run dev 单独启动前端。", file=sys.stderr)

    from .main import app

    port = free_port(args.port)
    url = f"http://127.0.0.1:{port}/"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None))

    if args.app:
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        if not wait_until_ready(url):
            notify(f"后端没有在 30 秒内启动，可以稍后用浏览器访问 {url}")
            return 1
        try:
            open_window(url)  # 关闭窗口后返回
        except Exception as exc:  # 窗口组件起不来（没装 pywebview、缺 WebView2、DLL 被系统拦截……）
            print(f"桌面窗口启动失败（{exc!r}），改用浏览器窗口打开", file=sys.stderr)
            browser_window(url)
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
