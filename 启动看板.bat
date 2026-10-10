@echo off
chcp 65001 >nul
cd /d "%~dp0"
title EFDW 企业财务实证数据仓库

if not exist "data\warehouse\financial_warehouse.db" (
    echo [提示] 未找到数据库，请先依次运行：
    echo   python src/initialize_db.py
    echo   python src/etl/fetch_real_data.py --skip-enrich
    echo   python src/etl/import_raw.py
    echo   python src/run_pipeline.py
    pause
    exit /b 1
)

echo 正在启动服务，看板地址: http://127.0.0.1:8000/
echo 浏览器将自动打开；关闭本窗口即停止服务。
start "" cmd /c "timeout /t 4 /nobreak >nul & start http://127.0.0.1:8000/"
python -m uvicorn src.api:app --port 8000
