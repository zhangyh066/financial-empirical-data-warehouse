# 企业财务实证数据仓库系统 (EFDW)

专为**财务实证研究**（公司金融、会计、财务管理方向）设计的轻量化企业级数据仓库。基于 **Python + DuckDB + SQL + FastAPI** 技术栈，围绕财务三张报表构建 **ODS → DWD → DWS → ADS** 四层架构，解决财务面板数据处理中的核心痛点：**多表合并、财务指标计算、样本筛选、数据导出**。

---

## 🌟 课程项目核心亮点

- **架构设计**：采用规范数仓建模思想，独立搭建 **ODS（贴源层）→ DWD（明细层）→ DWS（服务层）→ ADS（应用层）** 四层架构；ADS 层落地为行业-年度聚合集市与全市场年度概览表，实现原始只读数据到高度聚合服务数据的清晰解耦。
- **ETL 管线**：基于 **Python + DuckDB** 构建高性能数据流，覆盖财务三大报表核心科目（资产、负债、权益、营收、成本、利润、现金流等），利用 SQL 窗口函数计算**盈利能力（ROA/ROE/毛利率）、成长性（营收/资产增长率）、偿债能力（流动比率/利息保障倍数）、营运能力（周转率/现金转换周期）** 五大类 20+ 企业核心财务指标，并内置**杜邦分解因子**与**多期滞后项**（L / L2 / F），千万级财务数据查询响应达毫秒级。
- **API 开发**：使用 **FastAPI** 开发 **13 个核心业务接口**，集成**数据缩尾（Winsorize）、动态样本筛选（剔除金融业 / 剔除上市不足一年新股）、行业聚合统计**等学术实证逻辑，支持在线海量样本分析。
- **智能消歧**：实现 **4 级匹配算法**的企业名称消歧（当前名精确 → 曾用名精确 → 子串包含 → Jaro-Winkler 模糊匹配），精确对齐多源异构数据中的主体身份，解决中国上市公司更名、曾用名追溯困难问题。
- **前端交付**：自主开发**前后端分离 Web 看板**，提供变量可视化、行业分布统计与企业级**杜邦分解（DuPont Analysis）**；支持一键导出含中文的 **Stata（.dta）及 CSV** 格式数据集，可直接用于实证回归。

---

## 📁 目录结构

```
enterprise-financial-data-warehouse/
├── data/
│   ├── raw/                        # 存放原始 CSMAR/Wind 下载的 Excel/CSV
│   │   ├── pending/                #   待导入的文件放这里（推荐）
│   │   └── imported/               #   已导入的文件自动归档到这里
│   └── warehouse/                  # DuckDB 数据库文件 + 导出数据
│       └── financial_warehouse.db  #   核心数据库（一个文件走天下）
│
├── src/                            # 后端（Python + SQL）
│   ├── api.py                      #   FastAPI 后端 API 服务
│   ├── initialize_db.py            #   初始化数据库和四层 Schema
│   ├── run_pipeline.py             #   一键运行 DWD → DWS → ADS 清洗管线
│   ├── export.py                   #   一键导出 Stata .dta / CSV
│   ├── inspect_db.py               #   快速查看数据库内容
│   ├── etl/
│   │   ├── import_raw.py           #   通用数据导入引擎（支持 CSV/Excel/Parquet/Stata/SAS/SPSS）
│   │   └── mock_data.py            #   生成模拟 CSMAR 财务三表数据
│   └── sql/
│       ├── dwd_cleaning.sql        #   SQL：清洗 ODS → 构建 DWD 维度表和财务明细表
│       ├── dws_panel.sql           #   SQL：生成 DWS 企业-年度财务指标面板宽表
│       └── ads_marts.sql           #   SQL：生成 ADS 行业聚合数据集市
│
├── frontend/                       # 前端（纯 HTML + CSS + JS，前后端分离）
│   ├── index.html                  #   数据看板入口页
│   ├── style.css                   #   科研风样式
│   └── app.js                      #   前端交互逻辑（调用后端 API）
│
└── workbench/                      # 交互分析
    └── analysis_workbench.ipynb    #   金融硕士论文实证分析工作台模板
```

---

## 🏗️ 四层数仓架构

```
┌───────────────────────────────────────────────────────────┐
│  ODS  贴源层  →  原始 CSMAR 财务三表数据，只读不修改        │
│  DWD  明细层  →  字段翻译、公司维度消歧、年报过滤            │
│  DWS  服务层  →  企业-年度财务指标面板（盈利能力/成长性/      │
│                 偿债能力/营运能力 + 杜邦分解 + 多期滞后项）  │
│  ADS  应用层  →  行业-年度聚合集市 / 全市场年度概览          │
└───────────────────────────────────────────────────────────┘
```

### 数据流转示意

```
CSMAR Excel 下载                    你在 Stata 中的回归
     │                                  ▲
     ▼                                  │
import_raw.py  ──►  ODS (原始表)       │
                      │                │
                      ▼                │
dwd_cleaning.sql  ──►  DWD (清洗表)   │
                      │                │
                      ▼                │
dws_panel.sql  ──►  DWS (指标面板)    │
                      │                │
                      ▼                │
ads_marts.sql  ──►  ADS (聚合集市) ───┘
                                   export.py / API
```

### DWS 层自动计算的财务指标（按五大能力分组）

| 分类 | 变量名 | 公式 | 说明 |
|------|--------|------|------|
| 规模/市场 | `firm_size` | `ln(assets)` | 企业规模 |
| 规模/市场 | `leverage` | `debt / assets` | 资产负债率 |
| 规模/市场 | `tobin_q` | `(equity_mv + debt) / assets` | 托宾 Q |
| 盈利能力 | `roa` | `net_income / assets` | 总资产收益率 |
| 盈利能力 | `roe` | `net_income / equity` | 净资产收益率 |
| 盈利能力 | `gross_margin` | `(revenue - cost) / revenue` | 销售毛利率 |
| 盈利能力 | `net_margin` | `net_income / revenue` | 销售净利率 |
| 成长性 | `revenue_growth` | `(revₜ - revₜ₋₁) / revₜ₋₁` | 营收增长率 |
| 成长性 | `asset_growth` | `(Aₜ - Aₜ₋₁) / Aₜ₋₁` | 总资产增长率 |
| 成长性 | `net_income_growth` | 同上 | 净利润增长率 |
| 偿债能力 | `current_ratio` | `流动资产 / 流动负债` | 流动比率 |
| 偿债能力 | `quick_ratio` | `(流动资产 - 存货) / 流动负债` | 速动比率 |
| 偿债能力 | `cash_ratio` | `货币资金 / 流动负债` | 现金比率 |
| 偿债能力 | `interest_coverage` | `(营业利润+利息费用) / 利息费用` | 利息保障倍数 |
| 营运能力 | `total_asset_turnover` | `revenue / assets` | 总资产周转率 |
| 营运能力 | `inventory_turnover` | `cost / inventory` | 存货周转率 |
| 营运能力 | `receivables_turnover` | `revenue / AR` | 应收账款周转率 |
| 营运能力 | `cash_conversion_cycle` | `DIO + DSO - DPO` | 现金转换周期(天) |
| 现金流 | `ocf_to_assets` | `经营现金流 / assets` | 资产现金回收率 |
| 杜邦分解 | `equity_multiplier` | `assets / equity` | 权益乘数 |
| 杜邦分解 | `dupont_roe` | `净利率 × 周转率 × 权益乘数` | 杜邦验证 ROE |
| 时序项 | `lag_roa` / `lag2_roa` | `LAG(roa, 1/2)` | 滞后一/两期 |
| 时序项 | `lead_roa` | `LEAD(roa, 1)` | 超前一期 |

> **杜邦恒等式验证**：`dupont_roe ≡ roe`（数仓内置校验字段，公司详情页可视化展示三因子分解）。

---

## 🚀 首次安装配置（只做一次）

### 1. 安装 Python 依赖

确保你的电脑有 Python 3.8+，然后在终端运行：

```bash
pip install pandas polars duckdb pyarrow fastapi uvicorn python-multipart
```

> 已安装的包会自动跳过，不必担心重复安装。

### 2. 初始化数据库 & 生成模拟数据

```bash
cd enterprise-financial-data-warehouse
python src/initialize_db.py
python src/etl/mock_data.py
```

执行后会：
- 创建四层 Schema：`ods` / `dwd` / `dws` / `ads`
- 在 `data/warehouse/` 下生成 `financial_warehouse.db`
- 在 `data/raw/` 下生成 3 张模拟数据表（公司信息、财务三表、曾用名历史）

### 3. 导入数据到 ODS 层 & 运行清洗管线

```bash
python src/etl/import_raw.py
python src/run_pipeline.py
```

执行后会：
- 将 3 张模拟表导入 ODS 贴源层（原始文件自动归档到 `data/raw/imported/`）
- 执行 `dwd_cleaning.sql`：翻译字段名、构建公司维度表、过滤年报
- 执行 `dws_panel.sql`：拼表、计算五大能力 20+ 财务指标 + 杜邦分解 + 多期滞后项
- 执行 `ads_marts.sql`：生成行业-年度聚合集市与全市场年度概览
- 在终端打印 DWS / ADS 样例供核对

### 4. 验证安装结果

```bash
python src/inspect_db.py
```

你会看到数据库中各层表的列表、DWS 面板数据样例和各层行数统计。

---

## 🖥️ 启动后端服务

**步骤 1：先切换到项目根目录**

```bash
cd enterprise-financial-data-warehouse
```

> ⚠️ **这一步最容易漏掉**。如果不在这个目录下执行，Python 找不到 `src.api` 模块。

**步骤 2：启动 FastAPI 服务**

```bash
uvicorn src.api:app --port 8000
```

看到这行输出就表示成功了：

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

---

## 🌐 前端看板使用

后端运行后，**直接用浏览器打开**：

```
frontend/index.html
```

> 文件路径：`frontend/index.html`

前端默认连接 `http://127.0.0.1:8000`，如果你换了端口，打开 `frontend/app.js` 第 12 行修改。

### 前端功能一览

| 功能标签 | 能做什么 |
|----------|----------|
| **样本概览** | 数仓整体指标、学术 Table 1 描述性统计（支持缩尾、去金融业）、行业分布、变量直方图 |
| **企业检索** | 搜索公司，查看财务指标时序、**杜邦三因子分解**、ROA/ROE 趋势图 |
| **数据导出** | 按财务能力分组勾选变量、样本清洗（去金融业/去新股）、缩尾，一键导出 CSV 或 Stata .dta |
| **数据导入** | 上传新的 Excel/CSV 文件导入数仓，查看四层表清单 |
| **智能对齐** | 批量粘贴凌乱企业名称，4 级匹配算法对齐到标准股票代码 |

---

## 📊 三种使用方式

### 方式一：Web 看板（最直观，推荐新手）

启动后端 → 打开前端 → 点点点。

### 方式二：直接写 SQL（最灵活，推荐进阶）

```python
import duckdb

conn = duckdb.connect("data/warehouse/financial_warehouse.db")

# 盈利能力-偿债能力联合分析（剔除金融业、滞后项齐全）
df = conn.execute("""
    SELECT stkcd, company_name, year,
           ROUND(roa, 4)        AS ROA,
           ROUND(roe, 4)        AS ROE,
           ROUND(current_ratio, 2) AS Current,
           ROUND(interest_coverage, 2) AS Coverage,
           ROUND(lag_roa, 4)    AS L_ROA,
           ROUND(lag2_roa, 4)   AS L2_ROA
    FROM dws.firm_year_panel
    WHERE year BETWEEN 2019 AND 2022
      AND industry_category != '金融业'
    ORDER BY stkcd, year
""").fetchdf()
conn.close()
```

### 方式三：API 编程调用（适合自动化）

```python
import requests

BASE = "http://127.0.0.1:8000"

# 描述性统计（缩尾 1%、去金融业、剔除新股）
r = requests.get(f"{BASE}/api/descriptive", params={
    "year_min": 2018, "year_max": 2022,
    "exclude_financial": True, "exclude_newly_listed": True,
    "winsorize_pct": 0.01
})
stats = r.json()

# 导出 Stata 回归数据集
r = requests.get(f"{BASE}/api/export/stata", params={
    "year_min": 2019, "year_max": 2022,
    "variables": "firm_size,leverage,roa,roe,revenue_growth,current_ratio,lag_roa,lag2_roa"
})
with open("my_regression.dta", "wb") as f:
    f.write(r.content)

# 企业名称智能消歧（4 级匹配）
r = requests.post(f"{BASE}/api/resolve", json={
    "names": ["深发展", "万科集团", "比亚迪"],
    "threshold": 0.6
})
print(r.json())
```

---

## 📤 导出数据到 Stata

### 命令行导出

```bash
python src/export.py
```

生成文件：
- `data/warehouse/regression_panel_data.dta`（Stata 14+ 格式，含中文）
- `data/warehouse/regression_panel_data.csv`（UTF-8 编码）

### 在 Stata 中使用

```stata
* 加载数据
use "data/warehouse/regression_panel_data.dta", clear

* 设定面板结构
xtset stkcd year

* 描述性统计
sum roa roe firm_size leverage lag_roa lag2_roa

* 基准回归（公司聚类稳健标准误）
reg roa lag_roa lag2_roa firm_size leverage i.year, cluster(stkcd)
```

---

## 🔌 接入真实 CSMAR/Wind 数据

拿到真实数据后只需三步：

1. 把 CSMAR 下载的 Excel/CSV 放到 `data/raw/`
2. 修改 `src/sql/dwd_cleaning.sql` 中的字段映射（把 `F010101A` 等改成你数据里的真实列名）
3. 重新运行 `import_raw.py` + `run_pipeline.py`

常用科目映射参考：

| SQL 中名称 | 含义 | CSMAR 常见列名 |
|-----------|------|---------------|
| `assets` | 总资产 | `A001000000` / `F010101A` |
| `debt` | 总负债 | `A002000000` / `F010201A` |
| `equity` | 所有者权益 | `A003000000` / `F010301A` |
| `revenue` | 营业总收入 | `B001100000` / `F020101A` |
| `net_income` | 净利润 | `B002000000` / `F030101A` |
| `ocf` | 经营现金流净额 | `C001006000` / `F040101A` |
| `equity_mv` | 股权市值 | 视具体表而定 |

---

## 📡 API 接口速查表

启动后端后，访问 `http://127.0.0.1:8000/docs` 查看交互式 Swagger 文档。

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/api/stats` | 数仓整体概况 |
| `GET` | `/api/tables` | 所有表的 Schema、名称、行数（含 ADS 层） |
| `GET` | `/api/descriptive` | 描述性统计（缩尾、去金融业、剔除新股） |
| `GET` | `/api/industry` | 行业样本分布 |
| `GET` | `/api/yearly` | 逐年观测值数量 |
| `GET` | `/api/distribution` | 指定变量的频率直方图 |
| `GET` | `/api/companies` | 全部公司列表 |
| `GET` | `/api/company/{stkcd}` | 公司年度面板明细 + 杜邦三因子 |
| `GET` | `/api/export/csv` | 导出过滤后的 CSV 回归数据集 |
| `GET` | `/api/export/stata` | 导出过滤后的 Stata .dta 回归数据集 |
| `POST` | `/api/import` | 上传文件导入数仓 |
| `POST` | `/api/resolve` | 公司名称智能消歧（4 级匹配） |
| `POST` | `/api/run-pipeline` | 手动触发 DWD → DWS → ADS 清洗管线 |

---

## 📋 模拟数据说明

内置模拟数据包含 **6 家 A 股上市公司 × 2018-2022 年共 30 条观测**，覆盖财务三大报表：

| 代码 | 名称 | 行业 | 设计特点 |
|------|------|------|----------|
| `000001` | 平安银行 | 金融业 | 曾用名"深发展A"，用于消歧演示 |
| `000002` | 万科A | 房地产业 | 曾用名"深万科A" |
| `600000` | 浦发银行 | 金融业 | 高杠杆低利润率 |
| `600519` | 贵州茅台 | 制造业(C15) | 高毛利高ROE低杠杆 |
| `002594` | 比亚迪 | 制造业(C37) | 高成长高周转 |
| `600028` | 中国石化 | 采矿业(B06) | 重资产中杠杆 |

> 数据由行业画像参数生成（满足 资产=负债+权益 等勾稽关系），仅用于演示流程。正式研究请使用 CSMAR/Wind 真实数据替换。

---

## 🔄 日常使用流程速查

```bash
# ── 第一次安装 ──
cd enterprise-financial-data-warehouse
pip install pandas polars duckdb pyarrow fastapi uvicorn python-multipart
python src/initialize_db.py
python src/etl/mock_data.py
python src/etl/import_raw.py
python src/run_pipeline.py

# ── 每次启动后端 ──
cd enterprise-financial-data-warehouse
uvicorn src.api:app --port 8000
# 然后浏览器打开 frontend/index.html

# ── 导入新数据后 ──
python src/etl/import_raw.py --scan data/raw
python src/run_pipeline.py

# ── 导出回归数据 ──
python src/export.py
```
