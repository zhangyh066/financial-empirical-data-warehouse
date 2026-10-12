"""
EFDW 全局配置中心
=================
集中定义项目路径常量、表名校验与日志配置，
供 api.py / run_pipeline.py / export.py / etl/import_raw.py 统一引用，
避免硬编码路径散落各处。
"""

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ─────────────────────────────────────────────
# 路径常量（全部基于项目根目录解析，与当前工作目录无关）
# ─────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DB_PATH = PROJECT_ROOT / "data" / "warehouse" / "financial_warehouse.db"

RAW_DIR = PROJECT_ROOT / "data" / "raw"
PENDING_DIR = RAW_DIR / "pending"      # 待导入文件目录
IMPORTED_DIR = RAW_DIR / "imported"    # 已导入归档目录
WAREHOUSE_DIR = PROJECT_ROOT / "data" / "warehouse"
BACKUP_DIR = WAREHOUSE_DIR / "backups"  # 数据库自动备份目录
TMP_DIR = PROJECT_ROOT / "data" / "tmp"

SQL_DIR = PROJECT_ROOT / "src" / "sql"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
LOG_DIR = PROJECT_ROOT / "logs"

# 数据库备份保留份数（全量重建前自动备份，仅保留最近 N 份）
BACKUP_KEEP = 5


# ─────────────────────────────────────────────
# 表名合法性校验（防止 SQL 注入）
# ─────────────────────────────────────────────

_TABLE_PART_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_valid_table_name(table_name: str) -> bool:
    """校验表名（可带一层 schema 前缀，如 ods.my_table）是否为合法标识符"""
    if not table_name:
        return False
    parts = table_name.split(".")
    if len(parts) > 2:
        return False
    return all(_TABLE_PART_RE.match(part) for part in parts)


# ─────────────────────────────────────────────
# 日志配置：同时输出到控制台与 logs/efdw.log（滚动保留 3 份）
# ─────────────────────────────────────────────

def get_logger(name: str) -> logging.Logger:
    """获取项目统一日志器（重复调用安全，不会重复添加 handler）"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_DIR / "efdw.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
