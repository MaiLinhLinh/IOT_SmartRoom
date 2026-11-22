"""
schemas.py — Pydantic schemas v5 (contract cuối cho Flutter)
v4: 1 quạt 3 mức (fan_level) → 3 quạt độc lập bật/tắt (fan1, fan2, fan3)
v5: thêm schemas cấu hình thiết bị (hãng/công suất) + điện năng theo ngày
"""
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from datetime import datetime, date


# ========== AUTH & USER ==========

class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    full_name: Optional[str] = None
    email: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(BaseModel):
    """Thông tin user trả về (Trang 5 Profile + login response)"""
    id:         int
    username:   str
    full_name:  Optional[str] = None
    email:      Optional[str] = None
    role:       str
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    email:     Optional[str] = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6)


class StreamTokenOut(BaseModel):
    """Token ngắn hạn để WebView mở /api/camera/stream?token=..."""
    stream_token: str
    token_type: str = "bearer"
    expires_in: int
    stream_path: str = "/api/camera/stream"


# ========== SENSOR ==========

class SensorDataIn(BaseModel):
    """ESP32 gửi lên POST /api/sensor"""
    temperature: float
    humidity:    float


class SensorDataOut(BaseModel):
    """GET /api/sensor/latest"""
    id:           int
    timestamp:    datetime
    temperature:  Optional[float]
    humidity:     Optional[float]
    people_count: int
    fan1:         bool
    fan2:         bool
    fan3:         bool
    ac_on:        bool
    ac_temp:      int
    mode:         str

    class Config:
        from_attributes = True


# ========== DEVICE STATE ==========

class DeviceStateOut(BaseModel):
    """GET /api/device/state — Dashboard đọc toàn bộ trạng thái."""
    fan1:              bool
    fan2:              bool
    fan3:              bool
    ac_on:             bool
    ac_temp:           int
    mode:              str
    updated_at:        Optional[datetime]
    ai_reason:         Optional[str] = None
    people_count:      int = 0
    people_source:     str = "NONE"
    people_updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DeviceCommandIn(BaseModel):
    """
    POST /api/device/command — Flutter gửi lệnh thiết bị khi đang MANUAL.
    Chế độ phải được đổi qua PATCH /api/device/mode; body không nhận `mode`.
    """
    model_config = ConfigDict(extra="forbid")

    fan1:      bool
    fan2:      bool
    fan3:      bool
    ac_on:     bool
    ac_temp:   int  = Field(default=26, ge=16, le=32)


class ModeUpdateIn(BaseModel):
    """PATCH /api/device/mode — chỉ đổi AUTO/MANUAL."""
    mode: str = Field(..., pattern="^(MANUAL|AUTO)$")


# ========== AI BÊN NGOÀI (service riêng) ==========

class AIDecisionIn(BaseModel):
    """
    POST /api/ai/command — AI service bên ngoài gửi quyết định.
    Không cần JWT, dùng X-API-Key nếu .env có AI_API_KEY.
    """
    fan_level: int  = Field(..., ge=0, le=3)
    ac_on:     bool
    ac_temp:   int  = Field(default=26, ge=16, le=32)
    ai_reason: str  = Field(..., min_length=1, max_length=500)


# ========== DEVICE INFO (hãng + công suất thiết bị) ==========

class DeviceInfoOut(BaseModel):
    """GET /api/devices/info — cấu hình 1 thiết bị."""
    device_key:      str                        # fan1 | fan2 | fan3 | ac
    device_name:     str
    brand:           Optional[str] = None       # tên hãng (None = chưa nhập)
    power_watts:     Optional[float] = None     # None = đang dùng giá trị mặc định
    power_is_default: bool = False              # true → power_watts là mặc định hệ thống
    updated_at:      Optional[datetime] = None

    class Config:
        from_attributes = True


class DeviceInfoUpdateIn(BaseModel):
    """
    PUT /api/devices/info/{device_key} — sửa hãng/công suất.
    Bỏ qua field nào thì giữ nguyên field đó; gửi power_watts=null để về mặc định.
    """
    model_config = ConfigDict(extra="forbid")

    brand:       Optional[str] = Field(None, max_length=100)
    power_watts: Optional[float] = Field(None, gt=0, le=5000)


# ========== ENERGY (điện năng theo ngày) ==========

class EnergyDayOut(BaseModel):
    """GET /api/energy/daily — điện năng 1 ngày, theo từng thiết bị (Wh)."""
    date:     date
    fan1_wh:  float
    fan2_wh:  float
    fan3_wh:  float
    ac_wh:    float
    total_wh: float

    class Config:
        from_attributes = True


class EnergySummaryOut(BaseModel):
    """GET /api/energy/summary — số liệu nhanh cho widget Dashboard."""
    today_wh:        float                    # tổng hôm nay (Wh)
    fan1_wh:         float
    fan2_wh:         float
    fan3_wh:         float
    ac_wh:           float
    avg_7d_wh:       float                    # trung bình 7 ngày gần nhất (Wh/ngày)
    top_device:      Optional[str] = None     # device_key tốn điện nhất hôm nay
    days_recorded:   int                      # số ngày đã có dữ liệu


# ========== PEOPLE (YOLO service đẩy số người lên) ==========

class PeopleCountIn(BaseModel):
    count:  int = Field(..., ge=0, le=1000)
    source: str = Field(default="YOLO", pattern="^(YOLO|MANUAL|AI|SENSOR)$")


class PeopleLatestOut(BaseModel):
    people_count:      int
    people_source:     str
    people_updated_at: Optional[datetime] = None


# ========== CONTROL LOGS ==========

class ControlLogOut(BaseModel):
    """Trang 6 — Lịch sử thao tác"""
    id:             int
    timestamp:      datetime
    actor:          str
    actor_type:     str
    actor_user_id:  Optional[int] = None
    source:         str
    description:    str
    fan1:           bool
    fan2:           bool
    fan3:           bool
    ac_on:          bool
    ac_temp:        int
    mode:           str
    fan1_prev:      Optional[bool]
    fan2_prev:      Optional[bool]
    fan3_prev:      Optional[bool]
    ac_on_prev:     Optional[bool]
    ac_temp_prev:   Optional[int]
    ai_reason:      Optional[str]
    temperature:    Optional[float] = None
    humidity:       Optional[float] = None
    people_count:   Optional[int] = None

    class Config:
        from_attributes = True


class ControlLogListOut(BaseModel):
    total: int
    page:  int
    size:  int
    items: list[ControlLogOut]


# ========== SENSOR LOGS ==========

class LogOut(BaseModel):
    id:           int
    timestamp:    datetime
    temperature:  Optional[float]
    humidity:     Optional[float]
    people_count: int
    fan1:         bool
    fan2:         bool
    fan3:         bool
    ac_on:        bool
    ac_temp:      int
    mode:         str

    class Config:
        from_attributes = True


class LogListOut(BaseModel):
    total: int
    page:  int
    size:  int
    items: list[LogOut]


TokenResponse.model_rebuild()
