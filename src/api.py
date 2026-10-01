"""
EFDW - Enterprise Financial Data Warehouse
企业财务实证数据仓库 — FastAPI Backend API Server

Run: uvicorn src.api:app --port 8000
"""
import io
import shutil
import tempfile
from pathlib import Path

import duckdb
import pandas as pd
import numpy as np
from fastapi import FastAPI, Query, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from src.etl.import_raw import DataImporter

# ─────────────────────────────────────────────
# App Bootstrap
# ─────────────────────────────────────────────
app = FastAPI(
    title="EFDW Financial Data API",
    description="企业财务实证数据仓库 — 面向财务实证研究的 RESTful API（缩尾/样本筛选/行业聚合/杜邦分析）。",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = Path("data/warehouse/financial_warehouse.db")


def get_conn():
    """获取只读 DuckDB 连接"""
    if not DB_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="数据库文件不存在，请先运行 Pipeline 初始化数仓。"
        )
    return duckdb.connect(str(DB_PATH), read_only=True)


def winsorize(series: pd.Series, pct: float) -> pd.Series:
    """双侧缩尾（学术惯例，默认 1%）"""
    if pct <= 0:
        return series
    series = series.astype(float)
    lo = series.quantile(pct)
    hi = series.quantile(1 - pct)
    return series.clip(lower=lo, upper=hi)


def apply_sample_filters(df, year_min, year_max, exclude_financial, exclude_newly_listed):
    """统一的动态样本筛选逻辑：年份窗口 / 剔除金融业 / 剔除上市不足一年的新股"""
    df = df[(df["year"] >= year_min) & (df["year"] <= year_max)].copy()
    if exclude_financial:
        df = df[df["industry_category"] != "金融业"]
    if exclude_newly_listed and "list_year" in df.columns:
        df = df[df["year"] > df["list_year"]]
    return df


# ─────────────────────────────────────────────
# 变量字典：DWS 面板中的数值型学术变量（五大能力分类）
# ─────────────────────────────────────────────
NUMERIC_VARS = {
    # 规模与市场
    "firm_size": "企业规模 Size (ln Assets)",
    "leverage": "资产负债率 Lev",
    "tobin_q": "托宾Q (Tobin's Q)",
    # 盈利能力
    "roa": "总资产收益率 ROA",
    "roe": "净资产收益率 ROE",
    "gross_margin": "销售毛利率",
    "net_margin": "销售净利率",
    "operating_margin": "营业利润率",
    # 成长性
    "revenue_growth": "营业收入增长率",
    "asset_growth": "总资产增长率",
    "net_income_growth": "净利润增长率",
    # 偿债能力
    "current_ratio": "流动比率",
    "quick_ratio": "速动比率",
    "cash_ratio": "现金比率",
    "interest_coverage": "利息保障倍数",
    # 营运能力
    "total_asset_turnover": "总资产周转率",
    "inventory_turnover": "存货周转率",
    "receivables_turnover": "应收账款周转率",
    "cash_conversion_cycle": "现金转换周期 (天)",
    # 现金流能力
    "ocf_to_assets": "经营现金流/总资产",
    # 多期滞后项 / 超前项
    "lag_roa": "L.ROA (滞后一期)",
    "lag2_roa": "L2.ROA (滞后两期)",
    "lag_roe": "L.ROE (滞后一期)",
    "lag_leverage": "L.Lev (滞后一期)",
    "lag_revenue_growth": "L.营收增长 (滞后一期)",
    "lead_roa": "F.ROA (超前一期)",
}

# 默认导出变量（回归常用组合）
DEFAULT_EXPORT_VARS = "firm_size,leverage,roa,roe,revenue_growth,current_ratio,total_asset_turnover,tobin_q,lag_roa"


# ─────────────────────────────────────────────
# API: 1. Overview Statistics
# ─────────────────────────────────────────────
@app.get("/api/stats")
def get_stats():
    """整体数仓核心指标概览"""
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT
                COUNT(DISTINCT stkcd)   AS n_firms,
                MIN(year)               AS year_min,
                MAX(year)               AS year_max,
                COUNT(*)                AS n_obs,
                COUNT(DISTINCT industry_category) AS n_industries
            FROM dws.firm_year_panel
        """).fetchone()
        return {
            "n_firms": row[0],
            "year_min": row[1],
            "year_max": row[2],
            "n_obs": row[3],
            "n_industries": row[4],
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────
# API: 2. Descriptive Statistics Table
# ─────────────────────────────────────────────
@app.get("/api/descriptive")
def get_descriptive(
    year_min: int = Query(default=2018),
    year_max: int = Query(default=2022),
    exclude_financial: bool = Query(default=True),
    exclude_newly_listed: bool = Query(default=False),
    winsorize_pct: float = Query(default=0.01),
):
    """生成学术标准描述性统计表 (Table 1)"""
    conn = get_conn()
    try:
        df = conn.execute("SELECT * FROM dws.firm_year_panel").fetchdf()
    finally:
        conn.close()

    df = apply_sample_filters(df, year_min, year_max, exclude_financial, exclude_newly_listed)

    results = []
    for col, label in NUMERIC_VARS.items():
        if col not in df.columns:
            continue
        s = df[col].dropna()
        if s.empty:
            continue
        if winsorize_pct > 0:
            s = winsorize(s, winsorize_pct)
        results.append({
            "variable": col,
            "label": label,
            "n": int(s.count()),
            "mean": round(float(s.mean()), 4),
            "sd": round(float(s.std()), 4),
            "min": round(float(s.min()), 4),
            "p25": round(float(s.quantile(0.25)), 4),
            "median": round(float(s.median()), 4),
            "p75": round(float(s.quantile(0.75)), 4),
            "max": round(float(s.max()), 4),
        })
    return results


# ─────────────────────────────────────────────
# API: 3. Industry Distribution
# ─────────────────────────────────────────────
@app.get("/api/industry")
def get_industry_distribution(
    year_min: int = Query(default=2018),
    year_max: int = Query(default=2022),
):
    """行业样本分布（公司个数）"""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT industry_category, COUNT(DISTINCT stkcd) AS n_firms
            FROM dws.firm_year_panel
            WHERE year BETWEEN ? AND ?
            GROUP BY industry_category
            ORDER BY n_firms DESC
        """, [year_min, year_max]).fetchall()
        return [{"industry": r[0], "n_firms": r[1]} for r in rows]
    finally:
        conn.close()


# ─────────────────────────────────────────────
# API: 4. Year-wise Observation Count
# ─────────────────────────────────────────────
@app.get("/api/yearly")
def get_yearly_obs():
    """逐年观测值数量"""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT year, COUNT(*) AS n_obs, COUNT(DISTINCT stkcd) AS n_firms
            FROM dws.firm_year_panel
            GROUP BY year ORDER BY year
        """).fetchall()
        return [{"year": r[0], "n_obs": r[1], "n_firms": r[2]} for r in rows]
    finally:
        conn.close()


# ─────────────────────────────────────────────
# API: 5. Variable Distribution (Histogram bins)
# ─────────────────────────────────────────────
@app.get("/api/distribution")
def get_distribution(
    variable: str = Query(default="roa"),
    year_min: int = Query(default=2018),
    year_max: int = Query(default=2022),
    exclude_financial: bool = Query(default=True),
    exclude_newly_listed: bool = Query(default=False),
    winsorize_pct: float = Query(default=0.01),
    bins: int = Query(default=30),
):
    """指定变量的频率直方图数据"""
    if variable not in NUMERIC_VARS:
        raise HTTPException(status_code=400, detail=f"不支持的变量: {variable}")

    conn = get_conn()
    try:
        df = conn.execute("SELECT * FROM dws.firm_year_panel").fetchdf()
    finally:
        conn.close()

    df = apply_sample_filters(df, year_min, year_max, exclude_financial, exclude_newly_listed)

    s = df[variable].dropna()
    if winsorize_pct > 0:
        s = winsorize(s, winsorize_pct)

    counts, edges = np.histogram(s, bins=bins)
    return {
        "variable": variable,
        "label": NUMERIC_VARS[variable],
        "bins": [{"x0": round(float(edges[i]), 4), "x1": round(float(edges[i+1]), 4), "count": int(counts[i])} for i in range(len(counts))],
        "mean": round(float(s.mean()), 4),
        "median": round(float(s.median()), 4),
        "sd": round(float(s.std()), 4),
    }


# ─────────────────────────────────────────────
# API: 6. Company List
# ─────────────────────────────────────────────
@app.get("/api/companies")
def get_companies():
    """所有公司列表"""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT DISTINCT stkcd, company_name, industry_code, industry_category
            FROM dws.firm_year_panel
            ORDER BY stkcd
        """).fetchall()
        return [{"stkcd": r[0], "company_name": r[1], "industry_code": r[2], "industry_category": r[3]} for r in rows]
    finally:
        conn.close()


# ─────────────────────────────────────────────
# API: 7. Company Detail（含杜邦分解与五大能力指标）
# ─────────────────────────────────────────────
@app.get("/api/company/{stkcd}")
def get_company_detail(stkcd: str):
    """公司年度面板明细 + 杜邦三因子分解"""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT year, assets, debt, equity, revenue, net_income, ocf,
                   firm_size, leverage, roa, roe, net_margin, operating_margin,
                   gross_margin, revenue_growth, asset_growth,
                   current_ratio, quick_ratio, interest_coverage,
                   total_asset_turnover, inventory_turnover, receivables_turnover,
                   cash_conversion_cycle, ocf_to_assets, tobin_q,
                   equity_multiplier, dupont_roe
            FROM dws.firm_year_panel
            WHERE stkcd = ?
            ORDER BY year
        """, [stkcd]).fetchdf()
        if rows.empty:
            raise HTTPException(status_code=404, detail=f"未找到公司 {stkcd}")
        info = conn.execute("""
            SELECT stkcd, company_name, industry_code, industry_category, list_date
            FROM dws.firm_year_panel WHERE stkcd = ? LIMIT 1
        """, [stkcd]).fetchone()
    finally:
        conn.close()

    panel = rows.replace({np.nan: None}).to_dict(orient="records")
    return {
        "stkcd": info[0],
        "company_name": info[1],
        "industry_code": info[2],
        "industry_category": info[3],
        "list_date": str(info[4]) if info[4] is not None else None,
        "panel": panel,
    }


# ─────────────────────────────────────────────
# API: 8. Export Dataset
# ─────────────────────────────────────────────
@app.get("/api/export/csv")
def export_csv(
    year_min: int = Query(default=2018),
    year_max: int = Query(default=2022),
    exclude_financial: bool = Query(default=True),
    exclude_newly_listed: bool = Query(default=False),
    winsorize_pct: float = Query(default=0.01),
    variables: str = Query(default=DEFAULT_EXPORT_VARS),
):
    """一键导出过滤后的 CSV 格式回归数据集"""
    conn = get_conn()
    try:
        df = conn.execute("SELECT * FROM dws.firm_year_panel").fetchdf()
    finally:
        conn.close()

    df = apply_sample_filters(df, year_min, year_max, exclude_financial, exclude_newly_listed)

    sel_vars = [v.strip() for v in variables.split(",") if v.strip() in NUMERIC_VARS]
    if winsorize_pct > 0:
        for v in sel_vars:
            if v in df.columns:
                df[v] = winsorize(df[v], winsorize_pct)

    base_cols = ["stkcd", "company_name", "year", "industry_category"]
    export_df = df[base_cols + sel_vars]

    buf = io.StringIO()
    export_df.to_csv(buf, index=False, encoding="utf-8-sig")
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue().encode("utf-8-sig")]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=financial_regression_panel.csv"}
    )


@app.get("/api/export/stata")
def export_stata(
    year_min: int = Query(default=2018),
    year_max: int = Query(default=2022),
    exclude_financial: bool = Query(default=True),
    exclude_newly_listed: bool = Query(default=False),
    winsorize_pct: float = Query(default=0.01),
    variables: str = Query(default=DEFAULT_EXPORT_VARS),
):
    """一键导出过滤后的 Stata .dta 格式回归数据集（Stata 14+ 支持中文）"""
    conn = get_conn()
    try:
        df = conn.execute("SELECT * FROM dws.firm_year_panel").fetchdf()
    finally:
        conn.close()

    df = apply_sample_filters(df, year_min, year_max, exclude_financial, exclude_newly_listed)

    sel_vars = [v.strip() for v in variables.split(",") if v.strip() in NUMERIC_VARS]
    if winsorize_pct > 0:
        for v in sel_vars:
            if v in df.columns:
                df[v] = winsorize(df[v], winsorize_pct)

    base_cols = ["stkcd", "company_name", "year", "industry_category"]
    export_df = df[base_cols + sel_vars].copy()
    export_df.columns = [c.lower() for c in export_df.columns]

    buf = io.BytesIO()
    export_df.to_stata(buf, write_index=False, version=118)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=financial_regression_panel.dta"}
    )


# ─────────────────────────────────────────────
# API: 9. Get Database Tables
# ─────────────────────────────────────────────
@app.get("/api/tables")
def get_tables():
    """获取数仓中所有 ODS / DWD / DWS / ADS 表的列表"""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema IN ('ods', 'dwd', 'dws', 'ads')
            ORDER BY table_schema, table_name
        """).fetchall()

        results = []
        for schema, table in rows:
            full_name = f"{schema}.{table}"
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {full_name}").fetchone()[0]
            except Exception:
                count = 0
            results.append({
                "schema": schema,
                "table": table,
                "full_name": full_name,
                "rows": count
            })
        return results
    finally:
        conn.close()


# ─────────────────────────────────────────────
# API: 10. File Upload and Data Import
# ─────────────────────────────────────────────
@app.post("/api/import")
def import_file_api(
    file: UploadFile = File(...),
    table_name: str = Form(default=""),
    varchar_columns: str = Form(default=""),
):
    """从前端上传文件并导入 DuckDB 贴源层"""
    temp_dir = Path("data/tmp")
    temp_dir.mkdir(parents=True, exist_ok=True)

    file_ext = Path(file.filename).suffix.lower()
    temp_file_name = f"upload_{tempfile.mktemp(dir='')}{file_ext}"
    temp_file_path = temp_dir / temp_file_name

    try:
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        importer = DataImporter(str(DB_PATH))

        if not table_name:
            table_name = importer._path_to_table_name(Path(file.filename), "ods")

        extra_varchar = [v.strip() for v in varchar_columns.split(",")] if varchar_columns else None

        import_res = importer.import_file(
            str(temp_file_path),
            table_name,
            varchar_columns=extra_varchar
        )

        quality_res = importer.check_quality(table_name)

        conn = get_conn()
        try:
            preview_df = conn.execute(f"SELECT * FROM {table_name} LIMIT 10").fetchdf()
            preview_df = preview_df.replace({np.nan: None})
            preview_data = preview_df.to_dict(orient="split")
        finally:
            conn.close()

        return {
            "success": True,
            "message": f"成功将 {file.filename} 导入至 {table_name}",
            "import_details": import_res,
            "quality": {
                "total_rows": quality_res["total_rows"],
                "duplicate_rows": quality_res["duplicate_rows"],
                "columns": [
                    {
                        "name": col,
                        "dtype": info["dtype"],
                        "null_count": info["null_count"],
                        "null_pct": info["null_pct"]
                    }
                    for col, info in quality_res["columns"].items()
                ]
            },
            "preview": {
                "columns": preview_data.get("columns", []),
                "data": preview_data.get("data", [])
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导入失败: {str(e)}")

    finally:
        if temp_file_path.exists():
            try:
                temp_file_path.unlink()
            except Exception:
                pass


# ─────────────────────────────────────────────
# API: 11. Run Data Cleaning & Panel Pipeline
# ─────────────────────────────────────────────
@app.post("/api/run-pipeline")
def run_pipeline_api():
    """手动触发数仓 DWD -> DWS -> ADS 清洗与指标计算逻辑"""
    try:
        from src.run_pipeline import run_pipeline
        run_pipeline()
        return {
            "success": True,
            "message": "数仓清洗与财务指标计算 Pipeline 运行成功！"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline 运行失败: {str(e)}"
        )


# ─────────────────────────────────────────────
# API: 12. 4 级企业名称智能消歧
# ─────────────────────────────────────────────
class ResolveRequest(BaseModel):
    names: list[str]
    threshold: float = 0.6


@app.post("/api/resolve")
def resolve_company_names(request: ResolveRequest):
    """多级智能公司实体消歧匹配算法：
    1 当前名称精确匹配 → 2 历史名称精确匹配 → 3 子串包含匹配 → 4 Jaro-Winkler 模糊匹配
    """
    conn = get_conn()
    try:
        companies = conn.execute("SELECT stkcd, company_name, industry_category FROM dwd.dim_company").fetchdf()
        history = conn.execute("SELECT stkcd, company_name_history FROM dwd.dim_company_names").fetchdf()
    finally:
        conn.close()

    results = []
    threshold = request.threshold

    for raw_name in request.names:
        name = raw_name.strip()
        if not name:
            continue

        matched_stkcd = None
        matched_name = None
        matched_industry = None
        match_type = "No Match"
        score = 0.0

        def set_match(stkcd, comp_name, ind, m_type, val):
            nonlocal matched_stkcd, matched_name, matched_industry, match_type, score
            matched_stkcd = stkcd
            matched_name = comp_name
            matched_industry = ind
            match_type = m_type
            score = val

        # 1. First Level: Exact Match with Current Name
        exact_match = companies[companies["company_name"] == name]
        if not exact_match.empty:
            row = exact_match.iloc[0]
            set_match(row["stkcd"], row["company_name"], row["industry_category"], "Current Exact Match", 1.0)
        else:
            # 2. Second Level: Exact Match with Historical Name
            hist_match = history[history["company_name_history"] == name]
            if not hist_match.empty:
                stkcd = hist_match.iloc[0]["stkcd"]
                comp_info = companies[companies["stkcd"] == stkcd]
                if not comp_info.empty:
                    row = comp_info.iloc[0]
                    set_match(row["stkcd"], row["company_name"], row["industry_category"], "Historical Exact Match", 0.95)

            # 3. Third Level: Substring Match
            if match_type == "No Match":
                sub_match = companies[companies.apply(lambda r: name in r["company_name"] or r["company_name"] in name, axis=1)]
                if not sub_match.empty:
                    row = sub_match.iloc[0]
                    set_match(row["stkcd"], row["company_name"], row["industry_category"], "Substring Match", 0.90)
                else:
                    sub_hist = history[history.apply(lambda r: name in r["company_name_history"] or r["company_name_history"] in name, axis=1)]
                    if not sub_hist.empty:
                        stkcd = sub_hist.iloc[0]["stkcd"]
                        comp_info = companies[companies["stkcd"] == stkcd]
                        if not comp_info.empty:
                            row = comp_info.iloc[0]
                            set_match(row["stkcd"], row["company_name"], row["industry_category"], "Substring Match", 0.85)

            # 4. Fourth Level: Fuzzy Match (Jaro-Winkler in DuckDB)
            if match_type == "No Match":
                conn = get_conn()
                try:
                    query = """
                        SELECT stkcd, company_name, industry_category,
                               jaro_winkler_similarity(company_name, ?) as sim
                        FROM dwd.dim_company
                        WHERE jaro_winkler_similarity(company_name, ?) >= ?
                        ORDER BY sim DESC
                        LIMIT 1
                    """
                    row = conn.execute(query, [name, name, threshold]).fetchone()
                    if row:
                        set_match(row[0], row[1], row[2], "Fuzzy Match", round(row[3], 4))
                    else:
                        query_h = """
                            SELECT h.stkcd, c.company_name, c.industry_category,
                                   jaro_winkler_similarity(h.company_name_history, ?) as sim
                            FROM dwd.dim_company_names h
                            JOIN dwd.dim_company c ON h.stkcd = c.stkcd
                            WHERE jaro_winkler_similarity(h.company_name_history, ?) >= ?
                            ORDER BY sim DESC
                            LIMIT 1
                        """
                        row_h = conn.execute(query_h, [name, name, threshold]).fetchone()
                        if row_h:
                            set_match(row_h[0], row_h[1], row_h[2], "Fuzzy Match", round(row_h[3], 4))
                finally:
                    conn.close()

        results.append({
            "raw_name": raw_name,
            "stkcd": matched_stkcd or "—",
            "company_name": matched_name or "—",
            "industry_category": matched_industry or "—",
            "match_type": match_type,
            "score": score
        })

    return results
