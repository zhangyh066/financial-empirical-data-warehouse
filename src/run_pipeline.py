import os
import duckdb
import pandas as pd

DB_PATH = "data/warehouse/financial_warehouse.db"


def run_pipeline():
    print("=== 开始运行数仓清洗与分析 Pipeline (DWD -> DWS -> ADS) ===")

    if not os.path.exists(DB_PATH):
        print(f"[错误] 数据库 {DB_PATH} 不存在，请先运行初始化和导入脚本。")
        return

    conn = duckdb.connect(DB_PATH)

    sql_steps = [
        ("DWD", "src/sql/dwd_cleaning.sql", "DWD 维度表与明细事实表"),
        ("DWS", "src/sql/dws_panel.sql", "DWS 企业-年度财务面板宽表"),
        ("ADS", "src/sql/ads_marts.sql", "ADS 行业聚合应用数据集市"),
        ("ADS", "src/sql/ads_quality.sql", "ADS 财务勾稽关系校验报告"),
    ]

    for layer, sql_path, desc in sql_steps:
        print(f"[{layer}] 正在执行: {sql_path} ...")
        with open(sql_path, "r", encoding="utf-8") as f:
            conn.execute(f.read())
        print(f"[成功] {desc}构建成功！")

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
    print("\n=== Pipeline 全部运行成功！ ===")


if __name__ == "__main__":
    run_pipeline()
