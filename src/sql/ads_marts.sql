-- ==========================================
-- ADS 层：应用层聚合数据集市 (ads_marts.sql)
-- 企业财务实证数据仓库 EFDW
-- 面向论文分析场景的高度聚合服务数据，可直接支撑行业比较与年度趋势分析
-- ==========================================

-- 1. 行业-年度聚合统计表
--    服务场景：行业面板回归的行业层面变量、行业比较分析、Figure 行业趋势图
DROP TABLE IF EXISTS ads.industry_year_stats;
CREATE TABLE ads.industry_year_stats AS
SELECT
    industry_category,
    year,
    COUNT(*) AS n_firms,
    ROUND(AVG(roa), 4) AS avg_roa,
    ROUND(AVG(roe), 4) AS avg_roe,
    ROUND(AVG(gross_margin), 4) AS avg_gross_margin,
    ROUND(AVG(leverage), 4) AS avg_leverage,
    ROUND(AVG(revenue_growth), 4) AS avg_revenue_growth,
    ROUND(AVG(current_ratio), 4) AS avg_current_ratio,
    ROUND(AVG(total_asset_turnover), 4) AS avg_total_asset_turnover,
    ROUND(SUM(revenue), 2) AS total_revenue,
    ROUND(SUM(net_income), 2) AS total_net_income
FROM dws.firm_year_panel
GROUP BY industry_category, year
ORDER BY industry_category, year;

-- 2. 全市场年度概览表
--    服务场景：宏观经济与资本市场整体趋势描述、论文引言/背景数据
DROP TABLE IF EXISTS ads.market_year_overview;
CREATE TABLE ads.market_year_overview AS
SELECT
    year,
    COUNT(DISTINCT stkcd) AS n_firms,
    COUNT(*) AS n_obs,
    ROUND(SUM(assets), 2) AS total_assets,
    ROUND(SUM(revenue), 2) AS total_revenue,
    ROUND(SUM(net_income), 2) AS total_net_income,
    ROUND(AVG(roa), 4) AS avg_roa,
    ROUND(AVG(roe), 4) AS avg_roe,
    ROUND(AVG(leverage), 4) AS avg_leverage,
    ROUND(AVG(revenue_growth), 4) AS avg_revenue_growth
FROM dws.firm_year_panel
GROUP BY year
ORDER BY year;
