"""
EFDW DWD/DWS 指标计算逻辑测试
==============================
在临时 DuckDB 库中构造满足勾稽关系的小型样本（2 家公司 × 3 年），
执行真实的 dwd_cleaning.sql / dws_panel.sql，验证指标口径与勾稽逻辑。
不访问网络、不读写生产数据库。
"""

from pathlib import Path

import duckdb
import pandas as pd
import pytest

SQL_DIR = Path(__file__).resolve().parent.parent / "src" / "sql"

# 两家公司：制造业（000001）与金融业（600000），2020-2022 三年；
# 另加一家总资产为 0 的"问题公司"（300999），守护 dws_panel.sql 的 LN/除零防护
# 数字按 资产 = 负债 + 权益 构造，便于勾稽验证
COMPANY_ROWS = [
    ("000001", "测试制造", "2020-01-01", "C30"),
    ("600000", "测试银行", "2019-06-01", "J66"),
    ("300999", "问题公司", "2021-01-01", "C39"),
]

FIN_ROWS = [
    # stkcd, accper, assets, debt, equity, retained, ca, cl, inv, ar, cash, ap,
    # revenue, cost, op_profit, interest, net_income, ocf, equity_mv
    ("000001", "2020-12-31", 1000, 400, 600, 200, 500, 250, 100, 80, 50, 60, 800, 500, 120, 10, 100, 150, 900),
    ("000001", "2021-12-31", 1100, 440, 660, 260, 550, 275, 110, 88, 55, 66, 880, 550, 132, 11, 110, 160, 990),
    ("000001", "2022-12-31", 1210, 484, 726, 330, 605, 300, 121, 96, 60, 72, 968, 605, 145, 12, 121, 170, 1089),
    ("600000", "2020-12-31", 5000, 4500, 500, 100, 800, 700, 0, 200, 300, 100, 600, 300, 80, 20, 60, 90, 2000),
    ("600000", "2021-12-31", 5200, 4680, 520, 130, 820, 720, 0, 210, 310, 105, 620, 310, 84, 21, 63, 95, 2100),
    ("600000", "2022-12-31", 5400, 4860, 540, 160, 840, 740, 0, 220, 320, 110, 640, 320, 88, 22, 66, 100, 2200),
    # 坏数据行：总资产为 0 —— LN(assets) 与 debt/assets 等不应炸掉整个重建，应输出 NULL
    ("300999", "2023-12-31", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
]


@pytest.fixture()
def conn(tmp_path):
    """构建临时 DuckDB 库并执行 DWD + DWS 两层 SQL"""
    db = duckdb.connect(str(tmp_path / "test_warehouse.db"))
    for schema in ("ods", "dwd", "dws", "ads"):
        db.execute(f"CREATE SCHEMA {schema}")

    info_df = pd.DataFrame(COMPANY_ROWS, columns=["Stkcd", "Comnam", "Listdt", "Indcd"])
    names_df = pd.DataFrame(
        [("000001", "测试制造", "2020-01-01", "2099-12-31")],
        columns=["Stkcd", "Comnam", "BeginDate", "EndDate"],
    )
    fin_df = pd.DataFrame(FIN_ROWS, columns=[
        "Stkcd", "Accper", "F010101A", "F010201A", "F010301A", "F010401A",
        "F010701A", "F010801A", "F011001A", "F011101A", "F011201A", "F011301A",
        "F020101A", "F020201A", "F020301A", "F020401A", "F030101A", "F040101A", "F100101C",
    ])
    db.execute("CREATE TABLE ods.company_info AS SELECT * FROM info_df")
    db.execute("CREATE TABLE ods.company_names_raw AS SELECT * FROM names_df")
    db.execute("CREATE TABLE ods.financial_raw AS SELECT * FROM fin_df")

    for sql_file in ("dwd_cleaning.sql", "dws_panel.sql"):
        db.execute((SQL_DIR / sql_file).read_text(encoding="utf-8"))

    yield db
    db.close()


def test_dwd_year_filter_and_translation(conn):
    """DWD：年报过滤 + 字段翻译 + 年份提取"""
    rows = conn.execute(
        "SELECT COUNT(*), MIN(year), MAX(year) FROM dwd.fact_financial"
    ).fetchone()
    assert rows == (7, 2020, 2023)
    # 行业门类映射
    ind = conn.execute(
        "SELECT stkcd, industry_category FROM dwd.dim_company ORDER BY stkcd"
    ).fetchall()
    assert ind == [("000001", "制造业"), ("300999", "制造业"), ("600000", "金融业")]


def test_dws_row_count(conn):
    n = conn.execute("SELECT COUNT(*) FROM dws.firm_year_panel").fetchone()[0]
    assert n == 7


def test_dws_ratio_formulas(conn):
    """指标口径：ROA / 杠杆 / 流动比率按学术公式计算"""
    r = conn.execute("""
        SELECT roa, leverage, current_ratio, tobin_q
        FROM dws.firm_year_panel
        WHERE stkcd = '000001' AND year = 2020
    """).fetchone()
    roa, lev, cur, tq = r
    assert roa == pytest.approx(100 / 1000, abs=1e-9)
    assert lev == pytest.approx(400 / 1000, abs=1e-9)
    assert cur == pytest.approx(500 / 250, abs=1e-9)
    assert tq == pytest.approx((900 + 400) / 1000, abs=1e-9)


def test_dws_dupont_identity(conn):
    """杜邦恒等式：dupont_roe ≡ roe（勾稽关系验证）"""
    rows = conn.execute("""
        SELECT roe, dupont_roe FROM dws.firm_year_panel
        WHERE roe IS NOT NULL
    """).fetchall()
    assert len(rows) == 6
    for roe, dupont_roe in rows:
        assert dupont_roe == pytest.approx(roe, rel=1e-9)


def test_dws_growth_and_lag(conn):
    """成长性与滞后项：营收增长率 + L.ROA 窗口函数"""
    r = conn.execute("""
        SELECT revenue_growth, lag_roa, lag2_roa
        FROM dws.firm_year_panel
        WHERE stkcd = '000001' AND year = 2022
    """).fetchone()
    growth, lag_roa, lag2_roa = r
    assert growth == pytest.approx((968 - 880) / 880, abs=1e-9)
    assert lag_roa == pytest.approx(110 / 1100, abs=1e-9)   # 2021 年 ROA
    assert lag2_roa == pytest.approx(100 / 1000, abs=1e-9)  # 2020 年 ROA
    # 首年滞后项为 NULL
    first = conn.execute("""
        SELECT lag_roa, revenue_growth FROM dws.firm_year_panel
        WHERE stkcd = '000001' AND year = 2020
    """).fetchone()
    assert first == (None, None)


def test_dws_altman_z_rules(conn):
    """Altman Z：非金融企业计算 Z 值，金融企业置 NULL"""
    fin = conn.execute("""
        SELECT altman_z, altman_risk FROM dws.firm_year_panel
        WHERE stkcd = '600000' AND year = 2020
    """).fetchone()
    assert fin == (None, None)

    z = conn.execute("""
        SELECT altman_z FROM dws.firm_year_panel
        WHERE stkcd = '000001' AND year = 2020
    """).fetchone()[0]
    expected = (1.2 * (500 - 250) / 1000 + 1.4 * 200 / 1000
                + 3.3 * (120 + 10) / 1000 + 0.6 * 900 / 400 + 1.0 * 800 / 1000)
    assert z == pytest.approx(expected, abs=1e-9)


def test_dws_zero_asset_row_does_not_break_rebuild(conn):
    """坏数据防护：assets=0 的行不会让 LN/除零炸掉整个重建（fixture 已验证构建成功），
    firm_size / leverage / tobin_q / roa 应输出 NULL 而非异常值"""
    row = conn.execute("""
        SELECT firm_size, leverage, tobin_q, roa
        FROM dws.firm_year_panel
        WHERE stkcd = '300999' AND year = 2023
    """).fetchone()
    assert row == (None, None, None, None)
