import duckdb
import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.config import DB_PATH
from src.etl.import_raw import DataImporter
from src.etl.preflight import run_preflight


def _csv(path):
    path.write_text("stkcd,value\n000001,1\n000001,1\n000002,\n", encoding="utf-8")
    return path


def test_preflight_reports_quality_without_creating_db(tmp_path):
    source = _csv(tmp_path / "input.csv")
    target = tmp_path / "warehouse.db"
    importer = DataImporter(str(target))

    report = run_preflight(importer, str(source), "ods.new_table")

    assert report["ready"] is True
    assert report["rows"] == 3
    assert report["duplicate_rows"] == 1
    assert report["target_exists"] is False
    assert len(report["sha256"]) == 64
    value_col = next(col for col in report["columns"] if col["name"] == "value")
    assert value_col["null_count"] == 1
    assert value_col["null_pct"] == pytest.approx(33.33)
    assert not target.exists()


def test_preflight_conflict_never_changes_existing_table(tmp_path):
    source = _csv(tmp_path / "input.csv")
    target = tmp_path / "warehouse.db"
    conn = duckdb.connect(str(target))
    conn.execute("CREATE SCHEMA ods")
    conn.execute("CREATE TABLE ods.existing AS SELECT 1 AS id")
    conn.close()
    importer = DataImporter(str(target))

    blocked = run_preflight(importer, str(source), "ods.existing")
    allowed = run_preflight(importer, str(source), "ods.existing", overwrite=True)

    assert blocked["ready"] is False
    assert "未允许覆盖" in blocked["errors"][0]
    assert allowed["ready"] is True
    conn = duckdb.connect(str(target), read_only=True)
    assert conn.execute("SELECT COUNT(*) FROM ods.existing").fetchone()[0] == 1
    conn.close()


@pytest.mark.skipif(not DB_PATH.exists(), reason="数据库文件不存在，跳过 API 预检测试")
def test_api_preflight_does_not_import_table():
    table = "ods.pytest_preflight_only"
    conn = duckdb.connect(str(DB_PATH))
    conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.close()
    client = TestClient(app)
    files = {"file": ("preflight.csv", b"stkcd,value\n000001,1\n000001,1\n000002,\n", "text/csv")}

    response = client.post("/api/import/preflight", files=files, data={"table_name": table})

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["report"]["rows"] == 3
    assert data["report"]["duplicate_rows"] == 1
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    assert conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='ods' AND table_name='pytest_preflight_only'"
    ).fetchone()[0] == 0
    conn.close()
