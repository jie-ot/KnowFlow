# KnowFlow

本地运行的学习工作台，支持资料整理、Markdown 笔记、知识图谱、间隔复习与学习计划，使用 FastAPI、SQLite 和 Next.js。

安装 Python 3.11+ 后，Windows 双击 `start.bat`，macOS / Linux 运行 `bash start.sh`，或直接运行 `python run.py`。默认打开演示工作区，首次启动联网安装 Python 依赖；仓库包含编译好的网页，无需安装 Node.js。

演示快照来自 2026-10-30，包含 4 个学科、32 篇笔记、15 份资料、39 张卡片和学习计划。首次启动自动载入，后续启动保留本机修改。共享快照位于 `demo/share/`，本机演示数据写入 `demo/.local/`；模型 API Key 需在网页设置中自行配置。

需要恢复演示起始场景时，先关闭服务，再运行 `python demo/run_demo.py --reset`。当前演示数据库会先备份，本机模型配置保留。`start-demo.bat` 仍可直接启动演示。

原有日常工作区使用 `python run.py --personal`，数据仍在 `backend/data/`。桌面窗口使用 `python run.py --app`。

开发前端需要 Node.js 20.9+，运行 `python run.py --dev` 即可同时启动前后端并载入演示数据；使用日常数据时加 `--personal`。前端修改后在 `frontend/` 运行 `npm ci` 和 `npm run build`，将最新网页复制到 `backend/app/web/` 后一并提交。

桌面应用可在 GitHub Actions 中手动运行“打包桌面应用”，或在本机安装 `packaging/requirements-build.txt` 后运行 `python packaging/build_desktop.py`。

Git 仅跟踪功能源码、运行与构建配置、必要网页和演示快照。测试、说明文档、截图、验收材料、本机数据、缓存与发布包均忽略。
