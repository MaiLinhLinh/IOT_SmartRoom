# ============================================================
# setup.ps1 — Cài đặt và chạy AIoT Backend lần đầu
# Chạy: .\setup.ps1
# ============================================================

Write-Host "=== AIoT Backend Setup ===" -ForegroundColor Cyan

# 1. Tạo virtual environment
if (-not (Test-Path "venv")) {
    Write-Host "[1/4] Tao virtual environment..." -ForegroundColor Yellow
    python -m venv venv
} else {
    Write-Host "[1/4] Virtual environment da ton tai" -ForegroundColor Green
}

# 2. Activate venv
Write-Host "[2/4] Activate venv..." -ForegroundColor Yellow
& "venv\Scripts\Activate.ps1"

# 3. Cài dependencies
Write-Host "[3/4] Cai dependencies..." -ForegroundColor Yellow
pip install -r requirements.txt

# 4. Khởi chạy server
Write-Host "[4/4] Khoi chay FastAPI server..." -ForegroundColor Yellow
Write-Host ""
Write-Host ">>> Truy cap Swagger UI: http://localhost:8006/docs" -ForegroundColor Green
Write-Host ">>> Dung Ctrl+C de dung server" -ForegroundColor Green
Write-Host ""
uvicorn main:app --host 0.0.0.0 --port 8006 --reload
