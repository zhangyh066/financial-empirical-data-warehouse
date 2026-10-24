"""
EFDW 数仓清洗与分析 Pipeline (DWD -> DWS -> ADS)
=================================================
全量重建前自动备份现有数据库到 data/warehouse/backups/（仅保留最近 5 份）。
四层 SQL 在同一事务内执行：任何一步失败自动回滚，已 DROP 的旧表不会丢失；
写连接由 try/finally 保证关闭，避免异常时锁库。
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

SQL_STEPS = [
    ("DWD", "dwd_cleaning.sql", "DWD 维度表与明细事实表"),
    ("DWS", "dws_panel.sql", "DWS 企业-年度财务面板宽表"),
    ("ADS", "ads_marts.sql", "ADS 行业聚合应用数据集市"),
    ("ADS", "ads_quality.sql", "ADS 财务勾稽关系校验报告"),
]

# ODS 贴源层的最低字段要求：旧抓取数据缺列时自动补 NULL 列（DWD SQL 才能引用），
# 下次「更新数据」重抓后自然填充真实值
ODS_REQUIRED_COLUMNS = {
    "ods.financial_raw": [
        "F010101A", "F010201A", "F010301A", "F010401A",
        "F010701A", "F010801A",
        "F011001A", "F011101A", "F011201A", "F011301A", "F010901A",
        "F020101A", "F020201A", "F020301A", "F020401A", "F020501A",
        "F030101A",
        "F040101A", "F040201A", "F040301A",
        "F100101C",
    ],
}


def _ensure_ods_columns(conn) -> None:
    """为历史 ODS 表补齐缺失字段（补 NULL），保证 DWD SQL 向前兼容旧数据"""
    for table, columns in ODS_REQUIRED_COLUMNS.items():
        if not conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema || '.' || table_name = ?",
            [table],
        ).fetchone()[0]:
            continue
        existing = {
            r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema || '.' || table_name = ?",
                [table],
            ).fetchall()
        }
        for col in columns:
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} VARCHAR;")
                logger.info("[迁移] %s 缺列 %s，已自动补 NULL 列（重抓数据后填充）", table, col)


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


def _rebuild_warehouse(conn) -> None:
    """在单事务内执行四层重建 SQL：失败自动回滚，旧表不会被半途 DROP 丢失"""
    conn.execute("BEGIN TRANSACTION")
    try:
        _ensure_ods_columns(conn)
        for layer, sql_file, desc in SQL_STEPS:
            sql_path = SQL_DIR / sql_file
            logger.info("[%s] 正在执行: %s ...", layer, sql_path)
            conn.execute(sql_path.read_text(encoding="utf-8"))
            logger.info("[成功] %s构建成功！", desc)
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            logger.warning("事务回滚失败，数据库可能处于中间状态，可从 backups/ 恢复", exc_info=True)
        raise


def run_pipeline():
    logger.info("=== 开始运行数仓清洗与分析 Pipeline (DWD -> DWS -> ADS) ===")

    if not DB_PATH.exists():
        raise RuntimeError(
            f"数据库 {DB_PATH} 不存在，请先运行 python src/initialize_db.py 与数据导入（src/etl/import_raw.py）。"
        )

    # 全量重建前自动备份现有数据库
    backup_database()

    conn = duckdb.connect(str(DB_PATH))
    try:
        _rebuild_warehouse(conn)

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
    finally:
        conn.close()

    logger.info("=== Pipeline 全部运行成功！ ===")


if __name__ == "__main__":
    try:
        run_pipeline()
    except Exception as e:
        logger.error("Pipeline 运行失败: %s", e)
        sys.exit(1)
