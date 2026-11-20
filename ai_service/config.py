"""
config.py — Đọc cấu hình từ file .env (đặt cạnh main.py).

Mọi tham số đều có giá trị mặc định hợp lý; chỉ cần điền BACKEND_URL,
GEMINI_API_KEY và (nếu dùng) CAM_IP / các API key của backend.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass
class Settings:
    # ---------- Backend ----------
    backend_url: str = field(default_factory=lambda: _str("BACKEND_URL", "http://localhost:8006").rstrip("/"))
    people_api_key: str = field(default_factory=lambda: _str("PEOPLE_API_KEY"))
    ai_api_key: str = field(default_factory=lambda: _str("AI_API_KEY"))
    request_timeout: float = field(default_factory=lambda: _float("REQUEST_TIMEOUT_SECONDS", 8))

    # ---------- Nguồn ảnh ----------
    # "esp32"    : lấy trực tiếp http://CAM_IP:CAM_PORT/capture (cùng mạng LAN với CAM)
    # "backend"  : lấy qua backend  BACKEND_URL/api/camera/snapshot (AI ở mạng khác)
    # "webcam:0" : webcam laptop (để test khi chưa có ESP32-CAM)
    # đường dẫn ảnh/video hoặc URL http(s) bất kỳ trả về JPEG
    camera_source: str = field(default_factory=lambda: _str("CAMERA_SOURCE", "esp32"))
    cam_ip: str = field(default_factory=lambda: _str("CAM_IP", "192.168.1.100"))
    cam_port: str = field(default_factory=lambda: _str("CAM_PORT", "80"))

    # ---------- YOLO ----------
    yolo_model: str = field(default_factory=lambda: _str("YOLO_MODEL", "yolo11n.pt"))
    yolo_conf: float = field(default_factory=lambda: _float("YOLO_CONF", 0.40))
    yolo_imgsz: int = field(default_factory=lambda: _int("YOLO_IMGSZ", 640))
    yolo_device: str = field(default_factory=lambda: _str("YOLO_DEVICE"))  # "" = tự chọn, "cpu", "0" (GPU)
    count_interval: float = field(default_factory=lambda: _float("COUNT_INTERVAL_SECONDS", 2))
    smooth_window: int = field(default_factory=lambda: max(1, _int("SMOOTH_WINDOW", 5)))
    people_heartbeat: float = field(default_factory=lambda: _float("PEOPLE_HEARTBEAT_SECONDS", 10))
    debug_frame_path: str = field(default_factory=lambda: _str("DEBUG_FRAME_PATH"))

    # ---------- Gemini ----------
    gemini_api_key: str = field(default_factory=lambda: _str("GEMINI_API_KEY"))
    # Thử lần lượt; model đầu bị lỗi/hết quota thì chuyển sang model sau
    gemini_models: list[str] = field(
        default_factory=lambda: _list("GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-3.8-flash")
    )
    gemini_thinking_level: str = field(default_factory=lambda: _str("GEMINI_THINKING_LEVEL", "low"))
    gemini_timeout: float = field(default_factory=lambda: _float("GEMINI_TIMEOUT_SECONDS", 30))

    # ---------- Vòng quyết định ----------
    decision_check_interval: float = field(default_factory=lambda: _float("DECISION_CHECK_SECONDS", 5))
    llm_min_interval: float = field(default_factory=lambda: _float("LLM_MIN_INTERVAL_SECONDS", 10))
    llm_max_interval: float = field(default_factory=lambda: _float("LLM_MAX_INTERVAL_SECONDS", 120))
    temp_delta: float = field(default_factory=lambda: _float("TEMP_DELTA", 0.5))
    humidity_delta: float = field(default_factory=lambda: _float("HUMIDITY_DELTA", 5))
    people_stale_seconds: float = field(default_factory=lambda: _float("PEOPLE_STALE_SECONDS", 60))
    sensor_stale_seconds: float = field(default_factory=lambda: _float("SENSOR_STALE_SECONDS", 60))

    # ---------- Tham số KỸ THUẬT của chiến lược quạt / điều hòa (rules.py) ----------
    # (Thông số THIẾT BỊ & SỞ THÍCH người dùng — công suất, quạt/AC nào có, nhiệt độ mong muốn —
    #  nằm ở device_profile.py: mặc định → .env → backend context["config"].)
    ac_saving_per_degree: float = field(default_factory=lambda: _float("AC_SAVING_PER_DEGREE", 0.06))
    fan_cooling_effect: list[float] = field(
        default_factory=lambda: [float(x) for x in _list("FAN_COOLING_EFFECT", "1.5,2.5,3.0")][:3]
    )
    humid_rh: float = field(default_factory=lambda: _float("HUMID_RH", 80))
    hysteresis: float = field(default_factory=lambda: _float("HYSTERESIS", 0.5))
    pulldown_delta: float = field(default_factory=lambda: _float("PULLDOWN_DELTA", 2.0))
    ac_off_margin: float = field(default_factory=lambda: _float("AC_OFF_MARGIN", 1.0))
    baseline_ac_temp: int = field(default_factory=lambda: _int("BASELINE_AC_TEMP", 25))
    ac_min_on_seconds: float = field(default_factory=lambda: _float("AC_MIN_ON_SECONDS", 600))
    ac_min_off_seconds: float = field(default_factory=lambda: _float("AC_MIN_OFF_SECONDS", 300))
    empty_ac_grace_seconds: float = field(default_factory=lambda: _float("EMPTY_AC_GRACE_SECONDS", 180))
    natural_temp_ttl_seconds: float = field(default_factory=lambda: _float("NATURAL_TEMP_TTL_SECONDS", 1800))
    decision_log_path: str = field(default_factory=lambda: _str("DECISION_LOG_PATH", "logs/decisions.jsonl"))
    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", False))

    @property
    def esp32_capture_url(self) -> str:
        return f"http://{self.cam_ip}:{self.cam_port}/capture"


settings = Settings()
