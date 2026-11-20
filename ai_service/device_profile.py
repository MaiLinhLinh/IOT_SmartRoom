"""
device_profile.py — Hồ sơ THIẾT BỊ & SỞ THÍCH của phòng (thông tin người dùng sẽ nhập sau này).

Thứ tự ưu tiên (lớp sau ghi đè lớp trước, từng trường riêng lẻ):
    1. Giá trị mặc định trong FIELDS bên dưới   (chạy được ngay, không cần cấu hình)
    2. .env của ai_service                       (nhóm tự chỉnh khi demo)
    3. backend: GET /api/ai/context → "config"  (sau này người dùng nhập trên app Flutter)

Mỗi trường có: kiểu, mặc định, khoảng hợp lệ, đơn vị, nhãn + gợi ý tiếng Việt.
→ Bên backend/Flutter có thể dùng `schema()` (hoặc bảng trong README) để làm form nhập và kiểm tra dữ liệu,
  ai_service không cần sửa code khi thêm chức năng nhập.

Giá trị sai kiểu/ngoài khoảng → bỏ qua trường đó (giữ giá trị lớp trước) và ghi cảnh báo, không làm dừng service.
"""
import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

N_FANS = 3

# name: (kiểu, mặc định, min, max, đơn vị, env, nhãn, gợi ý)
FIELDS: dict[str, tuple] = {
    "fan_power_w": ("float[3]", [45.0, 45.0, 45.0], 5, 300, "W", "FAN_POWER_W",
                    "Công suất điện từng quạt",
                    "Xem nhãn quạt. Quạt đứng/bàn ~35–60 W, quạt trần ~60–80 W. Nhập 1 số = 3 quạt như nhau."),
    "fans_available": ("bool[3]", [True, True, True], None, None, "", "FANS_AVAILABLE",
                       "Quạt nào đang có / dùng được", "Bỏ chọn quạt bị hỏng hoặc phòng không có."),
    "ac_available": ("bool", True, None, None, "", "AC_AVAILABLE",
                     "Phòng có điều hòa", "Tắt nếu phòng không có hoặc điều hòa hỏng."),
    "ac_power_w": ("float", 900.0, 200, 4000, "W", "AC_POWER_W",
                   "Công suất điện điều hòa",
                   "Số W mục 'Input / Công suất tiêu thụ' trên nhãn, KHÔNG phải BTU. "
                   "1 HP ~750–900 W, 1.5 HP ~1100–1300 W, 2 HP ~1600–1900 W."),
    "comfort_max_temp": ("float", 26.0, 23, 29, "°C", "COMFORT_MAX_TEMP",
                         "Nhiệt độ cảm nhận tối đa mong muốn",
                         "Thấp hơn = mát hơn nhưng tốn điện hơn. Khuyến nghị 26°C."),
    "ac_set_min": ("int", 20, 16, 30, "°C", "AC_MIN_TEMP",
                   "Nhiệt độ đặt điều hòa thấp nhất cho phép", "Hệ thống không bao giờ đặt thấp hơn."),
    "ac_set_max": ("int", 28, 16, 32, "°C", "AC_SET_MAX",
                   "Nhiệt độ đặt điều hòa cao nhất", "Đặt cao tiết kiệm điện; 27–28°C là hợp lý khi có quạt."),
}


@dataclass
class DeviceProfile:
    fan_power_w: list = field(default_factory=lambda: list(FIELDS["fan_power_w"][1]))
    fans_available: list = field(default_factory=lambda: list(FIELDS["fans_available"][1]))
    ac_available: bool = FIELDS["ac_available"][1]
    ac_power_w: float = FIELDS["ac_power_w"][1]
    comfort_max_temp: float = FIELDS["comfort_max_temp"][1]
    ac_set_min: int = FIELDS["ac_set_min"][1]
    ac_set_max: int = FIELDS["ac_set_max"][1]
    sources: dict = field(default_factory=lambda: {k: "default" for k in FIELDS})

    # ---------- tiện ích ----------
    @property
    def fans_count(self) -> int:
        return sum(1 for a in self.fans_available if a)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in FIELDS}

    def signature(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    def copy(self) -> "DeviceProfile":
        p = DeviceProfile(**{k: (list(v) if isinstance(v, list) else v) for k, v in self.to_dict().items()})
        p.sources = dict(self.sources)
        return p

    # ---------- nạp / ghi đè ----------
    def apply(self, data: dict, source: str) -> list[str]:
        """Ghi đè các trường hợp lệ trong `data`. Trả về danh sách cảnh báo (trường bị bỏ qua)."""
        warnings = []
        for name, raw in (data or {}).items():
            if name not in FIELDS or raw is None or raw == "":
                continue
            try:
                value = _parse(name, raw)
            except ValueError as exc:
                warnings.append(f"{source}.{name}={raw!r} bỏ qua: {exc}")
                continue
            setattr(self, name, value)
            self.sources[name] = source
        if self.ac_set_min > self.ac_set_max:
            warnings.append(f"ac_set_min {self.ac_set_min} > ac_set_max {self.ac_set_max} → đảo lại")
            self.ac_set_min, self.ac_set_max = self.ac_set_max, self.ac_set_min
        return warnings


def _parse(name: str, raw: Any):
    kind, _, lo, hi, unit, *_ = FIELDS[name]

    def num(x, cast=float):
        v = cast(float(x))
        if lo is not None and not (lo <= v <= hi):
            raise ValueError(f"ngoài khoảng {lo}–{hi} {unit}".strip())
        return v

    def boolean(x):
        if isinstance(x, bool):
            return x
        s = str(x).strip().lower()
        if s in ("1", "true", "yes", "on", "có", "co"):
            return True
        if s in ("0", "false", "no", "off", "không", "khong"):
            return False
        raise ValueError("không phải true/false")

    def as_list(x):
        if isinstance(x, str):
            x = [p for p in x.split(",") if p.strip()]
        if not isinstance(x, (list, tuple)):
            x = [x]
        if len(x) == 1:
            x = list(x) * N_FANS        # 1 giá trị = áp dụng cho cả 3 quạt
        if len(x) != N_FANS:
            raise ValueError(f"cần {N_FANS} giá trị")
        return x

    if kind == "float":
        return num(raw)
    if kind == "int":
        return num(raw, int)
    if kind == "bool":
        return boolean(raw)
    if kind == "float[3]":
        return [num(x) for x in as_list(raw)]
    if kind == "bool[3]":
        return [boolean(x) for x in as_list(raw)]
    raise ValueError(f"kiểu {kind} chưa hỗ trợ")


def from_env(environ: Optional[dict] = None) -> tuple[DeviceProfile, list[str]]:
    """Mặc định + .env (config.py đã load_dotenv)."""
    environ = os.environ if environ is None else environ
    data = {name: environ.get(spec[5]) for name, spec in FIELDS.items() if environ.get(spec[5])}
    profile = DeviceProfile()
    return profile, profile.apply(data, "env")


def schema() -> list[dict]:
    """Mô tả các trường cho backend/Flutter (form nhập + kiểm tra hợp lệ)."""
    out = []
    for name, (kind, default, lo, hi, unit, env, label, hint) in FIELDS.items():
        out.append({"name": name, "type": kind, "default": default, "min": lo, "max": hi,
                    "unit": unit, "env": env, "label": label, "hint": hint})
    return out


if __name__ == "__main__":  # python device_profile.py → in schema JSON để gửi bạn backend/Flutter
    print(json.dumps(schema(), ensure_ascii=False, indent=2))
