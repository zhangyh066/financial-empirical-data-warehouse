@echo off
chcp 65001 >nul
cd /d "%~dp0"
title EFDW 企业财务实证数据仓库

REM ── 依赖自检：缺少关键依赖时自动安装 requirements.txt ──
python -c "import pandas, duckdb, pyarrow, fastapi, uvicorn, multipart, openpyxl" >nul 2>&1
if errorlevel 1 (
    echo [提示] 检测到缺少 Python 依赖，正在自动安装（pip install -r requirements.txt）...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后手动运行: pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo [完成] 依赖安装完成，正在继续启动...
)

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
echo 浏览器将在服务就绪后自动打开（每 2 秒检测一次，最长等待 120 秒）；关闭本窗口即停止服务。
start "" powershell -WindowStyle Hidden -Command "$ok=$false; for($i=0; $i -lt 60; $i++){ try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/stats' -UseBasicParsing -TimeoutSec 5; if($r.StatusCode -eq 200){ $ok=$true; break } } catch {}; Start-Sleep -Seconds 2 }; if($ok){ Start-Process 'http://127.0.0.1:8000/' }"
python -m uvicorn src.api:app --port 8000
