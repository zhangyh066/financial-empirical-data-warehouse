"""初始化数据库文件与目录结构（路径统一由 src/config.py 管理，与运行目录无关）"""
import sys
from pathlib import Path

import duckdb

# 兼容 `python src/initialize_db.py` 直接运行：把项目根目录加入模块搜索路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import (
    DB_PATH, IMPORTED_DIR, LOG_DIR, PENDING_DIR, PROJECT_ROOT,
    RAW_DIR, SQL_DIR, TMP_DIR, WAREHOUSE_DIR,
)


def initialize_warehouse():
    print("=== 开始初始化企业财务实证数据仓库 (EFDW) ===")

    # 1. 创建目录结构（源码目录仅在缺失时兜底创建）
    directories = [
        RAW_DIR, WAREHOUSE_DIR, PENDING_DIR, IMPORTED_DIR, TMP_DIR, LOG_DIR,
        PROJECT_ROOT / "src" / "etl", SQL_DIR,
    ]

    for directory in directories:
        if not directory.exists():
            directory.mkdir(parents=True)
            print(f"[目录创建] 已创建目录: {directory}")
        else:
            print(f"[目录检测] 目录已存在: {directory}")

    # 创建占位文件 .gitkeep 确保空文件夹被 Git 跟踪
    for directory in (RAW_DIR, WAREHOUSE_DIR):
        gitkeep_path = directory / ".gitkeep"
        if not gitkeep_path.exists():
            gitkeep_path.touch()

    # 2. 初始化 DuckDB 数据库文件
    print(f"[数据库] 正在连接/创建 DuckDB 数据库: {DB_PATH}")

    conn = duckdb.connect(str(DB_PATH))

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
