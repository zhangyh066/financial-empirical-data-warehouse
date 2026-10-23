"""
EFDW - akshare 真实数据抓取器
==============================
用 akshare 公开接口拉取 A 股全市场真实财务数据，替换 mock_data.py 的模拟数据。
输出与模拟数据完全同构的三张 CSV（CSMAR 风格列名），因此下游
import_raw.py / dwd_cleaning.sql / dws_panel.sql 无需任何改动。

数据源（全部为公开免费接口）：
  ① 公司清单与上市日期：沪深京交易所官网（akshare stock_info_*_name_code）
  ② 财务三表（简表）：东方财富数据中心，按报告期全市场批量接口
     - stock_zcfz_em  资产负债表：总资产/总负债/股东权益/货币资金/应收/存货/应付
     - stock_lrb_em   利润表：营业总收入/营业支出/营业利润/净利润
     - stock_xjll_em  现金流量表：经营性现金流净额
  ③ 年末总市值：东方财富估值分析接口 RPT_VALUEANALYSIS_DET（按交易日批量）
  ④ 行业代码：深交所/北交所官方证监会行业代码；上交所股票用东财行业名映射到门类字母
  ⑤ 明细补充（可选，逐股抓取，断点续抓）：新浪全量资产负债表/利润表
     - 流动资产合计 / 流动负债合计（→ 流动比率、速动比率、现金比率）
     - 盈余公积 + 未分配利润 = 留存收益（→ Altman Z 的 X2）
     - 利息费用（→ 利息保障倍数、Altman Z 的 X3）

已知口径限制（真实数据固有的）：
  - 东财简表不含流动资产/流动负债/留存收益/利息费用，这四项来自新浪逐股接口，
    抓取耗时较长（约 1 小时，4 线程），可用 --skip-enrich 跳过（相关指标将为 NULL）
  - 东财批量接口不含北交所财务数据，北交所公司只保留在公司维度表中
  - 已退市公司不出现在交易所清单中，其历史年份若出现在三表里仍保留
  - 上交所公司的行业门类由东财行业名关键词映射，是近似口径；
    深交所/北交所公司使用交易所官方证监会行业代码

用法：
  python src/etl/fetch_real_data.py                  # 全量抓取（含新浪明细补充）
  python src/etl/fetch_real_data.py --skip-enrich    # 只要批量数据（约 10 分钟）
  python src/etl/fetch_real_data.py --enrich-only    # 只跑新浪明细补充（可反复续抓）
  python src/etl/fetch_real_data.py --start-year 2020 --end-year 2025
所有中间结果缓存在 data/raw/akshare_cache/，中断后重跑自动续传。
"""

import argparse
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

# 兼容 `python src/etl/fetch_real_data.py` 直接运行：把项目根目录加入模块搜索路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.config import AKSHARE_CACHE_DIR, RAW_DIR

CACHE_DIR = str(AKSHARE_CACHE_DIR)
RAW_DIR = str(RAW_DIR)

FIN_COLS = [
    "Stkcd", "Accper",
    "F010101A", "F010201A", "F010301A", "F010401A",  # 资产/负债/权益/留存收益
    "F010701A", "F010801A",                          # 流动资产/流动负债
    "F011001A", "F011101A", "F011201A", "F011301A",  # 存货/应收/货币资金/应付
    "F020101A", "F020201A", "F020301A", "F020401A",  # 营收/营业成本/营业利润/利息费用
    "F030101A", "F040101A",                          # 净利润/经营现金流
    "F100101C",                                      # 股权市值
]

# ─────────────────────────────────────────────
# 网络工具：带重试
# ─────────────────────────────────────────────

def retry_call(fn, *args, tries=5, base_sleep=2.0, desc="", **kwargs):
    """调用 fn，失败指数退避重试；全部失败抛最后一次异常"""
    last = None
    for i in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last = e
            sleep = base_sleep * (2 ** i) + random.uniform(0, 1)
            print(f"    [重试 {i + 1}/{tries}] {desc}: {type(e).__name__}: {str(e)[:120]}")
            time.sleep(sleep)
    raise last


def to_num(series):
    """东财/新浪的数值列容错转 float（'-'、'--'、带逗号字符串 → NaN）"""
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False)
        .replace({"-": None, "--": None, "None": None, "nan": None}),
        errors="coerce",
    )


# ─────────────────────────────────────────────
# 阶段 1：公司清单（沪深京交易所官网，含上市日期、官方行业代码）
# ─────────────────────────────────────────────

def _norm_name(name: str) -> str:
    """公司名规范化：全角转半角、去空格（深交所原始名形如 '万  科Ａ'）"""
    import unicodedata
    return unicodedata.normalize("NFKC", str(name)).replace(" ", "").strip()


def _extract_csrc_letter(ind: str) -> str | None:
    """交易所行业字段形如 'J 金融业' 或 'C39'，统一提取门类字母"""
    import re
    m = re.match(r"^\s*([A-Za-z])", str(ind))
    return m.group(1).upper() if m else None


def fetch_company_base() -> pd.DataFrame:
    cache = os.path.join(CACHE_DIR, "company_base.csv")
    if os.path.exists(cache):
        print(f"[缓存命中] {cache}")
        return pd.read_csv(cache, dtype=str)

    import akshare as ak

    frames = []
    sh = retry_call(ak.stock_info_sh_name_code, desc="上交所公司清单")
    frames.append(pd.DataFrame({
        "Stkcd": sh["证券代码"].astype(str).str.zfill(6),
        "Comnam": sh["证券简称"].map(_norm_name),
        "Listdt": sh["上市日期"].astype(str),
        "Indcd": None,
        "exchange": "SH",
    }))

    sz = retry_call(ak.stock_info_sz_name_code, desc="深交所公司清单")  # 该接口仅含 A 股
    frames.append(pd.DataFrame({
        "Stkcd": sz["A股代码"].astype(str).str.zfill(6),
        "Comnam": sz["A股简称"].map(_norm_name),
        "Listdt": sz["A股上市日期"].astype(str),
        "Indcd": sz["所属行业"].map(_extract_csrc_letter),  # 官方证监会门类，如 'J 金融业' → J
        "exchange": "SZ",
    }))

    bj = retry_call(ak.stock_info_bj_name_code, desc="北交所公司清单")
    frames.append(pd.DataFrame({
        "Stkcd": bj["证券代码"].astype(str).str.zfill(6),
        "Comnam": bj["证券简称"].map(_norm_name),
        "Listdt": bj["上市日期"].astype(str),
        "Indcd": bj["所属行业"].map(_extract_csrc_letter),
        "exchange": "BJ",
    }))

    df = pd.concat(frames, ignore_index=True)
    df["Listdt"] = pd.to_datetime(df["Listdt"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.drop_duplicates(subset="Stkcd", keep="first")
    df.to_csv(cache, index=False, encoding="utf-8")
    print(f"[阶段1] 公司清单: {len(df)} 家（沪 {(df.exchange == 'SH').sum()} / "
          f"深 {(df.exchange == 'SZ').sum()} / 京 {(df.exchange == 'BJ').sum()}）→ {cache}")
    return df


# ─────────────────────────────────────────────
# 阶段 2：东财行业名（用于上交所股票的行业门类近似映射）
# ─────────────────────────────────────────────

def fetch_em_industry(end_year: int) -> pd.DataFrame:
    cache = os.path.join(CACHE_DIR, "em_industry.csv")
    if os.path.exists(cache):
        print(f"[缓存命中] {cache}")
        return pd.read_csv(cache, dtype=str)

    import akshare as ak

    # 业绩报表带"所处行业"列，用最近一个年报期抓一次即可
    for y in range(end_year, end_year - 3, -1):
        try:
            df = retry_call(ak.stock_yjbb_em, date=f"{y}1231", desc=f"业绩报表{y}")
            if len(df) > 1000:
                break
        except Exception:
            continue
    else:
        raise RuntimeError("业绩报表接口连续失败，无法获取行业信息")

    out = df[["股票代码", "所处行业"]].rename(columns={"股票代码": "Stkcd", "所处行业": "em_industry"})
    out["Stkcd"] = out["Stkcd"].astype(str).str.zfill(6)
    out = out.drop_duplicates(subset="Stkcd")
    out.to_csv(cache, index=False, encoding="utf-8")
    print(f"[阶段2] 东财行业名: {len(out)} 家 → {cache}")
    return out


# 东财行业名 → 证监会门类字母（近似映射，仅用于缺官方代码的上交所股票）
EM_INDUSTRY_RULES = [
    # (关键词列表, 门类字母) —— 按顺序匹配，金融/地产必须排在前面
    (["银行", "保险", "证券", "期货", "多元金融", "金融"], "J"),
    (["房地产"], "K"),
    (["煤炭", "石油", "油气", "采掘", "矿业", "贵金属"], "B"),
    (["电力", "燃气", "水务", "公用事业"], "D"),
    (["建筑", "工程", "装修"], "E"),
    (["医药商业", "商业", "贸易", "百货", "零售", "汽车服务", "珠宝"], "F"),
    (["物流", "航空", "航运", "港口", "铁路", "公路", "机场", "交运"], "G"),
    (["酒店", "餐饮", "旅游"], "H"),
    (["软件开发", "互联网", "通信服务", "电信", "信息技术", "游戏", "传媒"], "I"),
    (["教育"], "P"),
    (["医疗服务", "卫生"], "Q"),
    (["文化", "娱乐", "广播", "影视"], "R"),
    (["环保", "环境"], "N"),
    (["农", "林", "牧", "渔", "种植", "养殖"], "A"),
    (["租赁", "商务服务"], "L"),
    (["科研", "研究", "专业技术", "专业服务"], "M"),
    (["综合"], "S"),
]


def em_industry_to_csrc(name: str) -> str:
    """东财行业名 → 证监会门类字母；匹配不到默认 C（制造业，全市场最大门类）"""
    if not isinstance(name, str) or not name.strip():
        return "C"
    for keywords, letter in EM_INDUSTRY_RULES:
        if any(k in name for k in keywords):
            return letter
    return "C"


# ─────────────────────────────────────────────
# 阶段 3：财务三表（东财批量接口，每年 3 次请求）
# ─────────────────────────────────────────────

def fetch_statements_year(year: int) -> pd.DataFrame:
    cache = os.path.join(CACHE_DIR, f"stmt_{year}.csv")
    if os.path.exists(cache):
        print(f"[缓存命中] {cache}")
        return pd.read_csv(cache, dtype={"Stkcd": str})

    import akshare as ak

    date = f"{year}1231"
    bs = retry_call(ak.stock_zcfz_em, date=date, desc=f"{year}资产负债表")
    inc = retry_call(ak.stock_lrb_em, date=date, desc=f"{year}利润表")
    cf = retry_call(ak.stock_xjll_em, date=date, desc=f"{year}现金流量表")

    def code_col(df):
        return df["股票代码"].astype(str).str.zfill(6)

    m = pd.DataFrame({"Stkcd": code_col(bs), "Comnam_em": bs["股票简称"].map(_norm_name)})
    for col, src in [
        ("F011201A", "资产-货币资金"), ("F011101A", "资产-应收账款"),
        ("F011001A", "资产-存货"), ("F010101A", "资产-总资产"),
        ("F011301A", "负债-应付账款"), ("F010201A", "负债-总负债"),
        ("F010301A", "股东权益合计"),
    ]:
        m[col] = to_num(bs[src])

    inc_m = pd.DataFrame({
        "Stkcd": code_col(inc),
        "F030101A": to_num(inc["净利润"]),
        "F020101A": to_num(inc["营业总收入"]),
        "F020201A": to_num(inc["营业总支出-营业支出"]),
        "F020301A": to_num(inc["营业利润"]),
    })
    cf_m = pd.DataFrame({
        "Stkcd": code_col(cf),
        "F040101A": to_num(cf["经营性现金流-现金流量净额"]),
    })

    m = m.merge(inc_m, on="Stkcd", how="outer").merge(cf_m, on="Stkcd", how="outer")
    # 只保留沪深 A 股（排除北交所 4/8 开头与 CDR 689，东财批量接口本身已不含北交所）
    m = m[m["Stkcd"].str.match(r"^[036]\d{5}$") & ~m["Stkcd"].str.startswith("689")]
    m = m.drop_duplicates(subset="Stkcd")
    m.to_csv(cache, index=False, encoding="utf-8")
    print(f"[阶段3] {year} 年报三表: {len(m)} 家 → {cache}")
    return m


# ─────────────────────────────────────────────
# 阶段 4：年末总市值（东财估值分析，按交易日批量分页）
# ─────────────────────────────────────────────

EM_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def fetch_market_value_year(year: int) -> pd.DataFrame:
    cache = os.path.join(CACHE_DIR, f"mv_{year}.csv")
    if os.path.exists(cache):
        print(f"[缓存命中] {cache}")
        return pd.read_csv(cache, dtype={"Stkcd": str})

    # 查 12 月最后一周区间，再取每只股票该区间内最后一个交易日，天然兼容非交易日
    filter_str = f"(TRADE_DATE>='{year}-12-24')(TRADE_DATE<='{year}-12-31')"
    rows = []
    page = 1
    while True:
        params = {
            "sortColumns": "SECURITY_CODE", "sortTypes": "1",
            "pageSize": "500", "pageNumber": str(page),
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": "SECURITY_CODE,TRADE_DATE,TOTAL_MARKET_CAP",
            "source": "WEB", "client": "WEB",
            "filter": filter_str,
        }
        j = retry_call(requests.get, EM_API, params=params, timeout=30,
                       desc=f"{year}市值 p{page}").json()
        res = j.get("result")
        if not res or not res.get("data"):
            break
        rows.extend(res["data"])
        if page >= res.get("pages", 1):
            break
        page += 1
        time.sleep(0.3)

    if not rows:
        raise RuntimeError(f"{year} 年末市值接口返回空数据")

    df = pd.DataFrame(rows)
    df["Stkcd"] = df["SECURITY_CODE"].astype(str).str.zfill(6)
    df["TRADE_DATE"] = pd.to_datetime(df["TRADE_DATE"])
    df["F100101C"] = pd.to_numeric(df["TOTAL_MARKET_CAP"], errors="coerce")  # 单位：元
    df = (df.sort_values("TRADE_DATE")
            .groupby("Stkcd", as_index=False)
            .last()[["Stkcd", "TRADE_DATE", "F100101C"]])
    df = df[df["Stkcd"].str.match(r"^[036]\d{5}$") & ~df["Stkcd"].str.startswith("689")]
    df.to_csv(cache, index=False, encoding="utf-8")
    last_day = df["TRADE_DATE"].max().strftime("%Y-%m-%d")
    print(f"[阶段4] {year} 年末市值（{last_day}）: {len(df)} 家 → {cache}")
    return df[["Stkcd", "F100101C"]]


# ─────────────────────────────────────────────
# 阶段 5：新浪逐股明细补充（流动资产/流动负债/留存收益/利息费用）
# ─────────────────────────────────────────────

def _sina_symbol(code: str) -> str | None:
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    return None  # 北交所等跳过


def _enrich_one(code: str, years: set[int]) -> dict | None:
    """抓一只股票的新浪全量 BS + 利润表，返回 {年: {字段}}；失败返回 None"""
    import akshare as ak

    symbol = _sina_symbol(code)
    if symbol is None:
        return {}

    bs = retry_call(ak.stock_financial_report_sina, stock=symbol, symbol="资产负债表",
                    tries=3, base_sleep=1.0, desc=f"{code} BS")
    inc = retry_call(ak.stock_financial_report_sina, stock=symbol, symbol="利润表",
                     tries=3, base_sleep=1.0, desc=f"{code} IS")

    out: dict[int, dict] = {}

    if bs is not None and len(bs):
        bs = bs[bs["报告日"].astype(str).str.endswith("1231")].copy()
        bs["year"] = bs["报告日"].astype(str).str[:4].astype(int)
        bs = bs[bs["year"].isin(years)]
        for _, r in bs.iterrows():
            re_s = pd.to_numeric(pd.Series([r.get("盈余公积")]), errors="coerce").iloc[0]
            up_s = pd.to_numeric(pd.Series([r.get("未分配利润")]), errors="coerce").iloc[0]
            re_total = None
            if pd.notna(re_s) or pd.notna(up_s):
                re_total = (re_s if pd.notna(re_s) else 0) + (up_s if pd.notna(up_s) else 0)
            out.setdefault(int(r["year"]), {}).update({
                "F010701A": pd.to_numeric(pd.Series([r.get("流动资产合计")]), errors="coerce").iloc[0],
                "F010801A": pd.to_numeric(pd.Series([r.get("流动负债合计")]), errors="coerce").iloc[0],
                "F010401A": re_total,
            })

    if inc is not None and len(inc) and "利息费用" in inc.columns:
        inc = inc[inc["报告日"].astype(str).str.endswith("1231")].copy()
        inc["year"] = inc["报告日"].astype(str).str[:4].astype(int)
        inc = inc[inc["year"].isin(years)]
        for _, r in inc.iterrows():
            v = pd.to_numeric(pd.Series([r.get("利息费用")]), errors="coerce").iloc[0]
            out.setdefault(int(r["year"]), {}).update({"F020401A": v})

    return out


def enrich_sina(codes: list[str], years: set[int], workers: int = 4) -> pd.DataFrame:
    """逐股抓取，结果按股票逐条追加到缓存 CSV，支持断点续抓"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, "enrich_sina.csv")

    done = set()
    if os.path.exists(cache):
        try:
            done = set(pd.read_csv(cache, dtype={"Stkcd": str})["Stkcd"].unique())
        except Exception:
            done = set()

    todo = [c for c in codes if c not in done]
    print(f"[阶段5] 新浪明细补充: 共 {len(codes)} 家，已完成 {len(done)} 家，待抓 {len(todo)} 家"
          f"（{workers} 线程，可随时中断重跑续传）")
    if not todo:
        return pd.read_csv(cache, dtype={"Stkcd": str})

    buf = []
    flushed = len(done)
    t0 = time.time()

    def flush():
        nonlocal buf
        if not buf:
            return
        df = pd.DataFrame(buf)
        header = not os.path.exists(cache)
        df.to_csv(cache, mode="a", header=header, index=False, encoding="utf-8")
        buf = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_enrich_one, c, years): c for c in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            code = futures[fut]
            try:
                res = fut.result()
            except Exception as e:  # noqa: BLE001
                print(f"    [失败] {code}: {type(e).__name__}: {str(e)[:100]}")
                res = None
            if res is None:
                res = {}
            for y, fields in res.items():
                buf.append({"Stkcd": code, "year": y, **fields})
            if not res:
                buf.append({"Stkcd": code, "year": -1})  # 占位行：标记该股票已抓过但无数据
            if len(buf) >= 2000:
                flush()
            if i % 200 == 0:
                rate = i / (time.time() - t0)
                eta = (len(todo) - i) / rate / 60 if rate else 0
                print(f"    进度 {i}/{len(todo)}（{rate:.1f} 家/秒，预计剩余 {eta:.0f} 分钟）")
            time.sleep(random.uniform(0.02, 0.08))  # 温和限速，避免触发反爬

    flush()
    df = pd.read_csv(cache, dtype={"Stkcd": str})
    print(f"[阶段5] 明细补充完成: 缓存累计 {df['Stkcd'].nunique()} 家 → {cache}")
    return df


# ─────────────────────────────────────────────
# 汇总：生成与 mock_data.py 同构的三张 CSV
# ─────────────────────────────────────────────

def build_outputs(start_year: int, end_year: int, use_enrich: bool, enrich_workers: int):
    os.makedirs(CACHE_DIR, exist_ok=True)
    years = list(range(start_year, end_year + 1))

    base = fetch_company_base()
    em_ind = fetch_em_industry(end_year)

    stmts = {}
    for y in years:
        stmts[y] = fetch_statements_year(y)

    mvs = {}
    for y in years:
        mvs[y] = fetch_market_value_year(y)

    # 以三表出现过的股票为准（公司清单里没有上市日期的退市股也保留财务数据）
    all_codes = sorted(set().union(*[set(s["Stkcd"]) for s in stmts.values()]))

    enrich_df = None
    if use_enrich:
        enrich_df = enrich_sina(all_codes, set(years), workers=enrich_workers)
        enrich_df = enrich_df[enrich_df["year"] > 0].copy()
        enrich_df["year"] = enrich_df["year"].astype(int)

    # ── 财务事实表 ──
    rows = []
    for y in years:
        m = stmts[y].copy()
        m["Accper"] = f"{y}-12-31"
        m["year"] = y
        m = m.merge(mvs[y], on="Stkcd", how="left")
        if enrich_df is not None:
            m = m.merge(enrich_df[enrich_df["year"] == y],
                        on=["Stkcd", "year"], how="left")
        else:
            for c in ("F010701A", "F010801A", "F010401A", "F020401A"):
                m[c] = None
        rows.append(m)

    fin = pd.concat(rows, ignore_index=True)
    for c in FIN_COLS:
        if c not in fin.columns:
            fin[c] = None
    fin = fin[FIN_COLS].sort_values(["Stkcd", "Accper"])

    # ── 公司信息表 ──
    # 名称取该公司在全部年份三表中最近一次出现的简称（退市股也有名字，避免 NULL）
    name_frames = [stmts[y][["Stkcd", "Comnam_em"]].assign(_y=y) for y in years]
    names_em = (pd.concat(name_frames, ignore_index=True)
                  .dropna(subset=["Comnam_em"])
                  .sort_values("_y")
                  .groupby("Stkcd", as_index=False)
                  .last()[["Stkcd", "Comnam_em"]])
    info = pd.DataFrame({"Stkcd": all_codes})
    info = info.merge(base[["Stkcd", "Comnam", "Listdt", "Indcd"]], on="Stkcd", how="left")
    info = info.merge(names_em, on="Stkcd", how="left")
    info = info.merge(em_ind, on="Stkcd", how="left")

    info["Comnam"] = info["Comnam"].fillna(info["Comnam_em"]).fillna("")
    # 行业：优先交易所官方证监会门类（深/京），其次东财行业名映射门类字母（沪）
    official = info["Indcd"].where(info["Indcd"].astype(str).str.match(r"^[A-Z]$"))
    mapped = info["em_industry"].map(em_industry_to_csrc)
    info["Indcd"] = official.fillna(mapped).fillna("C")

    info_out = info[["Stkcd", "Comnam", "Listdt", "Indcd"]].sort_values("Stkcd")

    # ── 曾用名表（akshare 无全量批量曾用名接口，先以现名建档；
    #    需要历史曾用名时可用 stock_info_change_name 逐股补充）──
    names_out = info_out[["Stkcd", "Comnam"]].copy()
    names_out["BeginDate"] = info_out["Listdt"].fillna("1990-01-01")
    names_out["EndDate"] = "2099-12-31"

    fin_path = os.path.join(RAW_DIR, "csmar_financial_raw.csv")
    info_path = os.path.join(RAW_DIR, "csmar_company_info_raw.csv")
    names_path = os.path.join(RAW_DIR, "csmar_company_names_raw.csv")
    fin.to_csv(fin_path, index=False, encoding="utf-8")
    info_out.to_csv(info_path, index=False, encoding="utf-8")
    names_out.to_csv(names_path, index=False, encoding="utf-8")

    n_enrich = fin["F010701A"].notna().mean() * 100 if use_enrich else 0
    print("\n=== 真实数据 CSV 生成完毕 ===")
    print(f"  {fin_path}   : {len(fin):,} 行（{fin['Stkcd'].nunique()} 家 × {len(years)} 年）")
    print(f"  {info_path}  : {len(info_out):,} 家")
    print(f"  {names_path} : {len(names_out):,} 条")
    print(f"  市值覆盖率(F100101C): {fin['F100101C'].notna().mean() * 100:.1f}%")
    if use_enrich:
        print(f"  新浪明细覆盖率(流动资产等): {n_enrich:.1f}%")
    print("下一步：python src/etl/import_raw.py && python src/run_pipeline.py")


def main():
    parser = argparse.ArgumentParser(
        description="EFDW akshare 真实数据抓取器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--start-year", type=int, default=2018, help="起始年度（默认 2018）")
    parser.add_argument("--end-year", type=int, default=2025, help="截止年度（默认 2025）")
    parser.add_argument("--skip-enrich", action="store_true",
                        help="跳过新浪逐股明细补充（流动比率/留存收益/利息费用将为 NULL，约 10 分钟跑完）")
    parser.add_argument("--enrich-only", action="store_true",
                        help="只跑新浪明细补充阶段（断点续抓），然后重新生成 CSV")
    parser.add_argument("--workers", type=int, default=4, help="新浪补充抓取线程数（默认 4）")
    args = parser.parse_args()

    os.makedirs(CACHE_DIR, exist_ok=True)
    if args.enrich_only:
        years = set(range(args.start_year, args.end_year + 1))
        codes = set()
        for y in sorted(years):
            cache = os.path.join(CACHE_DIR, f"stmt_{y}.csv")
            if os.path.exists(cache):
                codes |= set(pd.read_csv(cache, dtype={"Stkcd": str})["Stkcd"])
        enrich_sina(sorted(codes), years, workers=args.workers)
        build_outputs(args.start_year, args.end_year, use_enrich=True,
                      enrich_workers=args.workers)
        return

    build_outputs(args.start_year, args.end_year,
                  use_enrich=not args.skip_enrich, enrich_workers=args.workers)


if __name__ == "__main__":
    sys.exit(main())
