"""全局配置：数据目录、限额和默认设置。

数值来源见 docs/合并需求说明.md 第 4 节（合并后的限额）。
"""

from __future__ import annotations

import os
from pathlib import Path

APP_VERSION = "0.1.0"

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
DATA_DIR = Path(os.environ.get("KNOWFLOW_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "knowflow.db"
MATERIALS_DIR = DATA_DIR / "materials"

# 只允许本机前端访问（同学文档 NFR-10）
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "KNOWFLOW_ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if o.strip()
]

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
