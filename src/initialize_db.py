import os
import duckdb

DB_PATH = "data/warehouse/financial_warehouse.db"


def initialize_warehouse():
    print("=== 开始初始化企业财务实证数据仓库 (EFDW) ===")

    # 1. 创建目录结构
    directories = [
        "data/raw",
        "data/warehouse",
        "src/etl",
        "src/sql"
    ]

    for directory in directories:
        if not os.path.exists(directory):
            os.makedirs(directory)
            print(f"[目录创建] 已创建目录: {directory}")
        else:
            print(f"[目录检测] 目录已存在: {directory}")

    # 创建占位文件 .gitkeep 确保空文件夹被 Git 跟踪
    for directory in ["data/raw", "data/warehouse"]:
        gitkeep_path = os.path.join(directory, ".gitkeep")
        if not os.path.exists(gitkeep_path):
            with open(gitkeep_path, 'w') as f:
                pass

    # 2. 初始化 DuckDB 数据库文件
    print(f"[数据库] 正在连接/创建 DuckDB 数据库: {DB_PATH}")

    conn = duckdb.connect(DB_PATH)

    # 3. 创建 Schema 分层（ODS 贴源层 → DWD 明细层 → DWS 服务层 → ADS 应用层）
    print("[数据库] 正在创建分层 Schema: ods, dwd, dws, ads...")
    conn.execute("CREATE SCHEMA IF NOT EXISTS ods;")
    conn.execute("CREATE SCHEMA IF NOT EXISTS dwd;")
    conn.execute("CREATE SCHEMA IF NOT EXISTS dws;")
    conn.execute("CREATE SCHEMA IF NOT EXISTS ads;")

    # 4. 验证创建结果
    schemas = conn.execute("SELECT schema_name FROM information_schema.schemata;").fetchall()
    print(f"[验证] 当前数据库包含的 Schemas: {[s[0] for s in schemas if not s[0].startswith('pg_')]}")

    conn.close()
    print("=== 初始化成功！你可以开始使用这个数仓了 ===")

if __name__ == "__main__":
    initialize_warehouse()
