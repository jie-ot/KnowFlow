"""桌面应用入口：打包后双击即打开 KnowFlow 窗口。加 --browser 参数则改用浏览器打开。"""

import sys

from app.launcher import main

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--browser" in args:
        args.remove("--browser")
    else:
        args.insert(0, "--app")
    sys.exit(main(args))
