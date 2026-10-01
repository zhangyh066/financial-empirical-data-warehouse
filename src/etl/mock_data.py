"""
EFDW - 企业财务实证数据仓库模拟数据生成器
==========================================
生成 CSMAR 风格的财务三表模拟数据（资产负债表关键科目 + 利润表 + 现金流量表），
覆盖 6 家 A 股上市公司（含银行、地产、制造、白酒、采矿等多行业）2018-2022 年共 30 条观测。

数据生成遵循基本的财务勾稽关系：
  - 资产 = 负债 + 所有者权益
  - 流动资产 = 货币资金 + 存货 + 应收账款 (+ 其他)
  - 各行业具有差异化的杠杆率、毛利率、ROA、周转结构，保证计算出的财务比率在合理区间内
"""
import os
import pandas as pd
import numpy as np

# ─────────────────────────────────────────────
# 行业特征参数（用于生成符合行业常识的财务数据）
# ─────────────────────────────────────────────
INDUSTRY_PROFILE = {
    #            杠杆率   毛利率   ROA     营收/资产  营收增速  流动资产占比 流动负债/总负债 存货/流动资产 应收/营收  应付/成本  利率
    "J": dict(lev=0.90, gm=0.42, roa=0.008, rev_a=0.06, g=0.06, ca=0.85, cl=0.60, inv=0.005, ar=0.05, ap=0.05, ir=0.025),  # 金融业
    "K": dict(lev=0.80, gm=0.28, roa=0.025, rev_a=0.25, g=0.08, ca=0.75, cl=0.70, inv=0.45,  ar=0.08, ap=0.35, ir=0.045),  # 房地产业
    "C": dict(lev=0.55, gm=0.20, roa=0.040, rev_a=0.70, g=0.13, ca=0.45, cl=0.65, inv=0.25,  ar=0.12, ap=0.35, ir=0.040),  # 制造业(一般)
    "B": dict(lev=0.48, gm=0.24, roa=0.040, rev_a=0.80, g=0.05, ca=0.35, cl=0.60, inv=0.15,  ar=0.10, ap=0.30, ir=0.040),  # 采矿业
}

# 个别公司的特殊画像（key: stkcd, 覆盖默认行业参数）
FIRM_OVERRIDE = {
    "600519": dict(lev=0.28, gm=0.75, roa=0.160, rev_a=0.55, g=0.12, ca=0.55, cl=0.55, inv=0.22, ar=0.05, ap=0.25, ir=0.020),  # 贵州茅台：高毛利高ROA低杠杆
    "002594": dict(lev=0.62, gm=0.16, roa=0.030, rev_a=0.80, g=0.20, ca=0.40, cl=0.70, inv=0.28, ar=0.10, ap=0.40, ir=0.042),  # 比亚迪：高成长高杠杆
}


def _clip(v, lo, hi):
    return min(max(v, lo), hi)


def generate_mock_data():
    print("=== 开始生成模拟的财务实证数据 (CSMAR 风格) ===")

    raw_dir = "data/raw"
    if not os.path.exists(raw_dir):
        os.makedirs(raw_dir)

    np.random.seed(42)

    # 1. 公司基本信息表（含曾用名设计，用于企业名称消歧演示）
    companies = [
        {"Stkcd": "000001", "Comnam": "平安银行",   "Listdt": "1991-04-03", "Indcd": "J66"},  # 曾用名：深发展A
        {"Stkcd": "000002", "Comnam": "万科A",      "Listdt": "1991-01-29", "Indcd": "K70"},  # 曾用名：深万科A
        {"Stkcd": "600000", "Comnam": "浦发银行",   "Listdt": "1999-11-10", "Indcd": "J66"},
        {"Stkcd": "600519", "Comnam": "贵州茅台",   "Listdt": "2001-08-27", "Indcd": "C15"},
        {"Stkcd": "002594", "Comnam": "比亚迪",     "Listdt": "2011-06-30", "Indcd": "C37"},
        {"Stkcd": "600028", "Comnam": "中国石化",   "Listdt": "2001-08-08", "Indcd": "B06"},
    ]

    df_info = pd.DataFrame(companies)
    info_path = os.path.join(raw_dir, "csmar_company_info_raw.csv")
    df_info.to_csv(info_path, index=False, encoding="utf-8")
    print(f"[生成成功] 模拟公司基本信息已保存至: {info_path}")

    # 2. 财务三表数据（2018-2022），按行业画像逐年演化
    years = [2018, 2019, 2020, 2021, 2022]
    financial_records = []

    for comp in companies:
        stkcd, indcd = comp["Stkcd"], comp["Indcd"]
        ind_key = indcd[0]  # 行业门类代码
        p = dict(INDUSTRY_PROFILE[ind_key])
        p.update(FIRM_OVERRIDE.get(stkcd, {}))

        # 初始规模：对数均匀分布，50亿 ~ 5000亿
        assets = float(np.exp(np.random.uniform(np.log(5e9), np.log(5e11))))
        revenue = assets * p["rev_a"] * np.random.uniform(0.8, 1.2)

        for year in years:
            accper = f"{year}-12-31"

            # ── 成长演化 ──
            g_rev = _clip(np.random.normal(p["g"], 0.12), -0.30, 0.55)
            revenue = revenue * (1 + g_rev)
            g_assets = _clip(np.random.normal(0.06, 0.08), -0.10, 0.28)
            assets = assets * (1 + g_assets)

            # ── 资产负债表 ──
            lev = _clip(np.random.normal(p["lev"], 0.03), 0.05, 0.95)
            debt = assets * lev
            equity = assets - debt  # 勾稽：资产 = 负债 + 权益
            retained_earnings = equity * np.random.uniform(0.35, 0.85)  # 留存收益（盈余公积+未分配利润）

            ca_ratio = _clip(np.random.normal(p["ca"], 0.03), 0.10, 0.95)
            current_assets = assets * ca_ratio
            cl_ratio = _clip(np.random.normal(p["cl"], 0.05), 0.10, 0.95)
            current_liabilities = debt * cl_ratio

            inventory = current_assets * _clip(np.random.normal(p["inv"], 0.02), 0.0, 0.35)
            accounts_receivable = min(
                revenue * _clip(np.random.normal(p["ar"], 0.03), 0.01, 0.60),
                current_assets * 0.45,  # 保证货币资金为正
            )
            cash = current_assets - inventory - accounts_receivable
            accounts_payable = min(
                (revenue * (1 - p["gm"])) * _clip(np.random.normal(p["ap"], 0.03), 0.05, 0.70),
                current_liabilities * 0.70,
            )

            # ── 利润表 ──
            gm = _clip(np.random.normal(p["gm"], 0.03), 0.05, 0.90)
            operating_cost = revenue * (1 - gm)
            net_income = assets * _clip(np.random.normal(p["roa"], 0.025), -0.15, 0.30)
            interest_expense = debt * _clip(np.random.normal(p["ir"], 0.006), 0.005, 0.10)
            # 营业利润 ≈ 净利润 + 利息费用 + 税费等余项（保证利息保障倍数为正且合理）
            operating_profit = net_income + interest_expense + abs(np.random.normal(0.03, 0.02)) * revenue

            # ── 现金流量表 ──
            ocf = net_income * _clip(np.random.normal(1.0, 0.25), 0.30, 1.80)

            # ── 市场数据 ──
            equity_mv = equity * np.random.uniform(1.0, 4.0)

            financial_records.append({
                "Stkcd": stkcd,
                "Accper": accper,
                "F010101A": round(assets, 2),                  # 总资产
                "F010201A": round(debt, 2),                    # 总负债
                "F010301A": round(equity, 2),                  # 所有者权益合计
                "F010401A": round(retained_earnings, 2),       # 留存收益（盈余公积+未分配利润）
                "F010701A": round(current_assets, 2),          # 流动资产合计
                "F010801A": round(current_liabilities, 2),     # 流动负债合计
                "F011001A": round(inventory, 2),               # 存货
                "F011101A": round(accounts_receivable, 2),     # 应收账款
                "F011201A": round(cash, 2),                    # 货币资金
                "F011301A": round(accounts_payable, 2),        # 应付账款
                "F020101A": round(revenue, 2),                 # 营业总收入
                "F020201A": round(operating_cost, 2),          # 营业成本
                "F020301A": round(operating_profit, 2),        # 营业利润
                "F020401A": round(interest_expense, 2),        # 利息费用
                "F030101A": round(net_income, 2),              # 净利润
                "F040101A": round(ocf, 2),                     # 经营活动现金流量净额
                "F100101C": round(equity_mv, 2),               # 股权市值
            })

    df_fin = pd.DataFrame(financial_records)
    fin_path = os.path.join(raw_dir, "csmar_financial_raw.csv")
    df_fin.to_csv(fin_path, index=False, encoding="utf-8")
    print(f"[生成成功] 模拟财务三表数据已保存至: {fin_path} ({len(df_fin)} 行)")

    # 3. 企业曾用名历史变更表（ODS），用于 4 级企业名称消歧匹配演示
    name_history = [
        {"Stkcd": "000001", "Comnam": "深发展A", "BeginDate": "1991-04-03", "EndDate": "2012-07-26"},
        {"Stkcd": "000001", "Comnam": "平安银行", "BeginDate": "2012-07-27", "EndDate": "2099-12-31"},
        {"Stkcd": "000002", "Comnam": "深万科A", "BeginDate": "1991-01-29", "EndDate": "1993-11-08"},
        {"Stkcd": "000002", "Comnam": "万科A",    "BeginDate": "1993-11-09", "EndDate": "2099-12-31"},
        {"Stkcd": "600000", "Comnam": "浦发银行", "BeginDate": "1999-11-10", "EndDate": "2099-12-31"},
        {"Stkcd": "600519", "Comnam": "贵州茅台", "BeginDate": "2001-08-27", "EndDate": "2099-12-31"},
        {"Stkcd": "002594", "Comnam": "比亚迪",   "BeginDate": "2011-06-30", "EndDate": "2099-12-31"},
        {"Stkcd": "600028", "Comnam": "中国石化", "BeginDate": "2001-08-08", "EndDate": "2099-12-31"},
    ]
    df_names = pd.DataFrame(name_history)
    names_path = os.path.join(raw_dir, "csmar_company_names_raw.csv")
    df_names.to_csv(names_path, index=False, encoding="utf-8")
    print(f"[生成成功] 模拟企业曾用名历史数据已保存至: {names_path}")

    print("=== 所有模拟数据生成成功！ ===")


if __name__ == "__main__":
    generate_mock_data()
