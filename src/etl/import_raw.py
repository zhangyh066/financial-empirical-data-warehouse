"""
EFDW 通用数据导入引擎
====================
支持格式：CSV, Excel (.xlsx/.xls), Parquet, JSON/NDJSON, Stata (.dta), SAS (.sas7bdat), SPSS (.sav)
核心特性：
  - 自动识别文件格式
  - 股票代码前导零保护 (Stkcd 等字段自动转 VARCHAR)
  - 通配符批量导入 (如 data/raw/csmar_*.csv)
  - 导入结果日志与数据质量预检
用法：
  # 命令行快捷导入（默认导入 data/raw 下的三张标准表）
  python src/etl/import_raw.py

  # 导入指定文件到指定表
  python src/etl/import_raw.py --file data/raw/my_data.xlsx --table ods.my_table

  # 扫描整个目录，每个文件自动创建一张表
  python src/etl/import_raw.py --scan data/raw

  # Python API 调用
  from src.etl.import_raw import DataImporter
  importer = DataImporter("data/warehouse/financial_warehouse.db")
  importer.import_file("data/raw/csmar_financial.xlsx", "ods.financial_xlsx")
"""

import os
import glob
import argparse
from pathlib import Path
from datetime import datetime

import duckdb


# ─────────────────────────────────────────────
# 常量配置
# ─────────────────────────────────────────────

# 需要强制转为 VARCHAR 的列名（防止前导零丢失）
VARCHAR_FORCE_COLUMNS = {
    "stkcd", "stockcode", "stock_code", "code", "ticker",
    "creditcode", "credit_code", "uscc",
    "indcd", "industry_code",
    "zipcode", "zip_code", "postcode",
}

# 文件扩展名 -> 格式标识
FORMAT_MAP = {
    ".csv":      "csv",
    ".tsv":      "csv",       # TSV 也走 csv 读取，DuckDB 自动检测分隔符
    ".txt":      "csv",
    ".xlsx":     "excel",
    ".xls":      "excel",
    ".parquet":  "parquet",
    ".pq":       "parquet",
    ".json":     "json",
    ".ndjson":   "json",
    ".jsonl":    "json",
    ".dta":      "stata",
    ".sas7bdat": "sas",
    ".sav":      "spss",
}


class DataImporter:
    """通用学术数据导入器"""

    def __init__(self, db_path: str = "data/warehouse/financial_warehouse.db"):
        self.db_path = db_path
        self._import_log = []  # 导入日志

    def table_exists(self, table_name: str) -> bool:
        """检查数据库中是否已存在指定表"""
        conn = duckdb.connect(self.db_path, read_only=True)
        try:
            parts = table_name.split('.')
            if len(parts) == 2:
                schema, table = parts
            else:
                schema, table = 'main', parts[0]
            
            res = conn.execute(f"""
                SELECT COUNT(*) 
                FROM information_schema.tables 
                WHERE table_schema = '{schema}' AND table_name = '{table}'
            """).fetchone()[0]
            return res > 0
        except Exception:
            return False
        finally:
            conn.close()

    def archive_file(self, file_path: str, dest_dir: str = "data/raw/imported") -> str:
        """成功导入后，将文件归档到已导入文件夹"""
        import shutil
        from pathlib import Path
        src = Path(file_path)
        if not src.exists():
            return file_path
        
        # 确保目标文件夹存在
        os.makedirs(dest_dir, exist_ok=True)
        
        # 避免将已经在 imported 目录中的文件再次移动
        if src.parent.resolve() == Path(dest_dir).resolve():
            return file_path
            
        dest = Path(dest_dir) / src.name
        try:
            if dest.exists():
                dest.unlink()
            shutil.move(str(src), str(dest))
            print(f"  [归档] 已将 {src.name} 移动至 {dest_dir}/")
            return str(dest)
        except Exception as e:
            print(f"  [警告] 归档文件 {src.name} 失败: {e}")
            return file_path

    # ─────────────────────────────────────────
    # 核心：单文件导入
    # ─────────────────────────────────────────

    def import_file(
        self,
        file_path: str,
        table_name: str,
        *,
        sheet: str | None = None,
        varchar_columns: list[str] | None = None,
        drop_if_exists: bool = True,
        skip_existing: bool = False,
        archive: bool = False,
    ) -> dict:
        """
        将单个文件导入到 DuckDB 指定表。

        参数:
            file_path:        文件路径 (支持通配符，如 data/raw/csmar_*.csv)
            table_name:       目标表名 (如 "ods.my_table")
            sheet:            Excel 专用 - 指定工作表名称
            varchar_columns:  额外需要强制为 VARCHAR 的列名列表
            drop_if_exists:   若表已存在，是否先删除 (默认 True)
            skip_existing:    若表已存在，是否跳过导入 (默认 False)
            archive:          导入成功后，是否归档到 data/raw/imported 文件夹 (默认 False)

        返回:
            {"table": 表名, "rows": 行数, "columns": 列数, "file": 文件路径, "skipped": 是否跳过}
        """
        file_path = str(file_path)
        fmt = self._detect_format(file_path)

        if fmt is None:
            raise ValueError(f"无法识别文件格式: {file_path}")

        # 若开启跳过且表已存在，则直接返回状态
        if skip_existing and self.table_exists(table_name):
            conn = duckdb.connect(self.db_path, read_only=True)
            try:
                row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                col_info = conn.execute(f"SELECT * FROM {table_name} LIMIT 0").description
                col_count = len(col_info) if col_info else 0
            except Exception:
                row_count, col_count = 0, 0
            finally:
                conn.close()

            result = {
                "table": table_name,
                "rows": row_count,
                "columns": col_count,
                "file": file_path,
                "format": fmt,
                "skipped": True,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._import_log.append(result)
            return result

        conn = duckdb.connect(self.db_path)
        try:
            # 确保 schema 存在
            schema = table_name.split(".")[0] if "." in table_name else None
            if schema:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema};")

            if drop_if_exists:
                conn.execute(f"DROP TABLE IF EXISTS {table_name};")

            # 根据格式选择读取策略
            if fmt == "csv":
                self._import_csv(conn, file_path, table_name, varchar_columns)
            elif fmt == "parquet":
                self._import_parquet(conn, file_path, table_name)
            elif fmt == "excel":
                self._import_excel(conn, file_path, table_name, sheet, varchar_columns)
            elif fmt == "json":
                self._import_json(conn, file_path, table_name)
            elif fmt in ("stata", "sas", "spss"):
                self._import_via_pandas(conn, file_path, table_name, fmt, varchar_columns)
            else:
                raise ValueError(f"暂不支持的格式: {fmt}")

            # 获取导入结果统计
            row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            col_info = conn.execute(f"SELECT * FROM {table_name} LIMIT 0").description
            col_count = len(col_info) if col_info else 0

            # 导入成功后，如果需要，进行文件归档移动
            final_file_path = file_path
            if archive:
                final_file_path = self.archive_file(file_path)

            result = {
                "table": table_name,
                "rows": row_count,
                "columns": col_count,
                "file": final_file_path,
                "format": fmt,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._import_log.append(result)
            return result

        finally:
            conn.close()

    # ─────────────────────────────────────────
    # 批量导入：扫描目录
    # ─────────────────────────────────────────

    def scan_and_import(
        self,
        directory: str,
        schema: str = "ods",
        recursive: bool = False,
        skip_existing: bool = False,
        archive: bool = False,
    ) -> list[dict]:
        """
        扫描目录下所有可识别的数据文件，自动导入到指定 schema。
        表名由文件名自动生成（去除扩展名，特殊字符替换为下划线）。

        参数:
            directory:  要扫描的目录路径
            schema:     目标 schema (默认 "ods")
            recursive:  是否递归扫描子目录
            skip_existing: 是否跳过已存在的表
            archive:    是否在导入成功后将文件移动至 data/raw/imported (默认 False)

        返回:
            导入结果列表
        """
        results = []
        pattern = "**/*" if recursive else "*"

        for file_path in sorted(Path(directory).glob(pattern)):
            if file_path.is_dir():
                continue
            if file_path.suffix.lower() not in FORMAT_MAP:
                continue
            if file_path.name.startswith((".", "~")):  # 跳过隐藏/临时文件
                continue

            table_name = self._path_to_table_name(file_path, schema)
            try:
                result = self.import_file(str(file_path), table_name, skip_existing=skip_existing, archive=archive)
                if result.get("skipped"):
                    print(f"  [SKIP] {file_path.name:40s} -> {table_name:30s} (表已存在，已跳过)")
                else:
                    print(f"  [OK] {file_path.name:40s} -> {table_name:30s} ({result['rows']:,} 行, {result['columns']} 列)")
                results.append(result)
            except Exception as e:
                error_info = {"table": table_name, "file": str(file_path), "error": str(e)}
                print(f"  [ERROR] {file_path.name:40s} -> 导入失败: {e}")
                results.append(error_info)

        return results

    # ─────────────────────────────────────────
    # 批量导入：通配符合并导入
    # ─────────────────────────────────────────

    def import_glob(
        self,
        pattern: str,
        table_name: str,
        varchar_columns: list[str] | None = None,
        skip_existing: bool = False,
    ) -> dict:
        """
        通配符批量导入，将匹配的所有文件合并到同一张表。
        例: importer.import_glob("data/raw/csmar_financial_*.csv", "ods.financial_all")

        参数:
            pattern:          通配符路径 (如 "data/raw/*.csv")
            table_name:       合并后的目标表名
            varchar_columns:  额外强制 VARCHAR 列
            skip_existing:    若表已存在，是否跳过

        返回:
            {"table": 表名, "rows": 总行数, "files": 文件数, "skipped": 是否跳过}
        """
        files = sorted(glob.glob(pattern))
        if not files:
            raise FileNotFoundError(f"没有匹配的文件: {pattern}")

        fmt = self._detect_format(files[0])

        # 若开启跳过且表已存在，则直接返回状态
        if skip_existing and self.table_exists(table_name):
            conn = duckdb.connect(self.db_path, read_only=True)
            try:
                row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            except Exception:
                row_count = 0
            finally:
                conn.close()
            result = {
                "table": table_name,
                "rows": row_count,
                "files": len(files),
                "pattern": pattern,
                "skipped": True,
            }
            self._import_log.append(result)
            return result

        conn = duckdb.connect(self.db_path)
        try:
            schema = table_name.split(".")[0] if "." in table_name else None
            if schema:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema};")
            conn.execute(f"DROP TABLE IF EXISTS {table_name};")

            if fmt == "csv":
                # DuckDB 原生支持 glob CSV
                types_clause = self._build_varchar_types(files[0], varchar_columns)
                conn.execute(f"""
                    CREATE TABLE {table_name} AS
                    SELECT * FROM read_csv_auto('{pattern}'{types_clause});
                """)
            elif fmt == "parquet":
                conn.execute(f"""
                    CREATE TABLE {table_name} AS
                    SELECT * FROM read_parquet('{pattern}');
                """)
            else:
                # 其他格式：逐文件导入后 UNION ALL
                for i, f in enumerate(files):
                    temp = f"__temp_import_{i}"
                    self.import_file(f, temp, varchar_columns=varchar_columns)
                    if i == 0:
                        conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM {temp};")
                    else:
                        conn.execute(f"INSERT INTO {table_name} SELECT * FROM {temp};")
                    conn.execute(f"DROP TABLE IF EXISTS {temp};")

            row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            result = {
                "table": table_name,
                "rows": row_count,
                "files": len(files),
                "pattern": pattern,
            }
            self._import_log.append(result)
            return result

        finally:
            conn.close()

    # ─────────────────────────────────────────
    # 数据预览 & 质量检查
    # ─────────────────────────────────────────

    def preview(self, table_name: str, limit: int = 5) -> None:
        """打印表的前 N 行和列信息"""
        conn = duckdb.connect(self.db_path, read_only=True)
        try:
            print(f"\n{'='*60}")
            print(f"[表预览] {table_name}")
            print(f"{'='*60}")

            # 列信息
            cols = conn.execute(f"""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema || '.' || table_name = '{table_name}'
                ORDER BY ordinal_position
            """).fetchall()

            if cols:
                print(f"\n[列结构] ({len(cols)} 列):")
                for name, dtype in cols:
                    print(f"   {name:30s}  {dtype}")

            # 行数
            count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            print(f"\n[总行数]: {count:,}")

            # 数据预览
            print(f"\n[前 {limit} 行预览]:")
            df = conn.execute(f"SELECT * FROM {table_name} LIMIT {limit}").fetchdf()
            print(df.to_string(index=False))

        finally:
            conn.close()

    def check_quality(self, table_name: str) -> dict:
        """数据质量预检：空值率、重复行、类型分布"""
        conn = duckdb.connect(self.db_path, read_only=True)
        try:
            df = conn.execute(f"SELECT * FROM {table_name}").fetchdf()
            total_rows = len(df)
            duplicates = df.duplicated().sum()

            null_stats = {}
            for col in df.columns:
                null_count = df[col].isnull().sum()
                null_stats[col] = {
                    "null_count": int(null_count),
                    "null_pct": round(null_count / total_rows * 100, 2) if total_rows > 0 else 0,
                    "dtype": str(df[col].dtype),
                }

            result = {
                "table": table_name,
                "total_rows": total_rows,
                "duplicate_rows": int(duplicates),
                "columns": null_stats,
            }

            # 打印摘要
            print(f"\n{'='*60}")
            print(f"[数据质量] 报告: {table_name}")
            print(f"{'='*60}")
            print(f"   总行数: {total_rows:,}")
            print(f"   重复行: {duplicates:,}")
            print(f"\n   {'列名':30s}  {'类型':12s}  {'空值数':>8s}  {'空值率':>8s}")
            print(f"   {'-'*30}  {'-'*12}  {'-'*8}  {'-'*8}")
            for col, info in null_stats.items():
                print(f"   {col:30s}  {info['dtype']:12s}  {info['null_count']:>8,}  {info['null_pct']:>7.1f}%")

            return result

        finally:
            conn.close()

    def print_log(self) -> None:
        """打印本次会话的所有导入日志"""
        if not self._import_log:
            print("[日志] 本次会话暂无导入记录。")
            return

        print(f"\n{'='*70}")
        print(f"[导入日志] (共 {len(self._import_log)} 条)")
        print(f"{'='*70}")
        for i, entry in enumerate(self._import_log, 1):
            if "error" in entry:
                print(f"  {i}. [FAIL] {entry['file']} -> {entry['table']}  错误: {entry['error']}")
            elif entry.get("skipped"):
                print(f"  {i}. [SKIP] {entry.get('file', entry.get('pattern', '?')):40s}"
                      f" -> {entry['table']:25s}  (已跳过，表已存在)")
            else:
                print(f"  {i}. [OK] {entry.get('file', entry.get('pattern', '?')):40s}"
                      f" -> {entry['table']:25s}  {entry['rows']:>8,} 行")

    # ─────────────────────────────────────────
    # 内部：各格式导入实现
    # ─────────────────────────────────────────

    def _import_csv(self, conn, file_path, table_name, varchar_columns=None):
        types_clause = self._build_varchar_types(file_path, varchar_columns)
        conn.execute(f"""
            CREATE TABLE {table_name} AS
            SELECT * FROM read_csv_auto('{file_path}'{types_clause});
        """)

    def _import_parquet(self, conn, file_path, table_name):
        conn.execute(f"""
            CREATE TABLE {table_name} AS
            SELECT * FROM read_parquet('{file_path}');
        """)

    def _import_json(self, conn, file_path, table_name):
        conn.execute(f"""
            CREATE TABLE {table_name} AS
            SELECT * FROM read_json_auto('{file_path}');
        """)

    def _import_excel(self, conn, file_path, table_name, sheet=None, varchar_columns=None):
        """通过 Pandas 中转导入 Excel"""
        import pandas as pd

        kwargs = {}
        if sheet:
            kwargs["sheet_name"] = sheet

        # 构建需要强制为 str 的列
        str_cols = self._get_varchar_col_set(varchar_columns)
        # 先读取表头来匹配
        header = pd.read_excel(file_path, nrows=0, **kwargs)
        converters = {col: str for col in header.columns if col.lower().strip() in str_cols}
        if converters:
            kwargs["converters"] = converters

        df = pd.read_excel(file_path, **kwargs)
        conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM df;")

    def _import_via_pandas(self, conn, file_path, table_name, fmt, varchar_columns=None):
        """通过 Pandas 导入 Stata/SAS/SPSS"""
        import pandas as pd

        if fmt == "stata":
            df = pd.read_stata(file_path)
        elif fmt == "sas":
            df = pd.read_sas(file_path)
        elif fmt == "spss":
            df = pd.read_spss(file_path)
        else:
            raise ValueError(f"未知格式: {fmt}")

        # 强制 VARCHAR 列
        str_cols = self._get_varchar_col_set(varchar_columns)
        for col in df.columns:
            if col.lower().strip() in str_cols:
                df[col] = df[col].astype(str)

        conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM df;")

    # ─────────────────────────────────────────
    # 内部：工具函数
    # ─────────────────────────────────────────

    def _detect_format(self, file_path: str) -> str | None:
        """根据扩展名检测文件格式"""
        ext = Path(file_path.replace("*", "placeholder")).suffix.lower()
        return FORMAT_MAP.get(ext)

    def _get_varchar_col_set(self, extra: list[str] | None = None) -> set[str]:
        """合并内置和用户自定义的 VARCHAR 强制列"""
        result = set(VARCHAR_FORCE_COLUMNS)
        if extra:
            result.update(c.lower().strip() for c in extra)
        return result

    def _build_varchar_types(self, file_path: str, varchar_columns: list[str] | None = None) -> str:
        """
        扫描 CSV 文件表头，找出需要强制 VARCHAR 的列，
        生成 DuckDB read_csv_auto 的 types 参数。
        """
        str_cols = self._get_varchar_col_set(varchar_columns)

        # 读取第一行获取列名
        try:
            sample_path = file_path
            if "*" in file_path:
                # 通配符场景，取第一个匹配的文件
                matches = glob.glob(file_path)
                if matches:
                    sample_path = matches[0]
                else:
                    return ""

            with open(sample_path, "r", encoding="utf-8") as f:
                header_line = f.readline().strip()

            # 简单解析 CSV 表头
            sep = "\t" if "\t" in header_line else ","
            headers = [h.strip().strip('"').strip("'") for h in header_line.split(sep)]

            forced = {h: "VARCHAR" for h in headers if h.lower().strip() in str_cols}
            if forced:
                types_str = ", ".join(f"'{k}': '{v}'" for k, v in forced.items())
                return f", types={{{types_str}}}"
        except Exception:
            pass

        return ""

    def _path_to_table_name(self, file_path: Path, schema: str) -> str:
        """将文件路径转为合法的表名"""
        name = file_path.stem.lower()
        # 替换特殊字符为下划线
        safe_name = ""
        for ch in name:
            if ch.isalnum() or ch == "_":
                safe_name += ch
            else:
                safe_name += "_"
        # 去除连续下划线和首尾下划线
        while "__" in safe_name:
            safe_name = safe_name.replace("__", "_")
        safe_name = safe_name.strip("_")
        return f"{schema}.{safe_name}"


# ─────────────────────────────────────────────
# 默认导入流程（兼容原有行为）
# ─────────────────────────────────────────────

def import_default_tables(skip_existing: bool = False, archive: bool = True):
    """导入项目预设的标准 ODS 表，并自动处理分类归档"""
    db_path = "data/warehouse/financial_warehouse.db"

    if not os.path.exists(db_path):
        print(f"[错误] 数据库文件 {db_path} 不存在，请先运行 python src/initialize_db.py")
        return

    # 自动创建待导入 (pending) 和已导入 (imported) 目录结构
    pending_dir = "data/raw/pending"
    imported_dir = "data/raw/imported"
    os.makedirs(pending_dir, exist_ok=True)
    os.makedirs(imported_dir, exist_ok=True)

    print("=" * 60)
    print("  EFDW 数据导入引擎")
    print(f"  模式: 标准三表导入 (ODS 贴源层) | 跳过已存在: {skip_existing} | 自动分类归档: {archive}")
    print("=" * 60)

    importer = DataImporter(db_path)

    # 预设的三张标准表（基本文件名）：公司信息、财务三表、曾用名历史
    default_tables = [
        ("csmar_company_info_raw.csv",  "ods.company_info"),
        ("csmar_financial_raw.csv",     "ods.financial_raw"),
        ("csmar_company_names_raw.csv", "ods.company_names_raw"),
    ]

    for filename, table_name in default_tables:
        # 文件寻址优先级：
        # 1. data/raw/pending/<filename> （新增的分类待导入目录）
        # 2. data/raw/<filename> （原本的根目录，保证向后兼容）
        # 3. data/raw/imported/<filename> （已归档目录，用于重刷数据）
        pending_path = os.path.join(pending_dir, filename)
        root_path = os.path.join("data/raw", filename)
        imported_path = os.path.join(imported_dir, filename)

        resolved_path = None
        if os.path.exists(pending_path):
            resolved_path = pending_path
        elif os.path.exists(root_path):
            resolved_path = root_path
        elif os.path.exists(imported_path):
            resolved_path = imported_path

        if not resolved_path:
            print(f"  [跳过] {filename} (在 data/raw/、data/raw/pending/ 或 data/raw/imported/ 中均未找到)")
            continue

        try:
            # 只有当文件不在 imported 目录中时，才需要执行归档移动
            should_archive = archive and (resolved_path != imported_path)
            
            result = importer.import_file(
                resolved_path, 
                table_name, 
                skip_existing=skip_existing, 
                archive=should_archive
            )
            if result.get("skipped"):
                print(f"  [SKIP] {filename:45s} -> {table_name:25s} (表已存在，已跳过)")
            else:
                print(f"  [OK] {filename:45s} -> {table_name:25s} ({result['rows']:,} 行, {result['columns']} 列)")
        except Exception as e:
            print(f"  [FAIL] {filename:45s} -> 导入失败: {e}")

    # 打印导入日志
    importer.print_log()
    print("\n" + "=" * 60)
    print("  ODS 层导入完成！")
    print("=" * 60)


# ─────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="EFDW 通用数据导入引擎",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 默认导入（标准 ODS 表，检查 data/raw 及其子目录，成功后自动归档到 data/raw/imported）
  python src/etl/import_raw.py

  # 默认导入，并跳过已经在数据库中存在的表
  python src/etl/import_raw.py --skip

  # 导入指定文件到指定表
  python src/etl/import_raw.py --file data/raw/csmar_governance.xlsx --table ods.governance

  # 导入 Excel 指定工作表
  python src/etl/import_raw.py --file data/raw/wind_data.xlsx --table ods.wind --sheet Sheet2

  # 扫描目录，每个文件自动建表，且跳过已存在的表
  python src/etl/import_raw.py --scan data/raw --skip

  # 通配符合并导入
  python src/etl/import_raw.py --glob "data/raw/csmar_financial_*.csv" --table ods.financial_all
        """,
    )

    parser.add_argument("--file", type=str, help="要导入的文件路径")
    parser.add_argument("--table", type=str, help="目标表名 (如 ods.my_table)")
    parser.add_argument("--sheet", type=str, help="Excel 工作表名称 (仅 Excel 格式)")
    parser.add_argument("--scan", type=str, help="扫描目录并自动导入所有可识别文件")
    parser.add_argument("--glob", type=str, help="通配符路径，合并导入到一张表 (需配合 --table)")
    parser.add_argument("--schema", type=str, default="ods", help="扫描导入的目标 schema (默认 ods)")
    parser.add_argument("--varchar", type=str, help="额外需要强制 VARCHAR 的列名 (逗号分隔)")
    parser.add_argument("--preview", action="store_true", help="导入后预览表数据")
    parser.add_argument("--check", action="store_true", help="导入后进行数据质量检查")
    parser.add_argument("--recursive", action="store_true", help="扫描时递归搜索子目录")
    parser.add_argument("--skip", action="store_true", help="若表已存在，则跳过导入")
    parser.add_argument("--archive", action="store_true", help="导入成功后，将原始文件移动到 data/raw/imported 归档")

    args = parser.parse_args()

    db_path = "data/warehouse/financial_warehouse.db"
    if not os.path.exists(db_path):
        print(f"[错误] 数据库文件 {db_path} 不存在，请先运行 python src/initialize_db.py")
        return

    importer = DataImporter(db_path)
    extra_varchar = [v.strip() for v in args.varchar.split(",")] if args.varchar else None

    # 模式 1: 扫描目录
    if args.scan:
        print(f"[SCAN] 扫描目录: {args.scan} (schema={args.schema}) | 跳过已存在: {args.skip} | 自动归档: {args.archive}")
        importer.scan_and_import(args.scan, schema=args.schema, recursive=args.recursive, skip_existing=args.skip, archive=args.archive)
        importer.print_log()
        return

    # 模式 2: 通配符合并导入
    if args.glob:
        if not args.table:
            print("[ERROR] 通配符导入必须指定 --table 参数")
            return
        print(f"[GLOB] 通配符导入: {args.glob} -> {args.table} | 跳过已存在: {args.skip}")
        result = importer.import_glob(args.glob, args.table, varchar_columns=extra_varchar, skip_existing=args.skip)
        if result.get("skipped"):
            print(f"  [SKIP] 合并 {result['files']} 个文件 -> {result['table']} (表已存在，已跳过)")
        else:
            print(f"  [OK] 合并 {result['files']} 个文件 -> {result['table']} ({result['rows']:,} 行)")
        return

    # 模式 3: 单文件导入
    if args.file:
        if not args.table:
            # 自动生成表名
            args.table = importer._path_to_table_name(Path(args.file), "ods")
            print(f"  [INFO] 未指定 --table，自动命名为: {args.table}")

        result = importer.import_file(
            args.file, args.table,
            sheet=args.sheet,
            varchar_columns=extra_varchar,
            skip_existing=args.skip,
            archive=args.archive,
        )
        if result.get("skipped"):
            print(f"  [SKIP] {result['file']} -> {result['table']} (表已存在，已跳过)")
        else:
            print(f"  [OK] {result['file']} -> {result['table']} ({result['rows']:,} 行, {result['columns']} 列)")

        if args.preview and not result.get("skipped"):
            importer.preview(args.table)
        if args.check and not result.get("skipped"):
            importer.check_quality(args.table)
        return

    # 默认模式: 导入三张标准表（默认开启分类归档）
    import_default_tables(skip_existing=args.skip, archive=True)


if __name__ == "__main__":
    main()
