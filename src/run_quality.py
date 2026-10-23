"""
EFDW 财务勾稽关系校验（单独执行 ads_quality.sql）
==================================================
导入新数据后无需重跑全量 Pipeline，只重建质检报告表：
  python src/run_quality.py
"""

import sys
from pathlib import Path

import duckdb

# 兼容 `python src/run_quality.py` 直接运行：把项目根目录加入模块搜索路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DB_PATH, SQL_DIR, get_logger

logger = get_logger("efdw.quality")


def run_quality():
    logger.info("=== 开始运行财务勾稽关系校验 (ads_quality.sql) ===")

    if not DB_PATH.exists():
        raise RuntimeError(
            f"数据库 {DB_PATH} 不存在，请先运行 python src/initialize_db.py 与数据导入。"
        )

    conn = duckdb.connect(str(DB_PATH))
    try:
        conn.execute((SQL_DIR / "ads_quality.sql").read_text(encoding="utf-8"))
    finally:
        conn.close()

    logger.info("=== 勾稽校验完成 (ads.data_quality_report 已重建) ===")


if __name__ == "__main__":
    try:
        run_quality()
    except Exception as e:
        logger.error("勾稽校验失败: %s", e)
        sys.exit(1)
