"""快速查看数据库内容"""
import duckdb

DB_PATH = "data/warehouse/financial_warehouse.db"

conn = duckdb.connect(DB_PATH, read_only=True)

# 1. 查看所有表
print("=" * 60)
print("  数据库中所有的表和视图 (ODS / DWD / DWS / ADS)")
print("=" * 60)
tables = conn.execute("""
    SELECT table_schema, table_name
    FROM information_schema.tables
    WHERE table_schema IN ('ods', 'dwd', 'dws', 'ads')
    ORDER BY table_schema, table_name
""").fetchdf()
print(tables.to_string(index=False))

# 2. 查看 DWS 面板数据前 8 行
print()
print("=" * 60)
print("  DWS 企业-年度财务面板前 8 行")
print("=" * 60)
panel = conn.execute("""
    SELECT stkcd, company_name, year,
           round(firm_size, 2)        AS Size,
           round(leverage, 3)         AS Lev,
           round(roa, 4)              AS ROA,
           round(roe, 4)              AS ROE,
           round(revenue_growth, 4)   AS RevG,
           round(current_ratio, 3)    AS Current,
           round(total_asset_turnover, 3) AS Tat,
           round(lag_roa, 4)          AS L_ROA
    FROM dws.firm_year_panel
    ORDER BY stkcd, year
    LIMIT 8
""").fetchdf()
print(panel.to_string(index=False))

# 3. 各层表的行数
print()
print("=" * 60)
print("  各层数据行数统计")
print("=" * 60)
for schema, table in [("ods", "company_info"), ("ods", "financial_raw"), ("ods", "company_names_raw"),
                      ("dwd", "dim_company"), ("dwd", "fact_financial"), ("dwd", "dim_company_names"),
                      ("dws", "firm_year_panel"),
                      ("ads", "industry_year_stats"), ("ads", "market_year_overview")]:
    try:
        cnt = conn.execute(f"SELECT COUNT(*) FROM {schema}.{table}").fetchone()[0]
        print(f"  {schema}.{table:25s}  →  {cnt} 行")
    except Exception:
        pass

conn.close()
