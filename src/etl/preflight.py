"""导入前只读预检：在独立内存 DuckDB 中解析文件，不写入正式数仓。"""

import hashlib
from datetime import datetime
from pathlib import Path

import duckdb

from src.config import is_valid_table_name

_TEMP_TABLE = "main.preflight_input"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def run_preflight(
    importer,
    file_path: str,
    table_name: str,
    *,
    sheet: str | None = None,
    varchar_columns: list[str] | None = None,
    overwrite: bool = False,
) -> dict:
    """解析输入并返回质量报告；异常输入也以 ready=false 返回，便于前端展示。"""
    if not is_valid_table_name(table_name):
        raise ValueError(f"非法表名: {table_name!r}")

    path = Path(file_path)
    report = {
        "ready": False,
        "file": str(path),
        "target_table": table_name,
        "target_exists": False,
        "overwrite": overwrite,
        "format": None,
        "rows": 0,
        "duplicate_rows": 0,
        "columns": [],
        "errors": [],
        "warnings": [],
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    errors = report["errors"]
    warnings = report["warnings"]

    if not path.is_file():
        errors.append(f"文件不存在: {path}")
        return report

    fmt = importer._detect_format(str(path))
    report["format"] = fmt
    if fmt is None:
        errors.append(f"无法识别文件格式: {path.suffix or '无扩展名'}")
        return report

    report["size_bytes"] = path.stat().st_size
    report["sha256"] = _file_sha256(path)
    report["target_exists"] = (
        importer.table_exists(table_name) if Path(importer.db_path).exists() else False
    )
    if report["target_exists"]:
        if overwrite:
            warnings.append("目标表已存在；正式导入时将按 overwrite=true 覆盖")
        else:
            errors.append("目标表已存在且未允许覆盖；预检不会修改该表")

    conn = duckdb.connect(":memory:")
    try:
        if fmt == "csv":
            importer._import_csv(conn, str(path), _TEMP_TABLE, varchar_columns)
        elif fmt == "parquet":
            importer._import_parquet(conn, str(path), _TEMP_TABLE)
        elif fmt == "excel":
            importer._import_excel(conn, str(path), _TEMP_TABLE, sheet, varchar_columns)
        elif fmt == "json":
            importer._import_json(conn, str(path), _TEMP_TABLE)
        elif fmt in ("stata", "sas", "spss"):
            importer._import_via_pandas(conn, str(path), _TEMP_TABLE, fmt, varchar_columns)
        else:
            raise ValueError(f"暂不支持的格式: {fmt}")

        columns = conn.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'main' AND table_name = 'preflight_input'
            ORDER BY ordinal_position
        """).fetchall()
        names = [row[0] for row in columns]
        null_select = ", ".join(
            f"(COUNT(*) - COUNT({_quote(name)})) AS null_{index}"
            for index, name in enumerate(names)
        )
        stats = conn.execute(
            f"SELECT COUNT(*){', ' + null_select if null_select else ''} FROM {_TEMP_TABLE}"
        ).fetchone()
        row_count = int(stats[0])
        duplicate_rows = 0
        group_columns = ", ".join(_quote(name) for name in names)
        if group_columns:
            duplicate_rows = int(conn.execute(f"""
                SELECT COALESCE(SUM(n - 1), 0)
                FROM (
                    SELECT COUNT(*) AS n
                    FROM {_TEMP_TABLE}
                    GROUP BY {group_columns}
                    HAVING COUNT(*) > 1
                )
            """).fetchone()[0])

        report["rows"] = row_count
        report["duplicate_rows"] = duplicate_rows
        report["columns"] = [
            {
                "name": name,
                "dtype": columns[index][1],
                "null_count": int(stats[index + 1]),
                "null_pct": round(int(stats[index + 1]) / row_count * 100, 2) if row_count else 0.0,
            }
            for index, name in enumerate(names)
        ]

        if row_count == 0:
            errors.append("文件没有数据行")
        if duplicate_rows:
            warnings.append(f"发现 {duplicate_rows} 条完全重复的数据行")
        for column in report["columns"]:
            if column["null_pct"] >= 20:
                warnings.append(f"字段 {column['name']} 空值率 {column['null_pct']}%")
    except Exception as exc:
        errors.append(f"解析失败: {exc}")
    finally:
        conn.close()

    report["ready"] = not errors
    return report
