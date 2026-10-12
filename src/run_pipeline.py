"""
EFDW 数仓清洗与分析 Pipeline (DWD -> DWS -> ADS)
=================================================
全量重建前自动备份现有数据库到 data/warehouse/backups/（仅保留最近 5 份）。
"""

import shutil
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

# 兼容 `python src/run_pipeline.py` 直接运行：把项目根目录加入模块搜索路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import BACKUP_DIR, BACKUP_KEEP, DB_PATH, SQL_DIR, get_logger

logger = get_logger("efdw.pipeline")


def backup_database(max_backups: int = BACKUP_KEEP) -> Path | None:
    """全量重建前备份现有数据库文件，仅保留最近 max_backups 份"""
    if not DB_PATH.exists():
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"financial_warehouse_{timestamp}.db"
    shutil.copy2(DB_PATH, backup_path)
    logger.info("数据库已备份至: %s", backup_path)

    # 清理过期备份，仅保留最近 max_backups 份
    backups = sorted(BACKUP_DIR.glob("financial_warehouse_*.db"))
    for old in backups[:-max_backups]:
        old.unlink()
        logger.info("已清理过期备份: %s", old.name)

    return backup_path


def run_pipeline():
    logger.info("=== 开始运行数仓清洗与分析 Pipeline (DWD -> DWS -> ADS) ===")

    if not DB_PATH.exists():
        logger.error("数据库 %s 不存在，请先运行初始化和导入脚本。", DB_PATH)
        return

    # 全量重建前自动备份现有数据库
    backup_database()

    conn = duckdb.connect(str(DB_PATH))

    sql_steps = [
        ("DWD", "dwd_cleaning.sql", "DWD 维度表与明细事实表"),
        ("DWS", "dws_panel.sql", "DWS 企业-年度财务面板宽表"),
        ("ADS", "ads_marts.sql", "ADS 行业聚合应用数据集市"),
        ("ADS", "ads_quality.sql", "ADS 财务勾稽关系校验报告"),
    ]

    for layer, sql_file, desc in sql_steps:
        sql_path = SQL_DIR / sql_file
        logger.info("[%s] 正在执行: %s ...", layer, sql_path)
        with open(sql_path, "r", encoding="utf-8") as f:
            conn.execute(f.read())
        logger.info("[成功] %s构建成功！", desc)

    # 展示生成的面板数据样例
    print("\n=== [验证] DWS 层企业-年度财务面板样例 (dws.firm_year_panel) ===")
    df = conn.execute("""
        SELECT stkcd, company_name, year,
               round(firm_size, 2)          AS size,
               round(leverage, 3)           AS lev,
               round(roa, 4)                AS roa,
               round(roe, 4)                AS roe,
               round(revenue_growth, 4)     AS rev_g,
               round(current_ratio, 3)      AS current,
               round(lag_roa, 4)            AS L_roa,
               round(lag2_roa, 4)           AS L2_roa
        FROM dws.firm_year_panel
        ORDER BY stkcd, year
        LIMIT 10;
    """).fetchdf()
    print(df.to_string(index=False))

    # 展示 ADS 行业聚合样例
    print("\n=== [验证] ADS 层行业-年度聚合样例 (ads.industry_year_stats) ===")
    ads_df = conn.execute("""
        SELECT industry_category, year, n_firms,
               round(avg_roa, 4) AS avg_roa,
               round(avg_leverage, 3) AS avg_lev
        FROM ads.industry_year_stats
        ORDER BY industry_category, year
        LIMIT 10;
    """).fetchdf()
    print(ads_df.to_string(index=False))

    conn.close()
    logger.info("=== Pipeline 全部运行成功！ ===")


if __name__ == "__main__":
    run_pipeline()
