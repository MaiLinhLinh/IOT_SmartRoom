"""
rules.py — Bộ lập kế hoạch "tiện nghi – năng lượng": khi nào CHỈ QUẠT, CHỈ ĐIỀU HÒA, hay KẾT HỢP.

Bài toán tối ưu nhỏ có ràng buộc, giải bằng cách thử hết các phương án:

    chọn (TẬP quạt bật ⊆ quạt khả dụng, điều hòa bật/tắt, nhiệt độ đặt Tset)
    sao cho  nhiệt độ cảm nhận ≤ comfort_max            (ràng buộc tiện nghi)
    và       công suất ước tính nhỏ nhất                 (mục tiêu tiết kiệm điện)

Đầu vào chia 2 nhóm (xem build_config):
  • Hồ sơ thiết bị & sở thích (device_profile.DeviceProfile) — sau này người dùng nhập trên app:
    công suất TỪNG quạt, quạt nào khả dụng, có điều hòa không, công suất điều hòa,
    nhiệt độ mong muốn, khoảng nhiệt độ đặt.
  • Tham số kỹ thuật của mô hình (config.Settings / .env) — nhóm phát triển chỉnh.

Mô hình (giải thích được):
  • Không AC:  cảm_nhận = T + phạt_ẩm(RH) + phạt_đông(N) − hiệu_ứng_quạt(n, RH)
  • Có AC:     cảm_nhận = Tset + phạt_đông(N) − hiệu_ứng_quạt(n, RH)   (AC hút ẩm nên bỏ phạt ẩm)
  • Hiệu ứng quạt theo SỐ quạt: 1.5 / 2.5 / 3.0°C (ASHRAE 55: gió bù tối đa ~3°C),
    ×0.75 khi RH ≥ 70%, ×0.5 khi RH ≥ 80% (ẩm → mồ hôi khó bay hơi).
  • Công suất = tổng công suất các quạt được chọn + AC(Tset); AC(Tset) = P_AC × (1 − k × (Tset − 24)).

Ba vùng tự xuất hiện:
  • CHỈ QUẠT : thiếu tiện nghi ≤ hiệu ứng quạt → dùng ít quạt / quạt tốn ít điện nhất đủ mát.
  • KẾT HỢP  : quạt không đủ → AC + quạt để đặt Tset cao (27–28°C), vì mỗi quạt đổi được
               ~1–1.5°C nhiệt độ đặt, đáng giá hơn điện quạt tiêu thụ.
  • CHỈ AC   : lợi ích quạt < điện quạt — điển hình RH ≥ 80% (quạt chỉ còn ½ hiệu quả);
               hoặc phòng không có quạt khả dụng.
Phòng không có AC → chỉ còn vùng "chỉ quạt" (quạt không đủ thì bật tối đa quạt khả dụng).
"""
import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Optional

N_FANS = 3


@dataclass
class PlannerConfig:
    # --- hồ sơ thiết bị & sở thích (DeviceProfile) ---
    comfort_max: float = 26.0
    fan_powers: tuple = (45.0, 45.0, 45.0)
    fans_available: tuple = (True, True, True)
    ac_available: bool = True
    ac_power_w: float = 900.0
    ac_min_temp: int = 20
    ac_set_max: int = 28
    # --- tham số kỹ thuật ---
    ac_saving_per_degree: float = 0.06
    fan_cooling: tuple = (1.5, 2.5, 3.0)
    humid_rh: float = 80.0
    hysteresis: float = 0.5
    pulldown_delta: float = 2.0
    baseline_ac_temp: int = 25
    ac_off_margin: float = 1.0

    @property
    def max_fans(self) -> int:
        return sum(1 for a in self.fans_available if a)

    @property
    def fan_power_avg(self) -> float:
        powers = [p for p, a in zip(self.fan_powers, self.fans_available) if a]
        return sum(powers) / len(powers) if powers else 0.0


def build_config(profile, settings) -> PlannerConfig:
    """Ghép hồ sơ thiết bị (người dùng) + tham số kỹ thuật (.env) thành cấu hình cho planner."""
    cooling = (list(settings.fan_cooling_effect) + [3.0, 3.0, 3.0])[:3]
    return PlannerConfig(
        comfort_max=profile.comfort_max_temp,
        fan_powers=tuple(float(p) for p in profile.fan_power_w),
        fans_available=tuple(bool(a) for a in profile.fans_available),
        ac_available=bool(profile.ac_available),
        ac_power_w=profile.ac_power_w,
        ac_min_temp=profile.ac_set_min,
        ac_set_max=profile.ac_set_max,
        ac_saving_per_degree=settings.ac_saving_per_degree,
        fan_cooling=tuple(cooling),
        humid_rh=settings.humid_rh,
        hysteresis=settings.hysteresis,
        pulldown_delta=settings.pulldown_delta,
        baseline_ac_temp=settings.baseline_ac_temp,
        ac_off_margin=settings.ac_off_margin,
    )


@dataclass
class Option:
    fans: int
    ac_on: bool
    ac_temp: Optional[int]
    feels_like: float
    power_w: float
    comfortable: bool = False
    label: str = ""
    fan_set: tuple = field(default=(False, False, False))

    def describe(self) -> str:
        ac = f"AC {self.ac_temp}°C" if self.ac_on else "AC tắt"
        return f"{fans_text(self.fan_set) if any(self.fan_set) else '0 quạt'} + {ac}"


# ----------------------------------------------------------------------
# Thành phần mô hình
# ----------------------------------------------------------------------
def humidity_penalty(rh: Optional[float]) -> float:
    """Độ ẩm > 60% làm cảm giác nóng hơn (tối đa +1.9°C ở RH 85%)."""
    if rh is None:
        return 0.0
    return max(0.0, min(rh, 85.0) - 60.0) * 0.075


def occupancy_penalty(people: int) -> float:
    """Mỗi người toả ~70–100 W nhiệt; phòng nhỏ đông người nóng nhanh hơn cảm biến kịp đo."""
    return min(1.5, 0.25 * max(0, people - 2))


def humidity_factor(rh: Optional[float]) -> float:
    if rh is None or rh < 70:
        return 1.0
    if rh < 80:
        return 0.75
    return 0.5


def fan_effect(fans: int, rh: Optional[float], cfg: PlannerConfig) -> float:
    if fans <= 0:
        return 0.0
    return cfg.fan_cooling[min(fans, N_FANS) - 1] * humidity_factor(rh)


def ac_power(tset: int, cfg: PlannerConfig) -> float:
    return cfg.ac_power_w * max(0.4, 1.0 - cfg.ac_saving_per_degree * (tset - 24))


def feels_like_still(temperature: float, rh: Optional[float], people: int) -> float:
    """Nhiệt độ cảm nhận khi không quạt, không AC."""
    return temperature + humidity_penalty(rh) + occupancy_penalty(people)


def fans_text(fan_set) -> str:
    on = [str(i + 1) for i, f in enumerate(fan_set) if f]
    return f"quạt {', '.join(on)}" if on else "không quạt"


# ----------------------------------------------------------------------
# Chọn quạt cụ thể
# ----------------------------------------------------------------------
def select_fans(count: int, current: tuple, cfg: PlannerConfig) -> tuple:
    """Chọn `count` quạt trong số quạt khả dụng: tổng công suất nhỏ nhất, rồi ít bật/tắt nhất
    so với hiện tại, rồi số thứ tự nhỏ. Quạt không khả dụng luôn tắt."""
    available = [i for i in range(N_FANS) if cfg.fans_available[i]]
    count = max(0, min(count, len(available)))
    best_key, best_set = None, (False,) * N_FANS
    for combo in combinations(available, count):
        fan_set = tuple(i in combo for i in range(N_FANS))
        power = sum(cfg.fan_powers[i] for i in combo)
        toggles = sum(1 for i in range(N_FANS) if fan_set[i] != bool(current[i]))
        key = (round(power, 1), toggles, combo)
        if best_key is None or key < best_key:
            best_key, best_set = key, fan_set
    return best_set


def fan_set_power(fan_set: tuple, cfg: PlannerConfig) -> float:
    return sum(p for p, on in zip(cfg.fan_powers, fan_set) if on)


# ----------------------------------------------------------------------
# Liệt kê, đánh giá và chọn phương án
# ----------------------------------------------------------------------
def _no_ac_conditions(temperature, rh, natural_temp, natural_rh):
    """Điều kiện phòng NẾU KHÔNG có AC. Khi AC đang chạy, nhiệt độ/độ ẩm đo được đã bị AC kéo
    xuống nên dùng giá trị "tự nhiên" ghi lại lúc trước khi bật AC (nếu có)."""
    t = max(temperature, natural_temp) if natural_temp is not None else temperature
    h = rh
    if natural_rh is not None:
        h = natural_rh if rh is None else max(rh, natural_rh)
    return t, h


def _best_tset(n: int, rh: Optional[float], people: int, cfg: PlannerConfig) -> int:
    """Nhiệt độ đặt CAO NHẤT vẫn đạt tiện nghi với n quạt (đặt càng cao càng ít điện)."""
    tset = math.floor(cfg.comfort_max - occupancy_penalty(people) + fan_effect(n, rh, cfg))
    upper = cfg.ac_set_max
    if rh is not None and rh >= cfg.humid_rh:
        upper = min(upper, 27)  # rất ẩm → không đặt quá cao để AC còn hút ẩm
    return int(min(upper, max(cfg.ac_min_temp, tset)))


def evaluate(fan_set: tuple, ac_on: bool, ac_temp: Optional[int], temperature: float, rh: Optional[float],
             people: int, cfg: PlannerConfig, natural_temp: Optional[float] = None,
             natural_rh: Optional[float] = None) -> Option:
    """Đánh giá một cấu hình cụ thể (tập quạt + AC) theo mô hình."""
    n = sum(1 for f in fan_set if f)
    if ac_on:
        feel = ac_temp + occupancy_penalty(people) - fan_effect(n, rh, cfg)
        power = ac_power(ac_temp, cfg) + fan_set_power(fan_set, cfg)
    else:
        t_no_ac, rh_no_ac = _no_ac_conditions(temperature, rh, natural_temp, natural_rh)
        feel = feels_like_still(t_no_ac, rh_no_ac, people) - fan_effect(n, rh_no_ac, cfg)
        power = fan_set_power(fan_set, cfg)
    opt = Option(fans=n, ac_on=ac_on, ac_temp=ac_temp if ac_on else None,
                 feels_like=round(feel, 2), power_w=round(power, 1), fan_set=tuple(fan_set))
    opt.comfortable = opt.feels_like <= cfg.comfort_max + 1e-6
    opt.label = classify(opt)
    return opt


def enumerate_options(temperature: float, rh: Optional[float], people: int, current_fans: tuple,
                      cfg: PlannerConfig, natural_temp: Optional[float] = None,
                      natural_rh: Optional[float] = None) -> list[Option]:
    options = []
    for n in range(cfg.max_fans + 1):
        fan_set = select_fans(n, current_fans, cfg)
        options.append(evaluate(fan_set, False, None, temperature, rh, people, cfg, natural_temp, natural_rh))
        if cfg.ac_available:
            tset = _best_tset(n, rh, people, cfg)
            options.append(evaluate(fan_set, True, tset, temperature, rh, people, cfg, natural_temp, natural_rh))
    return options


def _current_fans(current: dict) -> tuple:
    if "fans" in current:
        return tuple(bool(x) for x in current["fans"])
    n = int(current.get("fan_count", 0))
    return tuple(i < n for i in range(N_FANS))


def plan(temperature: float, rh: Optional[float], people: int, current: dict,
         cfg: PlannerConfig, natural_temp: Optional[float] = None,
         natural_rh: Optional[float] = None) -> tuple[Option, Option, list[Option]]:
    """
    Trả về (phương án đề xuất, phương án ổn định trước khi "làm mát nhanh", mọi phương án).
    current = {"fans": (bool, bool, bool) | "fan_count": int, "ac_on": bool, "ac_temp": int}
    """
    cur_fans = _current_fans(current)
    ac_running = bool(current.get("ac_on"))
    options = enumerate_options(temperature, rh, people, cur_fans, cfg, natural_temp, natural_rh)

    # AC đang chạy mà không biết điều kiện "tự nhiên" → chỉ cho tắt AC khi phòng đã mát hơn Tset
    # một khoảng. Tránh vòng lặp: AC làm mát → tắt AC → nóng lại → bật AC.
    allow_ac_off = (not ac_running or natural_temp is not None or not cfg.ac_available
                    or temperature <= current.get("ac_temp", 26) - cfg.ac_off_margin)
    now = evaluate(cur_fans, ac_running and cfg.ac_available, current.get("ac_temp", 26),
                   temperature, rh, people, cfg, natural_temp, natural_rh)
    for opt in options:
        if ac_running and not opt.ac_on and not allow_ac_off:
            opt.comfortable = False
            continue
        # Biên trễ: phương án làm phòng "ấm hơn" hiện tại phải mát hơn ngưỡng thêm HYSTERESIS
        warmer = opt.feels_like > now.feels_like + 1e-6
        limit = cfg.comfort_max - (cfg.hysteresis if warmer else 0.0)
        opt.comfortable = opt.feels_like <= limit + 1e-6

    feasible = [o for o in options if o.comfortable]
    if feasible:
        # ít điện nhất; hoà thì ít thiết bị hơn; rồi giữ trạng thái AC hiện tại
        best = min(feasible, key=lambda o: (round(o.power_w, 1), o.fans + (1 if o.ac_on else 0),
                                            o.ac_on != ac_running))
    else:
        best = min(options, key=lambda o: (o.feels_like, o.power_w))  # không đạt → mát nhất có thể
    best.label = classify(best)
    steady = best

    # Làm mát nhanh: vừa bật AC mà phòng còn nóng hơn Tset nhiều → bật hết quạt khả dụng
    # (khi rất ẩm, gió quạt kém hiệu quả nên không tăng tốc bằng quạt)
    if (best.ac_on and temperature - best.ac_temp >= cfg.pulldown_delta and best.fans < cfg.max_fans
            and humidity_factor(rh) >= 0.75):
        best = evaluate(select_fans(cfg.max_fans, cur_fans, cfg), True, best.ac_temp,
                        temperature, rh, people, cfg)
        best.comfortable, best.label = True, "làm mát nhanh"

    return best, steady, options


def classify(opt: Option) -> str:
    if opt.fans == 0 and not opt.ac_on:
        return "tắt hết"
    if not opt.ac_on:
        return "chỉ quạt"
    if opt.fans == 0:
        return "chỉ điều hòa"
    return "kết hợp"


def baseline_power(temperature: float, rh: Optional[float], people: int, cfg: PlannerConfig) -> float:
    """Cách dùng 'truyền thống' để so sánh: có người và thấy nóng → chỉ bật AC ở BASELINE_AC_TEMP
    (phòng không có AC → bật hết quạt)."""
    if people <= 0 or feels_like_still(temperature, rh, people) <= cfg.comfort_max:
        return 0.0
    if not cfg.ac_available:
        return round(fan_set_power(cfg.fans_available, cfg), 1)
    return round(ac_power(cfg.baseline_ac_temp, cfg), 1)


def explain(best: Option, temperature: float, rh: Optional[float], people: int,
            cfg: PlannerConfig) -> str:
    """Câu lý do tiếng Việt cho phương án của planner (dùng khi không có / lỗi Gemini)."""
    still = feels_like_still(temperature, rh, people)
    hum = f", ẩm {rh:.0f}%" if rh is not None else ""
    head = f"{people} người, {temperature:.1f}°C{hum} (cảm nhận ~{still:.1f}°C)"
    fans = fans_text(best.fan_set)
    label = best.label
    if label == "tắt hết":
        action = "đã thoải mái, tắt quạt và điều hòa"
    elif label == "chỉ quạt" and not best.comfortable:
        action = f"không có điều hòa → bật tối đa {fans}"
    elif label == "chỉ quạt":
        action = f"chỉ cần {fans}, chưa cần điều hòa"
    elif label == "chỉ điều hòa":
        action = f"rất ẩm, quạt kém hiệu quả → chỉ bật điều hòa {best.ac_temp}°C để làm mát và hút ẩm"
    elif label == "làm mát nhanh":
        action = f"phòng còn nóng → điều hòa {best.ac_temp}°C + {fans} để mát nhanh"
    else:
        action = f"điều hòa {best.ac_temp}°C kèm {fans} để đặt nhiệt độ cao hơn, tiết kiệm điện"
    return f"{head} → {action}."
