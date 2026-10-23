"""
EFDW - Enterprise Financial Data Warehouse
企业财务实证数据仓库 — FastAPI Backend API Server

Run: uvicorn src.api:app --port 8000
"""
import io
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import numpy as np
from fastapi import FastAPI, Query, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from src.config import (
    DB_PATH, FRONTEND_DIR, PROJECT_ROOT, SQL_DIR, TMP_DIR,
    get_logger, is_valid_table_name,
)
from src.etl.import_raw import DataImporter

logger = get_logger("efdw.api")

# ─────────────────────────────────────────────
# App Bootstrap
# ─────────────────────────────────────────────
app = FastAPI(
    title="EFDW Financial Data API",
    description="企业财务实证数据仓库 — 面向财务实证研究的 RESTful API（缩尾/样本筛选/行业聚合/杜邦分析/Altman Z 预警/相关性矩阵）。",
    version="2.1.0"
)

# 前端由后端同源托管，CORS 仅放行本机回环地址（本工具仅限本机使用，勿暴露到局域网/公网）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


# ─────────────────────────────────────────────
# 变量元数据中心：标签 / 所属能力分类 / 计算公式
# （同时驱动 描述性统计、分布、导出校验、变量字典 API，保证口径唯一）
# ─────────────────────────────────────────────
VARIABLE_META = {
    # 规模与市场
    "firm_size":        dict(label="企业规模 Size (ln Assets)",      category="规模与市场",   formula="LN(总资产)"),
    "leverage":         dict(label="资产负债率 Lev",                  category="规模与市场",   formula="总负债 / 总资产"),
    "tobin_q":          dict(label="托宾Q (Tobin's Q)",               category="规模与市场",   formula="(股权市值 + 总负债) / 总资产"),
    # 盈利能力
    "roa":              dict(label="总资产收益率 ROA",                category="盈利能力",     formula="净利润 / 总资产"),
    "roe":              dict(label="净资产收益率 ROE",                category="盈利能力",     formula="净利润 / 所有者权益"),
    "gross_margin":     dict(label="销售毛利率",                      category="盈利能力",     formula="(营业收入 - 营业成本) / 营业收入"),
    "net_margin":       dict(label="销售净利率",                      category="盈利能力",     formula="净利润 / 营业收入"),
    "operating_margin": dict(label="营业利润率",                      category="盈利能力",     formula="营业利润 / 营业收入"),
    # 成长性
    "revenue_growth":   dict(label="营业收入增长率",                  category="成长性",       formula="(营收ₜ - 营收ₜ₋₁) / 营收ₜ₋₁"),
    "asset_growth":     dict(label="总资产增长率",                    category="成长性",       formula="(总资产ₜ - 总资产ₜ₋₁) / 总资产ₜ₋₁"),
    "net_income_growth": dict(label="净利润增长率",                   category="成长性",       formula="(净利润ₜ - 净利润ₜ₋₁) / 净利润ₜ₋₁"),
    # 偿债能力
    "current_ratio":    dict(label="流动比率",                        category="偿债能力",     formula="流动资产 / 流动负债"),
    "quick_ratio":      dict(label="速动比率",                        category="偿债能力",     formula="(流动资产 - 存货) / 流动负债"),
    "cash_ratio":       dict(label="现金比率",                        category="偿债能力",     formula="货币资金 / 流动负债"),
    "interest_coverage": dict(label="利息保障倍数",                   category="偿债能力",     formula="(营业利润 + 利息费用) / 利息费用"),
    # 营运能力
    "total_asset_turnover": dict(label="总资产周转率",                category="营运能力",     formula="营业收入 / 总资产"),
    "inventory_turnover":   dict(label="存货周转率",                  category="营运能力",     formula="营业成本 / 存货"),
    "receivables_turnover": dict(label="应收账款周转率",              category="营运能力",     formula="营业收入 / 应收账款"),
    "cash_conversion_cycle": dict(label="现金转换周期 (天)",          category="营运能力",     formula="365/存货周转率 + 365/应收周转率 - 365/应付周转率"),
    # 现金流能力
    "ocf_to_assets":    dict(label="经营现金流/总资产",               category="现金流能力",   formula="经营活动现金流净额 / 总资产"),
    # 财务风险预警
    "altman_z":         dict(label="Altman Z 值（财务困境预警）",     category="财务风险预警", formula="1.2X₁ + 1.4X₂ + 3.3X₃ + 0.6X₄ + 1.0X₅（非金融企业适用）"),
    # 多期滞后 / 超前项
    "lag_roa":          dict(label="L.ROA (滞后一期)",                category="滞后与超前项", formula="LAG(roa, 1) OVER (PARTITION BY stkcd ORDER BY year)"),
    "lag2_roa":         dict(label="L2.ROA (滞后两期)",               category="滞后与超前项", formula="LAG(roa, 2) OVER (PARTITION BY stkcd ORDER BY year)"),
    "lag_roe":          dict(label="L.ROE (滞后一期)",                category="滞后与超前项", formula="LAG(roe, 1) OVER (PARTITION BY stkcd ORDER BY year)"),
    "lag_leverage":     dict(label="L.Lev (滞后一期)",                category="滞后与超前项", formula="LAG(leverage, 1) OVER (PARTITION BY stkcd ORDER BY year)"),
    "lag_revenue_growth": dict(label="L.营收增长 (滞后一期)",         category="滞后与超前项", formula="LAG(revenue_growth, 1) OVER (PARTITION BY stkcd ORDER BY year)"),
    "lead_roa":         dict(label="F.ROA (超前一期)",                category="滞后与超前项", formula="LEAD(roa, 1) OVER (PARTITION BY stkcd ORDER BY year)"),
}

# 变量名 → 中文标签的便捷映射（由 VARIABLE_META 派生，供统计/校验等接口使用）
NUMERIC_VARS = {k: v["label"] for k, v in VARIABLE_META.items()}

# 相关性矩阵默认变量（论文核心变量组合）
DEFAULT_CORR_VARS = "roa,roe,leverage,firm_size,revenue_growth,current_ratio,total_asset_turnover,tobin_q"

# 默认导出变量（回归常用组合）
DEFAULT_EXPORT_VARS = "firm_size,leverage,roa,roe,revenue_growth,current_ratio,total_asset_turnover,tobin_q,lag_roa"

# 行业对标分析的核心指标（direction: higher = 越高越优，lower = 越低越优）
BENCHMARK_VARS = [
    ("roa", "higher"), ("roe", "higher"), ("gross_margin", "higher"), ("net_margin", "higher"),
    ("revenue_growth", "higher"), ("leverage", "lower"), ("current_ratio", "higher"),
    ("interest_coverage", "higher"), ("total_asset_turnover", "higher"), ("ocf_to_assets", "higher"),
    ("tobin_q", "higher"), ("altman_z", "higher"),
]


def get_db_year_range() -> tuple[int, int]:
    """查询库内 DWS 面板实际年份范围，作为各接口缺省的研究窗口"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT MIN(year), MAX(year) FROM dws.firm_year_panel").fetchone()
        return int(row[0]), int(row[1])
    finally:
        conn.close()


def resolve_year_window(year_min: int | None, year_max: int | None) -> tuple[int, int]:
    """缺省年份补全：未指定时取库内实际 MIN/MAX(year)，与 export.py 行为一致"""
    if year_min is None or year_max is None:
        db_min, db_max = get_db_year_range()
        year_min = db_min if year_min is None else year_min
        year_max = db_max if year_max is None else year_max
    return year_min, year_max


def load_panel(year_min=None, year_max=None, exclude_financial=True,
               exclude_newly_listed=False, winsorize_pct=0.0, variables=None):
    """加载 DWS 面板并统一执行样本筛选（可选缩尾），返回 DataFrame。

    样本筛选条件（年份窗口 / 剔除金融业 / 剔除上市不足一年的新股）全部下推到
    SQL WHERE 子句执行，避免全表读入内存后再用 pandas 过滤。
    年份窗口缺省时取库内实际全部年份。
    """
    year_min, year_max = resolve_year_window(year_min, year_max)
    conditions = ["year BETWEEN ? AND ?"]
    params = [year_min, year_max]
    if exclude_financial:
        conditions.append("industry_category != ?")
        params.append("金融业")
    if exclude_newly_listed:
        conditions.append("year > list_year")

    sql = f"SELECT * FROM dws.firm_year_panel WHERE {' AND '.join(conditions)}"
    conn = get_conn()
    try:
        df = conn.execute(sql, params).fetchdf()
    finally:
        conn.close()
    if variables:
        sel = [v for v in variables if v in df.columns]
        df = df[sel]
    if winsorize_pct > 0:
        for v in df.columns:
            if v in NUMERIC_VARS:
                df[v] = winsorize(df[v], winsorize_pct)
    return df


def _friendly_db_error(e: Exception, action: str) -> HTTPException:
    """把 DuckDB 锁冲突等可预期异常映射为中文友好提示"""
    msg = str(e)
    if "Conflicting lock" in msg or "Could not set lock" in msg:
        return HTTPException(
            status_code=409,
            detail=f"数据库正被其他任务占用（可能正在更新数据或运行 Pipeline），请稍后重试。原始信息: {msg}"
        )
    if "No such file" in msg or "不存在" in msg:
        return HTTPException(status_code=500, detail=f"所需文件缺失，{action}失败: {msg}")
    return HTTPException(status_code=500, detail=f"{action}失败: {msg}（详情可查看 logs/efdw.log）")


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
            # 数据库文件最后修改时间（前端页头展示"库更新时间"）
            "db_updated_at": datetime.fromtimestamp(DB_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────
# API: 2. Descriptive Statistics Table
# ─────────────────────────────────────────────
@app.get("/api/descriptive")
def get_descriptive(
    year_min: int | None = Query(default=None),
    year_max: int | None = Query(default=None),
    exclude_financial: bool = Query(default=True),
    exclude_newly_listed: bool = Query(default=False),
    winsorize_pct: float = Query(default=0.01),
):
    """生成学术标准描述性统计表 (Table 1)"""
    df = load_panel(year_min, year_max, exclude_financial, exclude_newly_listed)

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
# API: 3. Variable Dictionary（变量数据字典）
# ─────────────────────────────────────────────
@app.get("/api/dict")
def get_variable_dict():
    """获取全部学术变量的中文标签、所属能力分类与计算公式"""
    return [
        {"variable": name, "label": meta["label"], "category": meta["category"], "formula": meta["formula"]}
        for name, meta in VARIABLE_META.items()
    ]


# ─────────────────────────────────────────────
# API: 4. Correlation Matrix（Pearson 相关性矩阵，论文 Table 3）
# ─────────────────────────────────────────────
@app.get("/api/correlation")
def get_correlation(
    year_min: int | None = Query(default=None),
    year_max: int | None = Query(default=None),
    exclude_financial: bool = Query(default=True),
    exclude_newly_listed: bool = Query(default=False),
    winsorize_pct: float = Query(default=0.01),
    variables: str = Query(default=DEFAULT_CORR_VARS),
):
    """Pearson 相关系数矩阵（缩尾后计算），支撑论文相关性分析表"""
    sel_vars = [v.strip() for v in variables.split(",") if v.strip() in NUMERIC_VARS]
    if len(sel_vars) < 2:
        raise HTTPException(status_code=400, detail="相关矩阵至少需要选择 2 个有效变量")

    df = load_panel(year_min, year_max, exclude_financial, exclude_newly_listed,
                    winsorize_pct=winsorize_pct, variables=sel_vars)
    df = df.dropna()
    if len(df) < 3:
        raise HTTPException(status_code=400, detail="有效样本量不足（<3），无法计算相关系数")

    corr = df[sel_vars].corr(method="pearson").round(3)
    matrix = corr.where(pd.notna(corr), None).values.tolist()
    return {
        "n": int(len(df)),
        "variables": [{"name": v, "label": NUMERIC_VARS[v]} for v in sel_vars],
        "matrix": matrix,
    }


# ─────────────────────────────────────────────
# API: 5. Industry Benchmarking（行业对标分析）
# ─────────────────────────────────────────────
@app.get("/api/benchmark/{stkcd}")
def get_benchmark(stkcd: str):
    """行业对标：公司核心指标 vs 同行业-年度中位数/均值，及行业内分位数"""
    conn = get_conn()
    try:
        df = conn.execute("SELECT * FROM dws.firm_year_panel").fetchdf()
    finally:
        conn.close()

    firm = df[df["stkcd"] == stkcd]
    if firm.empty:
        raise HTTPException(status_code=404, detail=f"未找到公司 {stkcd}")

    info = firm.iloc[0]
    indicators = []
    for var, direction in BENCHMARK_VARS:
        if var not in df.columns:
            continue
        # 同行业-年度的中位数 / 均值 / 分位数（窗口化计算，无需循环）
        grp = df.groupby(["industry_category", "year"])[var]
        df[f"__med_{var}"] = grp.transform("median")
        df[f"__mean_{var}"] = grp.transform("mean")
        df[f"__pct_{var}"] = grp.rank(pct=True)

        sub = df[df["stkcd"] == stkcd][["year", var, f"__med_{var}", f"__mean_{var}", f"__pct_{var}"]]
        values = [
            {
                "year": int(r["year"]),
                "company": None if pd.isna(r[var]) else round(float(r[var]), 4),
                "industry_median": None if pd.isna(r[f"__med_{var}"]) else round(float(r[f"__med_{var}"]), 4),
                "industry_mean": None if pd.isna(r[f"__mean_{var}"]) else round(float(r[f"__mean_{var}"]), 4),
                "percentile": None if pd.isna(r[f"__pct_{var}"]) else round(float(r[f"__pct_{var}"]), 4),
            }
            for _, r in sub.iterrows()
        ]
        indicators.append({
            "variable": var,
            "label": NUMERIC_VARS.get(var, var),
            "category": VARIABLE_META.get(var, {}).get("category", ""),
            "direction": direction,
            "values": values,
        })

    return {
        "stkcd": info["stkcd"],
        "company_name": info["company_name"],
        "industry_category": info["industry_category"],
        "industry_code": info["industry_code"],
        "indicators": indicators,
    }


# ─────────────────────────────────────────────
# API: 6. Industry Distribution
# ─────────────────────────────────────────────
@app.get("/api/industry")
def get_industry_distribution(
    year_min: int | None = Query(default=None),
    year_max: int | None = Query(default=None),
):
    """行业样本分布（公司个数）"""
    year_min, year_max = resolve_year_window(year_min, year_max)
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
# API: 7. Year-wise Observation Count
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
# API: 8. Variable Distribution (Histogram bins)
# ─────────────────────────────────────────────
@app.get("/api/distribution")
def get_distribution(
    variable: str = Query(default="roa"),
    year_min: int | None = Query(default=None),
    year_max: int | None = Query(default=None),
    exclude_financial: bool = Query(default=True),
    exclude_newly_listed: bool = Query(default=False),
    winsorize_pct: float = Query(default=0.01),
    bins: int = Query(default=30),
):
    """指定变量的频率直方图数据"""
    if variable not in NUMERIC_VARS:
        raise HTTPException(status_code=400, detail=f"不支持的变量: {variable}")

    df = load_panel(year_min, year_max, exclude_financial, exclude_newly_listed)
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
# API: 9. Company List
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
# API: 10. Company Detail（含杜邦分解、五大能力指标与 Altman Z 预警）
# ─────────────────────────────────────────────
@app.get("/api/company/{stkcd}")
def get_company_detail(stkcd: str):
    """公司年度面板明细 + 杜邦三因子分解 + Altman Z 财务困境预警"""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT year, assets, debt, equity, revenue, net_income, ocf,
                   firm_size, leverage, roa, roe, net_margin, operating_margin,
                   gross_margin, revenue_growth, asset_growth,
                   current_ratio, quick_ratio, interest_coverage,
                   total_asset_turnover, inventory_turnover, receivables_turnover,
                   cash_conversion_cycle, ocf_to_assets, tobin_q,
                   equity_multiplier, dupont_roe,
                   altman_z, altman_risk
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
# API: 11. Export Dataset
# ─────────────────────────────────────────────
def _build_export_df(year_min, year_max, exclude_financial, exclude_newly_listed,
                     winsorize_pct, variables):
    """导出数据集的统一构建逻辑（CSV / Stata / 配方文件共用，保证口径一致）"""
    year_min, year_max = resolve_year_window(year_min, year_max)
    df = load_panel(year_min, year_max, exclude_financial, exclude_newly_listed, winsorize_pct=0.0)
    sel_vars = [v.strip() for v in variables.split(",") if v.strip() in NUMERIC_VARS]
    if winsorize_pct > 0:
        for v in sel_vars:
            if v in df.columns:
                df[v] = winsorize(df[v], winsorize_pct)
    base_cols = ["stkcd", "company_name", "year", "industry_category"]
    return df[base_cols + sel_vars], sel_vars, year_min, year_max


@app.get("/api/export/csv")
def export_csv(
    year_min: int | None = Query(default=None),
    year_max: int | None = Query(default=None),
    exclude_financial: bool = Query(default=True),
    exclude_newly_listed: bool = Query(default=False),
    winsorize_pct: float = Query(default=0.01),
    variables: str = Query(default=DEFAULT_EXPORT_VARS),
):
    """一键导出过滤后的 CSV 格式回归数据集"""
    export_df, _, _, _ = _build_export_df(
        year_min, year_max, exclude_financial, exclude_newly_listed, winsorize_pct, variables
    )

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
    year_min: int | None = Query(default=None),
    year_max: int | None = Query(default=None),
    exclude_financial: bool = Query(default=True),
    exclude_newly_listed: bool = Query(default=False),
    winsorize_pct: float = Query(default=0.01),
    variables: str = Query(default=DEFAULT_EXPORT_VARS),
):
    """一键导出过滤后的 Stata .dta 格式回归数据集（Stata 14+ 支持中文）"""
    export_df, _, _, _ = _build_export_df(
        year_min, year_max, exclude_financial, exclude_newly_listed, winsorize_pct, variables
    )
    export_df = export_df.copy()
    export_df.columns = [c.lower() for c in export_df.columns]

    buf = io.BytesIO()
    export_df.to_stata(buf, write_index=False, version=118)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=financial_regression_panel.dta"}
    )


@app.get("/api/export/meta")
def export_meta(
    year_min: int | None = Query(default=None),
    year_max: int | None = Query(default=None),
    exclude_financial: bool = Query(default=True),
    exclude_newly_listed: bool = Query(default=False),
    winsorize_pct: float = Query(default=0.01),
    variables: str = Query(default=DEFAULT_EXPORT_VARS),
):
    """导出样本配方说明（_meta.txt）：与导出接口同参数，行数/窗口以后端真实执行结果为准"""
    export_df, sel_vars, year_min, year_max = _build_export_df(
        year_min, year_max, exclude_financial, exclude_newly_listed, winsorize_pct, variables
    )
    db_updated_at = datetime.fromtimestamp(DB_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    var_lines = "\n".join(f"  - {v}: {NUMERIC_VARS[v]}" for v in sel_vars)

    meta_text = f"""EFDW 回归数据集 — 样本配方说明（_meta.txt）
================================================
导出时间:        {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
数据库更新时间:  {db_updated_at}

样本筛选口径
------------------------------------------------
年份窗口:        {year_min} - {year_max}（后端实际执行窗口）
剔除金融业:      {"是" if exclude_financial else "否"}
剔除上市不足一年新股: {"是" if exclude_newly_listed else "否"}
缩尾比例:        {winsorize_pct:.2%}（双侧）{"" if winsorize_pct > 0 else "（未缩尾）"}

导出变量（{len(sel_vars)} 个）
------------------------------------------------
{var_lines}

导出规模
------------------------------------------------
实际导出行数:    {len(export_df):,}
基础列:          stkcd, company_name, year, industry_category

复现提示: 在 EFDW 看板「数据导出」页按以上口径配置并重新导出，
或调用 GET /api/export/csv 与 GET /api/export/stata 并携带相同参数。
"""

    return StreamingResponse(
        iter([meta_text.encode("utf-8-sig")]),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=financial_regression_panel_meta.txt"}
    )


# ─────────────────────────────────────────────
# API: 12. Get Database Tables
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
# API: 13. Financial Data Quality（财务勾稽关系校验报告）
# ─────────────────────────────────────────────

def _quality_check_count() -> int:
    """从 ads_quality.sql 的 UNION ALL 分支数推导检查项数量，避免硬编码与 SQL 脱钩"""
    try:
        sql = (SQL_DIR / "ads_quality.sql").read_text(encoding="utf-8")
        return sql.upper().count("UNION ALL") + 1
    except Exception:
        logger.warning("无法读取 ads_quality.sql 推导检查项数量，回退默认值 10", exc_info=True)
        return 10


QUALITY_CHECK_COUNT = _quality_check_count()


def _run_script_sync(script: str, action: str) -> None:
    """以子进程同步执行项目脚本（写入 DuckDB 的操作统一走独立进程，
    避免与 API 进程内的只读连接发生 "same database file with a different
    configuration" 冲突；失败时抛出带尾部错误信息的 RuntimeError）"""
    proc = subprocess.run(
        [sys.executable, script],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise RuntimeError(f"{action}失败（退出码 {proc.returncode}）: {' | '.join(tail)}")


@app.get("/api/quality")
def get_quality_report():
    """读取财务勾稽校验报告（ads.data_quality_report），并汇总通过率"""
    conn = get_conn()
    try:
        n_firm_years = conn.execute("SELECT COUNT(*) FROM dwd.fact_financial").fetchone()[0]
        rows = conn.execute("""
            SELECT stkcd, company_name, year, check_item, severity, detail
            FROM ads.data_quality_report
            ORDER BY stkcd, year, check_item
        """).fetchall()
    finally:
        conn.close()

    violations = [{"stkcd": r[0], "company_name": r[1], "year": r[2],
                   "check_item": r[3], "severity": r[4], "detail": r[5]} for r in rows]
    n_fail = sum(1 for v in violations if v["severity"] == "FAIL")
    n_warn = sum(1 for v in violations if v["severity"] == "WARN")
    total_checks = n_firm_years * QUALITY_CHECK_COUNT
    n_pass = total_checks - len(violations)
    return {
        "n_firm_years": n_firm_years,
        "checks_per_firm_year": QUALITY_CHECK_COUNT,
        "total_checks": total_checks,
        "passed": n_pass,
        "warned": n_warn,
        "failed": n_fail,
        "pass_rate": round(n_pass / total_checks, 4) if total_checks else None,
        "violations": violations,
    }


@app.post("/api/run-quality")
def run_quality_check():
    """手动触发财务勾稽关系校验（导入新数据后无需重跑全量 Pipeline）"""
    try:
        _run_script_sync("src/run_quality.py", "勾稽校验运行")
        report = get_quality_report()
        return {
            "success": True,
            "message": f"勾稽校验完成：共 {report['total_checks']} 项检查，通过 {report['passed']} 项，"
                       f"警告 {report['warned']} 项，失败 {report['failed']} 项。",
            "summary": {k: report[k] for k in ("total_checks", "passed", "warned", "failed", "pass_rate")},
        }
    except Exception as e:
        raise _friendly_db_error(e, "勾稽校验运行")


# ─────────────────────────────────────────────
# API: 14. File Upload and Data Import
# ─────────────────────────────────────────────
@app.post("/api/import")
def import_file_api(
    file: UploadFile = File(...),
    table_name: str = Form(default=""),
    varchar_columns: str = Form(default=""),
    overwrite: bool = Form(default=False),
):
    """从前端上传文件并导入 DuckDB 贴源层（默认拒绝覆盖已存在的表，需显式勾选 overwrite）"""
    temp_dir = TMP_DIR
    temp_dir.mkdir(parents=True, exist_ok=True)

    file_ext = Path(file.filename).suffix.lower()

    # 安全的临时文件命名（避免 tempfile.mktemp 的竞态风险），用后务必清理
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext, dir=temp_dir, prefix="upload_") as tmp:
        temp_file_path = Path(tmp.name)

    importer = DataImporter(str(DB_PATH))

    # 表名白名单校验（可带一层 schema 前缀），防止 SQL 注入
    if not table_name:
        table_name = importer._path_to_table_name(Path(file.filename), "ods")
    if not is_valid_table_name(table_name):
        temp_file_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"非法表名: {table_name}（仅允许字母/数字/下划线，且须以字母或下划线开头，可带一层 schema 前缀）"
        )

    try:
        # 防误覆盖：目标表已存在且未显式要求覆盖时直接拒绝，避免静默 DROP 核心表
        if not overwrite and importer.table_exists(table_name):
            raise HTTPException(
                status_code=409,
                detail=f"目标表 {table_name} 已存在。如确要覆盖，请勾选「覆盖已存在的同名表」后重试（旧表数据将被删除，不可恢复）。"
            )

        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        extra_varchar = [v.strip() for v in varchar_columns.split(",")] if varchar_columns else None

        import_res = importer.import_file(
            str(temp_file_path),
            table_name,
            varchar_columns=extra_varchar,
            drop_if_exists=overwrite,
        )

        quality_res = importer.check_quality(table_name)

        conn = get_conn()
        try:
            preview_df = conn.execute(f"SELECT * FROM {table_name} LIMIT 10").fetchdf()
            preview_df = preview_df.replace({np.nan: None})
            preview_data = preview_df.to_dict(orient="split")
        finally:
            conn.close()

        logger.info("文件导入成功: %s -> %s", file.filename, table_name)
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

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("文件导入失败: %s -> %s", file.filename, table_name)
        raise _friendly_db_error(e, "导入")

    finally:
        temp_file_path.unlink(missing_ok=True)


# ─────────────────────────────────────────────
# API: 15. Run Data Cleaning & Panel Pipeline
# ─────────────────────────────────────────────
@app.post("/api/run-pipeline")
def run_pipeline_api():
    """手动触发数仓 DWD -> DWS -> ADS 清洗与指标计算逻辑（子进程执行，失败自动回滚）"""
    try:
        _run_script_sync("src/run_pipeline.py", "Pipeline 运行")
        return {
            "success": True,
            "message": "数仓清洗与财务指标计算 Pipeline 运行成功！"
        }
    except Exception as e:
        raise _friendly_db_error(e, "Pipeline 运行")


# ─────────────────────────────────────────────
# API: 16. 一键更新数据（抓取 → 导入 → 清洗，后台线程执行）
# ─────────────────────────────────────────────
REFRESH_STEPS = [
    ("fetch_real_data", "抓取 akshare 全市场真实数据（增量复用磁盘缓存）", ["src/etl/fetch_real_data.py"]),
    ("import_raw", "导入 ODS 贴源层", ["src/etl/import_raw.py"]),
    ("run_pipeline", "运行 DWD -> DWS -> ADS 清洗管线", ["src/run_pipeline.py"]),
]

# 数据刷新任务状态（单任务互斥，内存态）
_refresh_lock = threading.Lock()
_refresh_state = {
    "running": False,
    "step": None,          # 当前执行步骤名
    "started_at": None,
    "finished_at": None,
    "success": None,       # None=未运行过/运行中, True/False=最近一次结果
    "message": None,
}


def _refresh_worker():
    """后台线程：依次以子进程执行 抓取 -> 导入 -> 清洗，避免阻塞 API 主线程"""
    try:
        for step_name, step_desc, script in REFRESH_STEPS:
            _refresh_state["step"] = step_name
            logger.info("[数据更新] 开始步骤 %s: %s", step_name, step_desc)
            proc = subprocess.run(
                [sys.executable, *script],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
                raise RuntimeError(f"步骤 {step_name} 失败（退出码 {proc.returncode}）: {' | '.join(tail)}")
            logger.info("[数据更新] 步骤 %s 完成", step_name)

        _refresh_state["success"] = True
        _refresh_state["message"] = "数据更新完成：抓取、导入与清洗管线全部成功。"
        logger.info("[数据更新] 全部步骤完成")
    except Exception as e:
        _refresh_state["success"] = False
        _refresh_state["message"] = str(e)
        logger.error("[数据更新] 失败: %s", e)
    finally:
        _refresh_state["running"] = False
        _refresh_state["step"] = None
        _refresh_state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@app.post("/api/refresh-data")
def refresh_data():
    """一键更新数据：后台线程依次执行 fetch_real_data -> import_raw -> run_pipeline"""
    with _refresh_lock:
        if _refresh_state["running"]:
            raise HTTPException(status_code=409, detail="数据更新任务正在运行中，请稍候。")
        _refresh_state.update({
            "running": True,
            "step": None,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None,
            "success": None,
            "message": None,
        })
        threading.Thread(target=_refresh_worker, daemon=True).start()
    logger.info("[数据更新] 任务已启动")
    return {"success": True, "message": "数据更新任务已在后台启动，可通过 /api/refresh-data/status 查询进度。"}


@app.get("/api/refresh-data/status")
def refresh_data_status():
    """查询数据更新任务的运行状态与最近一次结果"""
    step_labels = {name: desc for name, desc, _ in REFRESH_STEPS}
    return {
        **_refresh_state,
        "step_label": step_labels.get(_refresh_state["step"]),
    }


# ─────────────────────────────────────────────
# API: 17. 4 级企业名称智能消歧
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


# ─────────────────────────────────────────────
# 前端静态托管：同源访问 http://127.0.0.1:8000/ 即看板（/api/* 路由优先）
# ─────────────────────────────────────────────
from fastapi.staticfiles import StaticFiles

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
