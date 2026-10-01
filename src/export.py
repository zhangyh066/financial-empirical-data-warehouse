import os
import duckdb
import pandas as pd

DB_PATH = "data/warehouse/financial_warehouse.db"


def export_to_stata():
    export_dta_path = "data/warehouse/regression_panel_data.dta"
    export_csv_path = "data/warehouse/regression_panel_data.csv"

    print("=== 开始运行一键数据导出模块 (DWS -> ADS Stata/CSV) ===")

    if not os.path.exists(DB_PATH):
        print(f"[错误] 数据库 {DB_PATH} 不存在，请先运行 Pipeline 脚本。")
        return

    conn = duckdb.connect(DB_PATH)

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
        WHERE year BETWEEN 2019 AND 2022   -- 假设研究窗口
          AND industry_category != '金融业'
        ORDER BY stkcd, year;
    """

    df = conn.execute(sql_query).fetchdf()
    conn.close()

    # 2. Stata 格式学术细节优化：变量名小写（Stata 14+ 支持 UTF-8 中文内容）
    df.columns = [col.lower() for col in df.columns]

    print(f"[导出] 正在将 {len(df)} 行数据转换为 Stata dta 格式...")
    try:
        df.to_stata(
            export_dta_path,
            write_index=False,
            version=118,  # Stata 14+，支持 unicode/中文
            convert_dates=None
        )
        print(f"[成功] Stata 数据集已导出至: {export_dta_path}")
    except Exception as e:
        print(f"[警告] 导出 Stata 失败 (可能未安装 pyarrow 或依赖库): {e}")

    # 同时备份一份万能的 CSV 格式
    df.to_csv(export_csv_path, index=False, encoding="utf-8-sig")
    print(f"[成功] CSV 数据集已同步备份至: {export_csv_path}")

    print("\n=== 回归数据集导出全部完成！现在你可以直接在 Stata 中运行 `use regression_panel_data.dta, clear` 了 ===")


if __name__ == "__main__":
    export_to_stata()
