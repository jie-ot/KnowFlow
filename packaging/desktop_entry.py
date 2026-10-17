"""桌面应用入口：打包后双击即打开 KnowFlow 窗口。加 --browser 参数则改用浏览器打开。"""

import os
import sys


def unblock_bundle() -> None:
    """Windows：去掉解压时继承来的“来自互联网”标记（Zone.Identifier）。

    用资源管理器解压下载的 zip 时，每个文件都会带上这个标记，而 .NET 拒绝加载带标记的 DLL，
    pywebview 依赖的 Python.Runtime.dll 就会报 “Failed to resolve Python.Runtime.Loader.Initialize”。
    效果等同于在文件属性里勾选“解除锁定”。改不了的文件跳过即可。
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    roots = {os.path.dirname(sys.executable), getattr(sys, "_MEIPASS", "")}
    for root in filter(None, roots):
        for dirpath, _, files in os.walk(root):
            for name in files:
                if name.lower().endswith((".dll", ".exe", ".pyd", ".config")):
                    try:
                        os.remove(os.path.join(dirpath, name) + ":Zone.Identifier")
                    except OSError:
                        pass


if __name__ == "__main__":
    unblock_bundle()
    from app.launcher import main

    args = sys.argv[1:]
    if "--browser" in args:
        args.remove("--browser")
    else:
        args.insert(0, "--app")
    sys.exit(main(args))
