"""全局配置：数据目录、限额和默认设置。

数值来源见 docs/合并需求说明.md 第 4 节（合并后的限额）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_VERSION = "0.2.0"

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
WEB_DIR = Path(__file__).resolve().parent / "web"  # 编译好的前端静态网页


def _default_data_dir() -> Path:
    """源码运行时数据放在 backend/data；打包成桌面应用后放到系统的用户数据目录。"""
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / "KnowFlow"
        if os.name == "nt":
            return Path(os.environ.get("APPDATA", Path.home())) / "KnowFlow"
        return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "KnowFlow"
    return BASE_DIR / "data"


DATA_DIR = Path(os.environ.get("KNOWFLOW_DATA_DIR", _default_data_dir()))
DB_PATH = DATA_DIR / "knowflow.db"
MATERIALS_DIR = DATA_DIR / "materials"

# 只允许本机页面访问（同学文档 NFR-10）：127.0.0.1 或 localhost 的任意端口
LOCAL_ORIGIN_REGEX = r"^http://(127\.0\.0\.1|localhost)(:\d+)?$"

# 上传限额（同学文档 NFR-01）
MAX_FILES_PER_BATCH = 5
MAX_FILE_BYTES = 20_000_000  # 20 MB，十进制

# 回收站保留天数（D6）；已结束的 AI 草稿保留天数（设计文档 DD-10）
TRASH_RETENTION_DAYS = 30
DRAFT_RETENTION_DAYS = 7

# 同时进行的 AI 请求上限（设计文档 4.5）
MAX_CONCURRENT_AI = 2

# 可在设置页修改的默认值
DEFAULT_SETTINGS: dict[str, object] = {
    "max_input_chars": 16000,  # 单次 AI 输入字数上限（NFR-09）
    "idle_timeout_s": 60,  # 首字或相邻数据的最长等待（NFR-04）
    "total_timeout_s": 120,  # 单次调用总时长上限（同学文档 NFR-04）
    "temperature": 0.3,
    "day_start_hour": 4,  # 凌晨 4 点前算前一天（D5）
}

COURSE_COLORS = ["blue", "green", "orange", "purple", "red", "teal", "brown", "pink"]
