"""
routers/energy.py — Điện năng tiêu thụ theo ngày (feed biểu đồ cột Flutter).
GET /api/energy/daily?days=7  ← biểu đồ cột: mỗi ngày bao nhiêu Wh, từng thiết bị
GET /api/energy/summary       ← widget tổng quan Dashboard
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from schemas import EnergyDayOut, EnergySummaryOut
from services.energy_service import get_daily, get_summary

router = APIRouter(prefix="/api/energy", tags=["Energy"])


@router.get("/daily", response_model=list[EnergyDayOut],
            summary="Điện năng theo ngày, từng thiết bị (Wh)")
def read_daily(
    days: int = Query(7, ge=1, le=30, description="Số ngày gần nhất (1–30)"),
    db:   Session = Depends(get_db),
):
    """
    Trả về đúng `days` phần tử liên tục theo ngày (ngày chưa có dữ liệu → 0 Wh)
    để frontend vẽ biểu đồ cột không bị khuyết.
    Đơn vị Wh; ví dụ quạt 45W chạy 4h ≈ 180 Wh = 0.18 kWh.
    """
    return get_daily(db, days)


@router.get("/summary", response_model=EnergySummaryOut,
            summary="Số liệu nhanh: hôm nay + trung bình 7 ngày")
def read_summary(db: Session = Depends(get_db)):
    return get_summary(db)
