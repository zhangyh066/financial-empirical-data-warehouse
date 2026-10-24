"""
EFDW 只读 API 冒烟测试
======================
使用 FastAPI TestClient 直接调用应用，不访问网络、不依赖 8000 端口服务。
数据库文件不存在时整组跳过。
"""

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.config import DB_PATH

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="数据库文件不存在，跳过 API 冒烟测试")

client = TestClient(app)


def test_stats_overview():
    """样本概览：核心指标齐全且年份窗口自洽"""
    r = client.get("/api/stats")
    assert r.status_code == 200
    data = r.json()
    for key in ("n_firms", "year_min", "year_max", "n_obs", "n_industries", "db_updated_at"):
        assert key in data, f"缺少字段 {key}"
    assert data["n_firms"] > 0
    assert data["year_min"] <= data["year_max"]
    assert data["n_obs"] > 0


def test_variable_dict():
    """变量字典：包含 ROA 等核心变量及公式"""
    r = client.get("/api/dict")
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 20
    names = {d["variable"] for d in data}
    assert {"roa", "roe", "leverage", "firm_size"} <= names
    for d in data:
        assert d["label"] and d["category"] and d["formula"]


def test_descriptive_table():
    """描述性统计表：结构完整"""
    r = client.get("/api/descriptive", params={"winsorize_pct": 0.01})
    assert r.status_code == 200
    data = r.json()
    assert len(data) > 0
    row = next(d for d in data if d["variable"] == "roa")
    assert row["n"] > 0
    assert row["min"] <= row["median"] <= row["max"]


def test_tables_inventory():
    """四层表清单：包含 dws.firm_year_panel 且行数 > 0"""
    r = client.get("/api/tables")
    assert r.status_code == 200
    tables = {t["full_name"]: t["rows"] for t in r.json()}
    assert "dws.firm_year_panel" in tables
    assert tables["dws.firm_year_panel"] > 0


def test_yearly_obs():
    r = client.get("/api/yearly")
    assert r.status_code == 200
    data = r.json()
    assert len(data) > 0
    assert all(d["n_obs"] > 0 for d in data)


def test_default_year_window_covers_full_range():
    """缺省年份窗口 = 库内实际全部年份（不再写死 2018-2022）"""
    stats = client.get("/api/stats").json()
    # 导出接口不传年份参数：返回数据的年份范围应覆盖库内全部年份
    r = client.get("/api/export/csv")
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    years = {int(line.split(",")[2]) for line in lines[1:]}
    assert min(years) == stats["year_min"]
    assert max(years) == stats["year_max"]
    # 行业分布接口不传年份参数同样应覆盖全窗口
    r2 = client.get("/api/industry")
    assert r2.status_code == 200
    assert sum(d["n_firms"] for d in r2.json()) >= stats["n_firms"]


def test_export_meta_recipe_file():
    """样本配方文件：窗口/行数与真实导出结果一致"""
    stats = client.get("/api/stats").json()
    params = {"exclude_financial": "true", "exclude_newly_listed": "false",
              "winsorize_pct": 0.01, "variables": "roa,roe,leverage"}

    # 真实 CSV 导出行数
    csv_res = client.get("/api/export/csv", params=params)
    assert csv_res.status_code == 200
    csv_rows = len(csv_res.text.strip().splitlines()) - 1

    r = client.get("/api/export/meta", params=params)
    assert r.status_code == 200
    assert "financial_regression_panel_meta.txt" in r.headers.get("content-disposition", "")
    text = r.text
    # 实际窗口（非默认值占位）
    assert f"{stats['year_min']} - {stats['year_max']}" in text
    # 行数与真实导出一致
    assert f"{csv_rows:,}" in text
    # 关键口径字段齐全
    for kw in ("导出时间", "数据库更新时间", "剔除金融业", "缩尾比例", "roa", "roe", "leverage", "实际导出行数"):
        assert kw in text, f"meta 缺少内容: {kw}"


def test_refresh_data_status_shape():
    """数据更新状态接口：字段结构完整"""
    r = client.get("/api/refresh-data/status")
    assert r.status_code == 200
    data = r.json()
    for key in ("running", "step", "started_at", "finished_at", "success", "message"):
        assert key in data, f"缺少字段 {key}"
    assert data["running"] is False  # 测试环境不应有正在运行的刷新任务


def test_import_rejects_illegal_table_name():
    """表名注入防护：非法表名返回 400"""
    files = {"file": ("evil.csv", b"a,b\n1,2\n", "text/csv")}
    for bad_name in ("ods.x; DROP TABLE ods.company_info", "ods.x'--", "a.b.c", "1abc"):
        r = client.post("/api/import", files=files, data={"table_name": bad_name})
        assert r.status_code == 400, f"表名 {bad_name!r} 应被拒绝"


def test_import_accepts_legal_table_name():
    """合法表名可通过白名单校验并导入（导入临时 ods 表，随后清理）"""
    import duckdb
    files = {"file": ("pytest_demo.csv", b"stkcd,value\n000001,1.5\n", "text/csv")}
    r = client.post("/api/import", files=files, data={"table_name": "ods.pytest_smoke"})
    assert r.status_code == 200, r.text
    assert r.json()["import_details"]["rows"] == 1
    # 清理测试表
    conn = duckdb.connect(str(DB_PATH))
    try:
        conn.execute("DROP TABLE IF EXISTS ods.pytest_smoke")
    finally:
        conn.close()


def test_import_rejects_existing_table_without_overwrite():
    """防误覆盖：目标表已存在时默认 409，显式 overwrite 才允许覆盖（随后清理）"""
    import duckdb
    files = {"file": ("pytest_ow.csv", b"stkcd,value\n000001,1.5\n", "text/csv")}
    data = {"table_name": "ods.pytest_overwrite_smoke"}

    r1 = client.post("/api/import", files=files, data=data)
    assert r1.status_code == 200, r1.text

    r2 = client.post("/api/import", files=files, data=data)
    assert r2.status_code == 409
    assert "已存在" in r2.json()["detail"]

    r3 = client.post("/api/import", files=files, data={**data, "overwrite": "true"})
    assert r3.status_code == 200, r3.text

    conn = duckdb.connect(str(DB_PATH))
    try:
        conn.execute("DROP TABLE IF EXISTS ods.pytest_overwrite_smoke")
    finally:
        conn.close()


def test_cors_allows_localhost_origin():
    """CORS 收紧后：本机回环 Origin 放行"""
    r = client.get("/api/dict", headers={"Origin": "http://127.0.0.1:8000"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:8000"


def test_cors_rejects_foreign_origin():
    """CORS 收紧后：非法跨域 Origin 不发放行头"""
    r = client.get("/api/dict", headers={"Origin": "http://evil.example.com"})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


# ─────────────────────────────────────────────
# 财务分析深化功能：覆盖矩阵 / 市场概览 / 行业趋势 / 公司对比 / 体检报告
# ─────────────────────────────────────────────

def _first_company():
    rows = client.get("/api/companies").json()
    return rows[0]["stkcd"]


def test_coverage_matrix():
    """变量×年度覆盖矩阵：值域合法且包含核心变量"""
    r = client.get("/api/coverage")
    assert r.status_code == 200
    data = r.json()
    assert len(data["years"]) > 0
    cov_by_var = {v["variable"]: v for v in data["variables"]}
    assert "roa" in cov_by_var
    for year, pct in cov_by_var["roa"]["coverage"].items():
        assert 0 <= pct <= 1


def test_market_overview():
    r = client.get("/api/market-overview")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) > 0
    assert {"year", "n_firms", "avg_roa", "avg_leverage"} <= set(rows[0].keys())


def test_industry_trend():
    r = client.get("/api/industry-trend", params={"metrics": "avg_roa,avg_leverage"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) > 0
    assert {"industry", "year", "avg_roa", "avg_leverage"} <= set(rows[0].keys())
    # 非法指标 → 400
    r_bad = client.get("/api/industry-trend", params={"metrics": "evil_col"})
    assert r_bad.status_code == 400


def test_compare_companies():
    stkcd = _first_company()
    r = client.get("/api/compare", params={"stkcds": f"{stkcd}", "variable": "roa"})
    assert r.status_code == 200
    data = r.json()
    assert data["variable"] == "roa"
    assert len(data["series"]) == 1
    assert len(data["series"][0]["points"]) > 0
    # 非法变量 / 空列表 → 400
    assert client.get("/api/compare", params={"stkcds": stkcd, "variable": "evil"}).status_code == 400
    assert client.get("/api/compare", params={"stkcds": "", "variable": "roa"}).status_code == 400


def test_health_report():
    stkcd = _first_company()
    r = client.get(f"/api/health/{stkcd}")
    assert r.status_code == 200
    data = r.json()
    assert data["stkcd"] == stkcd
    assert data["company_name"]
    assert len(data["radar"]) == 5
    for dim in data["radar"]:
        assert dim["dimension"] and dim["variable"]
        if dim["percentile"] is not None:
            assert 0 <= dim["percentile"] <= 1
    assert len(data["panel"]) > 0
    assert isinstance(data["latest_year"], int)
    assert data["n_industry_firms"] > 0
    # 不存在的公司 → 404
    assert client.get("/api/health/999999").status_code == 404
