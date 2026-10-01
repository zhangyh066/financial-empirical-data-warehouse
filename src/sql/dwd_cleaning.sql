-- ==========================================
-- DWD 层：标准化清洗与维度表构建 (dwd_cleaning.sql)
-- 企业财务实证数据仓库 EFDW
-- ==========================================

-- 1. 创建并构建公司基本信息维度表 (dim_company)
-- 作用：标准化股票代码，提取核心维度字段，用于整个数仓的实体对齐
DROP TABLE IF EXISTS dwd.dim_company;
CREATE TABLE dwd.dim_company AS
SELECT
    TRIM(Stkcd) AS stkcd,
    TRIM(Comnam) AS company_name,
    CAST(Listdt AS DATE) AS list_date,
    EXTRACT(YEAR FROM CAST(Listdt AS DATE))::INTEGER AS list_year,
    TRIM(Indcd) AS industry_code,
    -- 衍生字段：证监会 2012 版行业门类名称（按门类代码首位映射）
    CASE SUBSTRING(TRIM(Indcd), 1, 1)
        WHEN 'A' THEN '农林牧渔业'
        WHEN 'B' THEN '采矿业'
        WHEN 'C' THEN '制造业'
        WHEN 'D' THEN '电力热力燃气及水生产和供应业'
        WHEN 'E' THEN '建筑业'
        WHEN 'F' THEN '批发和零售业'
        WHEN 'G' THEN '交通运输仓储和邮政业'
        WHEN 'H' THEN '住宿和餐饮业'
        WHEN 'I' THEN '信息传输软件和信息技术服务业'
        WHEN 'J' THEN '金融业'
        WHEN 'K' THEN '房地产业'
        WHEN 'L' THEN '租赁和商务服务业'
        WHEN 'M' THEN '科学研究和技术服务业'
        WHEN 'N' THEN '水利环境和公共设施管理业'
        WHEN 'O' THEN '居民服务修理和其他服务业'
        WHEN 'P' THEN '教育'
        WHEN 'Q' THEN '卫生和社会工作'
        WHEN 'R' THEN '文化体育和娱乐业'
        WHEN 'S' THEN '综合'
        ELSE '其他行业'
    END AS industry_category
FROM ods.company_info;

-- 2. 清洗并标准化财务明细事实表 (fact_financial)
-- 作用：将 CSMAR 难懂的原始字段代码翻译成规范的学术字段名，转换日期，清洗空值
-- 覆盖三大报表核心科目：资产负债表 + 利润表 + 现金流量表 + 市场数据
DROP TABLE IF EXISTS dwd.fact_financial;
CREATE TABLE dwd.fact_financial AS
SELECT
    TRIM(Stkcd) AS stkcd,
    CAST(Accper AS DATE) AS acc_date,
    -- 从 Accper 提取会计年度 (例如 '2022-12-31' 转换为 2022)
    EXTRACT(YEAR FROM CAST(Accper AS DATE))::INTEGER AS year,
    -- 资产负债表
    F010101A AS assets,                 -- 总资产
    F010201A AS debt,                   -- 总负债
    F010301A AS equity,                 -- 所有者权益合计
    F010701A AS current_assets,         -- 流动资产合计
    F010801A AS current_liabilities,    -- 流动负债合计
    F011001A AS inventory,              -- 存货
    F011101A AS accounts_receivable,    -- 应收账款
    F011201A AS cash,                   -- 货币资金
    F011301A AS accounts_payable,       -- 应付账款
    -- 利润表
    F020101A AS revenue,                -- 营业总收入
    F020201A AS operating_cost,         -- 营业成本
    F020301A AS operating_profit,       -- 营业利润
    F020401A AS interest_expense,       -- 利息费用
    F030101A AS net_income,             -- 净利润
    -- 现金流量表
    F040101A AS ocf,                    -- 经营活动产生的现金流量净额
    -- 市场数据
    F100101C AS equity_mv               -- 股权市值
FROM ods.financial_raw
WHERE
    -- 过滤非年底报表（实证论文通常只使用12月31日的年报数据，过滤掉一季报、半年报）
    EXTRACT(MONTH FROM CAST(Accper AS DATE)) = 12;

-- 3. 清洗并构建企业曾用名历史映射表 (dim_company_names)
-- 作用：解决中国上市公司更名、曾用名追溯困难的问题，支撑 4 级名称消歧算法
DROP TABLE IF EXISTS dwd.dim_company_names;
CREATE TABLE dwd.dim_company_names AS
SELECT
    TRIM(Stkcd) AS stkcd,
    TRIM(Comnam) AS company_name_history,
    CAST(BeginDate AS DATE) AS begin_date,
    CAST(EndDate AS DATE) AS end_date
FROM ods.company_names_raw;
