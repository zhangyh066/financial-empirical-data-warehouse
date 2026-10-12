"""
EFDW 一键数据导出模块 (DWS -> Stata/CSV)
=========================================
默认导出数据库中实际存在的全部年份（剔除金融业），
也可通过 CLI 参数 --start / --end 指定研究窗口：

  python src/export.py                  # 导出全部年份
  python src/export.py --start 2019 --end 2022
"""

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

# 兼容 `python src/export.py` 直接运行：把项目根目录加入模块搜索路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DB_PATH, WAREHOUSE_DIR, get_logger

logger = get_logger("efdw.export")

EXPORT_DTA_PATH = WAREHOUSE_DIR / "regression_panel_data.dta"
EXPORT_CSV_PATH = WAREHOUSE_DIR / "regression_panel_data.csv"


def resolve_year_window(start: int | None, end: int | None) -> tuple[int, int]:
    """确定导出年份窗口：未指定时取数据库中实际存在的全部年份"""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        db_min, db_max = conn.execute(
            "SELECT MIN(year), MAX(year) FROM dws.firm_year_panel"
        ).fetchone()
    finally:
        conn.close()
    year_min = start if start is not None else db_min
    year_max = end if end is not None else db_max
    return int(year_min), int(year_max)


def export_to_stata(start: int | None = None, end: int | None = None):
    print("=== 开始运行一键数据导出模块 (DWS -> ADS Stata/CSV) ===")

    if not DB_PATH.exists():
        print(f"[错误] 数据库 {DB_PATH} 不存在，请先运行 Pipeline 脚本。")
        return

    year_min, year_max = resolve_year_window(start, end)
    print(f"[窗口] 本次导出年份窗口: {year_min} - {year_max}（默认取数据库实际全部年份，可用 --start/--end 指定）")
    logger.info("导出年份窗口: %s - %s", year_min, year_max)

    conn = duckdb.connect(str(DB_PATH), read_only=True)

    # 1. 选定回归常用变量，并过滤金融业（实证论文惯例）
    print("[查询] 正在从 DWS 层拉取回归所需面板数据...")
    sql_query = """
        SELECT
            stkcd,
            company_name,
            year,
            industry_category,
            firm_size,
            leverage,
            roa,
            roe,
            gross_margin,
            revenue_growth,
            asset_growth,
            current_ratio,
            quick_ratio,
            interest_coverage,
            total_asset_turnover,
            inventory_turnover,
            cash_conversion_cycle,
            ocf_to_assets,
            tobin_q,
            lag_roa,
            lag2_roa,
            lag_roe,
            lag_leverage,
            lead_roa
        FROM dws.firm_year_panel
        WHERE year BETWEEN ? AND ?
          AND industry_category != '金融业'
        ORDER BY stkcd, year;
    """

    df = conn.execute(sql_query, [year_min, year_max]).fetchdf()
    conn.close()

    # 2. Stata 格式学术细节优化：变量名小写（Stata 14+ 支持 UTF-8 中文内容）
    df.columns = [col.lower() for col in df.columns]

    print(f"[导出] 正在将 {len(df)} 行数据转换为 Stata dta 格式...")
    try:
        df.to_stata(
            str(EXPORT_DTA_PATH),
            write_index=False,
            version=118,  # Stata 14+，支持 unicode/中文
            convert_dates=None
        )
        print(f"[成功] Stata 数据集已导出至: {EXPORT_DTA_PATH}")
    except Exception as e:
        print(f"[警告] 导出 Stata 失败 (可能未安装 pyarrow 或依赖库): {e}")

    # 同时备份一份万能的 CSV 格式
    df.to_csv(str(EXPORT_CSV_PATH), index=False, encoding="utf-8-sig")
    print(f"[成功] CSV 数据集已同步备份至: {EXPORT_CSV_PATH}")
    logger.info("导出完成：%d 行（%d-%d 年）", len(df), year_min, year_max)

    print("\n=== 回归数据集导出全部完成！现在你可以直接在 Stata 中运行 `use regression_panel_data.dta, clear` 了 ===")


def main():
    parser = argparse.ArgumentParser(description="EFDW 一键导出回归面板数据 (Stata/CSV)")
    parser.add_argument("--start", type=int, default=None, help="起始年份（默认取数据库最小年份）")
    parser.add_argument("--end", type=int, default=None, help="结束年份（默认取数据库最大年份）")
    args = parser.parse_args()
    export_to_stata(start=args.start, end=args.end)


if __name__ == "__main__":
    main()
