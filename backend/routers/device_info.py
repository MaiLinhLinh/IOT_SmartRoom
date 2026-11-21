"""
routers/device_info.py — Cấu hình thiết bị (hãng + công suất).
GET /api/devices/info            ← Flutter hiển thị form cấu hình
PUT  /api/devices/info/{key} 🔒  ← Flutter lưu hãng/công suất người dùng nhập

Công suất là đầu vào cho việc tính điện năng (services/energy_service.py)
và là dữ liệu để AI cân giữa "lạnh đủ dùng" và "tiết kiệm điện" sau này.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import DeviceInfo
from routers.auth import get_current_user
from schemas import DeviceInfoOut, DeviceInfoUpdateIn, UserOut
from services.energy_service import DEVICE_KEYS, DEFAULT_POWER_WATTS, seed_device_info

router = APIRouter(prefix="/api/devices", tags=["Device Info"])


@router.get("/info", response_model=list[DeviceInfoOut],
            summary="Danh sách cấu hình 4 thiết bị")
def list_device_info(db: Session = Depends(get_db)):
    """
    Trả về fan1, fan2, fan3, ac kèm brand + power_watts.
    `power_is_default = true` nghĩa là công suất đang là giá trị mặc định
    hệ thống (người dùng chưa nhập số riêng).
    """
    seed_device_info(db)

    result = []
    for info in db.query(DeviceInfo).order_by(DeviceInfo.device_key).all():
        watts = info.power_watts
        result.append(DeviceInfoOut(
            device_key=info.device_key,
            device_name=info.device_name,
            brand=info.brand,
            power_watts=watts if watts is not None else DEFAULT_POWER_WATTS.get(info.device_key),
            power_is_default=watts is None,
            updated_at=info.updated_at,
        ))
    return result


@router.put("/info/{device_key}", response_model=DeviceInfoOut,
            summary="Cập nhật hãng/công suất 1 thiết bị")
def update_device_info(
    device_key: str,
    body:       DeviceInfoUpdateIn,
    db:         Session  = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    """
    Chỉ sửa các field có trong body (bỏ qua = giữ nguyên).
    Gửi `"power_watts": null` để quay về công suất mặc định hệ thống.
    """
    if device_key not in DEVICE_KEYS:
        raise HTTPException(
            status_code=404,
            detail=f"device_key phải là một trong: {', '.join(DEVICE_KEYS)}",
        )

    seed_device_info(db)
    info = db.get(DeviceInfo, device_key)

    # model_fields_set phân biệt "không gửi field" với "gửi field = null"
    provided = body.model_fields_set
    if "brand" in provided:
        brand = (body.brand or "").strip() or None
        info.brand = brand
    if "power_watts" in provided:
        info.power_watts = body.power_watts

    info.updated_at = datetime.now()
    db.commit()
    db.refresh(info)

    watts = info.power_watts
    return DeviceInfoOut(
        device_key=info.device_key,
        device_name=info.device_name,
        brand=info.brand,
        power_watts=watts if watts is not None else DEFAULT_POWER_WATTS.get(info.device_key),
        power_is_default=watts is None,
        updated_at=info.updated_at,
    )
