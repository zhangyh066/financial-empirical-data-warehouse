-- ==========================================
-- ADS 层：财务勾稽关系自动校验 (ads_quality.sql)
-- 企业财务实证数据仓库 EFDW
-- 对 DWD 层财务明细逐企业-年度执行会计学勾稽恒等式校验，
-- 违规记录落入 ads.data_quality_report（长表：一条违规一行），
-- 作为接入真实 CSMAR/Wind 数据时的数据质量闸门
-- ==========================================

DROP TABLE IF EXISTS ads.data_quality_report;
CREATE TABLE ads.data_quality_report AS
SELECT f.stkcd, c.company_name, f.year,
       '资产 = 负债 + 权益' AS check_item,
       'FAIL' AS severity,
       '资产(' || ROUND(f.assets, 2) || ') ≠ 负债(' || ROUND(f.debt, 2) || ') + 权益(' || ROUND(f.equity, 2) || ')' AS detail
FROM dwd.fact_financial f
LEFT JOIN dwd.dim_company c ON f.stkcd = c.stkcd
WHERE ABS(f.assets - f.debt - f.equity) > 0.01

UNION ALL
-- 权益为负：资不抵债（也可能是数据符号错误）
SELECT f.stkcd, c.company_name, f.year,
       '所有者权益 > 0', 'WARN',
       '权益 = ' || ROUND(f.equity, 2) || '，存在资不抵债或符号错误风险'
FROM dwd.fact_financial f
LEFT JOIN dwd.dim_company c ON f.stkcd = c.stkcd
WHERE f.equity <= 0

UNION ALL
-- 流动资产不得超过总资产
SELECT f.stkcd, c.company_name, f.year,
       '流动资产 ≤ 总资产', 'FAIL',
       '流动资产(' || ROUND(f.current_assets, 2) || ') > 总资产(' || ROUND(f.assets, 2) || ')'
FROM dwd.fact_financial f
LEFT JOIN dwd.dim_company c ON f.stkcd = c.stkcd
WHERE f.current_assets > f.assets + 0.01

UNION ALL
-- 流动负债不得超过总负债
SELECT f.stkcd, c.company_name, f.year,
       '流动负债 ≤ 总负债', 'FAIL',
       '流动负债(' || ROUND(f.current_liabilities, 2) || ') > 总负债(' || ROUND(f.debt, 2) || ')'
FROM dwd.fact_financial f
LEFT JOIN dwd.dim_company c ON f.stkcd = c.stkcd
WHERE f.current_liabilities > f.debt + 0.01

UNION ALL
-- 存货非负
SELECT f.stkcd, c.company_name, f.year,
       '存货 ≥ 0', 'FAIL',
       '存货 = ' || ROUND(f.inventory, 2)
FROM dwd.fact_financial f
LEFT JOIN dwd.dim_company c ON f.stkcd = c.stkcd
WHERE f.inventory < 0

UNION ALL
-- 货币资金非负
SELECT f.stkcd, c.company_name, f.year,
       '货币资金 ≥ 0', 'FAIL',
       '货币资金 = ' || ROUND(f.cash, 2)
FROM dwd.fact_financial f
LEFT JOIN dwd.dim_company c ON f.stkcd = c.stkcd
WHERE f.cash < 0

UNION ALL
-- 流动资产构成项合计不超过流动资产（货币资金+存货+应收 ≤ 流动资产）
SELECT f.stkcd, c.company_name, f.year,
       '流动资产构成勾稽', 'WARN',
       '货币资金+存货+应收(' || ROUND(f.cash + f.inventory + f.accounts_receivable, 2) ||
       ') > 流动资产(' || ROUND(f.current_assets, 2) || ')，请核查其他流动资产科目'
FROM dwd.fact_financial f
LEFT JOIN dwd.dim_company c ON f.stkcd = c.stkcd
WHERE f.cash + f.inventory + f.accounts_receivable > f.current_assets + 0.01

UNION ALL
-- 应收账款非负
SELECT f.stkcd, c.company_name, f.year,
       '应收账款 ≥ 0', 'WARN',
       '应收账款 = ' || ROUND(f.accounts_receivable, 2) || '，负值需核实是否为预收款重分类'
FROM dwd.fact_financial f
LEFT JOIN dwd.dim_company c ON f.stkcd = c.stkcd
WHERE f.accounts_receivable < 0

UNION ALL
-- 应付账款非负
SELECT f.stkcd, c.company_name, f.year,
       '应付账款 ≥ 0', 'WARN',
       '应付账款 = ' || ROUND(f.accounts_payable, 2) || '，负值需核实是否为预付账款重分类'
FROM dwd.fact_financial f
LEFT JOIN dwd.dim_company c ON f.stkcd = c.stkcd
WHERE f.accounts_payable < 0

UNION ALL
-- 营业收入为正
SELECT f.stkcd, c.company_name, f.year,
       '营业收入 > 0', 'WARN',
       '营业收入 = ' || ROUND(f.revenue, 2) || '，非正值需核实退市风险或数据缺口'
FROM dwd.fact_financial f
LEFT JOIN dwd.dim_company c ON f.stkcd = c.stkcd
WHERE f.revenue <= 0

ORDER BY stkcd, year, check_item;
