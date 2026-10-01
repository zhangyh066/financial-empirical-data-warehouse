-- ==========================================
-- DWS 层：企业-年度财务面板宽表 (dws_panel.sql)
-- 企业财务实证数据仓库 EFDW
-- 计算盈利能力、成长性、偿债能力、营运能力、现金流五大类核心财务指标
-- 及杜邦分解因子、多期滞后项/超前项
-- ==========================================

DROP TABLE IF EXISTS dws.firm_year_panel;
CREATE TABLE dws.firm_year_panel AS
WITH base_panel AS (
    -- 1. 拼表：财务三表明细 + 公司维度（通过 stkcd 对齐，附带上市信息用于样本筛选）
    SELECT
        f.stkcd,
        c.company_name,
        c.industry_code,
        c.industry_category,
        c.list_date,
        c.list_year,
        f.year,
        f.assets,
        f.debt,
        f.equity,
        f.current_assets,
        f.current_liabilities,
        f.inventory,
        f.accounts_receivable,
        f.cash,
        f.accounts_payable,
        f.revenue,
        f.operating_cost,
        f.operating_profit,
        f.interest_expense,
        f.net_income,
        f.ocf,
        f.equity_mv
    FROM dwd.fact_financial f
    LEFT JOIN dwd.dim_company c ON f.stkcd = c.stkcd
),
ratios AS (
    -- 2. 五大类核心财务指标 + 杜邦分解因子（全部为学术惯用口径）
    SELECT
        *,
        -- ── 规模与市场 ──
        LN(assets) AS firm_size,                                    -- 企业规模 Size
        debt / assets AS leverage,                                  -- 资产负债率 Lev
        (equity_mv + debt) / assets AS tobin_q,                     -- 托宾 Q
        -- ── 盈利能力 ──
        net_income / assets AS roa,                                 -- 总资产收益率 ROA
        net_income / NULLIF(equity, 0) AS roe,                      -- 净资产收益率 ROE
        (revenue - operating_cost) / NULLIF(revenue, 0) AS gross_margin,   -- 销售毛利率
        net_income / NULLIF(revenue, 0) AS net_margin,              -- 销售净利率
        operating_profit / NULLIF(revenue, 0) AS operating_margin,  -- 营业利润率
        -- ── 偿债能力 ──
        current_assets / NULLIF(current_liabilities, 0) AS current_ratio,          -- 流动比率
        (current_assets - inventory) / NULLIF(current_liabilities, 0) AS quick_ratio, -- 速动比率
        cash / NULLIF(current_liabilities, 0) AS cash_ratio,                       -- 现金比率
        (operating_profit + interest_expense) / NULLIF(interest_expense, 0) AS interest_coverage, -- 利息保障倍数
        -- ── 营运能力 ──
        revenue / NULLIF(assets, 0) AS total_asset_turnover,        -- 总资产周转率
        operating_cost / NULLIF(inventory, 0) AS inventory_turnover,           -- 存货周转率
        revenue / NULLIF(accounts_receivable, 0) AS receivables_turnover,      -- 应收账款周转率
        operating_cost / NULLIF(accounts_payable, 0) AS payable_turnover,      -- 应付账款周转率
        -- 现金转换周期 CCC = 存货周转天数 + 应收周转天数 - 应付周转天数（金融类企业无存货，置 NULL）
        CASE
            WHEN inventory > 0 AND accounts_payable > 0
            THEN 365 * inventory / operating_cost
               + 365 * accounts_receivable / NULLIF(revenue, 0)
               - 365 * accounts_payable / operating_cost
        END AS cash_conversion_cycle,
        -- ── 现金流能力 ──
        ocf / NULLIF(assets, 0) AS ocf_to_assets,                   -- 经营现金流 / 总资产
        ocf / NULLIF(net_income, 0) AS ocf_to_net_income,           -- 盈余现金保障倍数
        -- ── 杜邦分解三因子：ROE = 净利率 × 总资产周转率 × 权益乘数 ──
        assets / NULLIF(equity, 0) AS equity_multiplier,            -- 权益乘数
        net_income / NULLIF(revenue, 0)
            * (revenue / NULLIF(assets, 0))
            * (assets / NULLIF(equity, 0)) AS dupont_roe             -- 杜邦验证 ROE（应 ≈ roe）
    FROM base_panel
),
growth AS (
    -- 3. 成长性指标（依赖上期值，使用窗口函数）
    SELECT
        *,
        (revenue - LAG(revenue, 1) OVER w) / NULLIF(LAG(revenue, 1) OVER w, 0)     AS revenue_growth,
        (assets - LAG(assets, 1) OVER w) / NULLIF(LAG(assets, 1) OVER w, 0)        AS asset_growth,
        (net_income - LAG(net_income, 1) OVER w) / NULLIF(LAG(net_income, 1) OVER w, 0) AS net_income_growth
    FROM ratios
    WINDOW w AS (PARTITION BY stkcd ORDER BY year)
)
-- 4. 多期滞后项 / 超前项（Stata 中 L.roa、L2.roa、F.roa 的 SQL 窗口函数实现）
SELECT
    *,
    LAG(roa, 1) OVER w       AS lag_roa,
    LAG(roa, 2) OVER w       AS lag2_roa,
    LAG(roe, 1) OVER w       AS lag_roe,
    LAG(leverage, 1) OVER w  AS lag_leverage,
    LAG(revenue_growth, 1) OVER w AS lag_revenue_growth,
    LEAD(roa, 1) OVER w      AS lead_roa
FROM growth
WINDOW w AS (PARTITION BY stkcd ORDER BY year);
