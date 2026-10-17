# KnowFlow

面向多学科学习的 AI 知识管理与智能复习助手（基础版）。单用户、在本机运行的网页应用：

- **笔记**：Markdown 编辑与预览，支持 LaTeX 公式、代码高亮、表格；学科与标签；自动保存；回收站保留 30 天；修订历史可撤销 AI 修改。
- **资料**：上传 PDF、PPTX、DOCX、TXT、MD、代码、图片，按页、幻灯片、段落、行切成片段；勾选片段让 AI 整理成带出处的笔记，点出处能回到原文位置。
- **AI**：整理、摘要（100 / 300 字）、提炼知识点卡片、资料成笔记、计划拆任务。结果都先进对比或审核视图，确认后才写入；整理和摘要会自动核对数字、公式、代码是否被改。
- **复习**：SM-2 间隔重复，凌晨 4 点前算前一天；空格翻面，1 / 2 / 3 自评。
- **计划**：你定目标、截止日和每天可用时间，AI 只出草稿；超出当天预算或越过截止日的任务不能采纳。
- **检索与导出**：⌘K 全局搜索；单篇导出 .md；多篇导出为一个离线 HTML（可打印）。

合并了两份需求文档，取舍见 [docs/合并需求说明.md](docs/合并需求说明.md)。

## 启动

需要 Node.js 20+ 和 Python 3.11+（有 [uv](https://docs.astral.sh/uv/) 时 macOS/Linux 会优先用 uv）。

macOS / Linux：

```bash
cd KnowFlow
./start.sh          # 开发模式，改代码会自动刷新
./start.sh prod     # 生产模式，页面切换更快
```

Windows：双击 `start.bat`，或在命令行里运行它。安装 Python 时要勾选“Add python.exe to PATH”。后端和前端会各开一个窗口，关掉这两个窗口就退出。

首次运行会安装依赖，然后自动打开 http://127.0.0.1:3000 。第一次打开有两门示例学科、几张今天到期的卡片和一个示例计划，删掉示例学科即可清空。

脚本默认用清华 pip 源和 npmmirror npm 源；想用官方源：

```bash
PIP_INDEX=https://pypi.org/simple NPM_REGISTRY=https://registry.npmjs.org ./start.sh
```

## 连接大模型

设置 › 大模型 › 添加配置。默认填好了 DeepSeek 的 Base URL（`https://api.deepseek.com`）和模型名 `deepseek-flash`，只需填 API Key，点“测试连接”。任何 OpenAI 兼容接口都可以（阿里云百炼、Kimi 等）。

API Key 只存在本机 `backend/data/knowflow.db`，页面只显示后 4 位，不写日志。第一次使用 AI 前会提示内容将发送给哪个服务商。

## 目录

```
backend/    FastAPI + SQLite（数据在 backend/data/）
  app/logic/  SM-2、逻辑日、资料解析、检索、导出、计划校验、事实核对
  app/llm/    OpenAI 兼容网关（流式、超时、错误映射）与提示词
  tests/      关键流程测试：cd backend && uv run pytest
frontend/   Next.js + Tailwind
docs/       合并需求说明
```

后端接口文档：启动后打开 http://127.0.0.1:8000/docs 。
