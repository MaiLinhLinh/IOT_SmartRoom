"""
simulator.py — Giả lập kịch bản nhiệt độ / độ ẩm / số người để DEMO các chế độ
(tắt hết, chỉ quạt, kết hợp, chỉ điều hòa, phòng trống) mà không cần thời tiết thay đổi thật.

Ba cách nối với hệ thống (tham số --mode của simulate.py):

  offline  : backend giả trong bộ nhớ. Không cần backend/ESP32 → tập dượt, chụp kết quả cho báo cáo.
  override : AI đọc context THẬT từ backend nhưng nhiệt độ/độ ẩm (và số người nếu kịch bản có) được
             THAY bằng số giả lập trước khi ra quyết định. Lệnh vẫn gửi thật → LED/LCD, Dashboard đổi theo.
             Không ghi gì vào sensor_logs, ESP32 + DHT11 thật vẫn chạy bình thường. (Khuyên dùng khi demo.)
  backend  : đẩy số giả lên backend (POST /api/sensor, /api/people/count) như một ESP32 giả, nên Dashboard
             hiện đúng nhiệt độ/độ ẩm giả lập. Cần ngắt gửi dữ liệu của ESP32 thật (rút dây DATA của DHT11:
             ESP32 đọc lỗi sẽ không gửi) nếu không số thật và số giả sẽ xen kẽ nhau.

Lý do AI gửi lên luôn có tiền tố "[Mô phỏng]" để người xem / lịch sử thao tác biết đây là dữ liệu giả lập.
"""
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from backend_client import BackendError

logger = logging.getLogger("simulator")

Number = Union[int, float]
SIM_TAG = "[Mô phỏng] "


# ======================================================================
# Kịch bản
# ======================================================================
@dataclass
class Step:
    name: str
    duration: float                                   # giây (trước khi chia --speed)
    temperature: Union[Number, list]                  # số, hoặc [từ, đến] để tăng/giảm dần
    humidity: Union[Number, list]
    people: Optional[int] = None                      # None = dùng số người thật (YOLO) / giữ bước trước
    note: str = ""                                    # câu giải thích cho người xem demo
    expect: list = field(default_factory=list)        # chế độ mong đợi khi kết thúc bước

    @staticmethod
    def _at(value, frac: float) -> float:
        if isinstance(value, (list, tuple)):
            start, end = float(value[0]), float(value[-1])
            return round(start + (end - start) * min(1.0, max(0.0, frac)), 1)
        return float(value)

    def values_at(self, frac: float) -> tuple[float, float]:
        return self._at(self.temperature, frac), self._at(self.humidity, frac)


# Kịch bản dựng sẵn. "expect" là chế độ của trạng thái thiết bị khi hết bước:
# tắt hết | chỉ quạt | kết hợp | chỉ điều hòa
SCENARIOS: dict[str, dict] = {
    "full": {
        "title": "Toàn bộ chiến lược: mát → chỉ quạt → kết hợp → đông người → phòng trống → nồm ẩm → hút ẩm",
        "steps": [
            dict(name="Phòng mát", duration=25, temperature=25.0, humidity=60, people=2,
                 note="Cảm nhận dưới 26°C → không cần thiết bị nào.", expect=["tắt hết"]),
            dict(name="Hơi ấm", duration=25, temperature=27.0, humidity=60, people=2,
                 note="Thiếu tiện nghi ~1°C, 1 quạt (~45 W) đủ bù → CHỈ QUẠT.", expect=["chỉ quạt"]),
            dict(name="Ấm hơn", duration=25, temperature=28.5, humidity=60, people=2,
                 note="Cần bù ~2.5°C → thêm quạt, vẫn chưa cần điều hòa.", expect=["chỉ quạt"]),
            dict(name="Nóng dần", duration=40, temperature=[29.0, 31.0], humidity=62, people=2,
                 note="Quạt hết khả năng bù → bật điều hòa KÈM quạt (đặt 28°C), lúc đầu bật hết quạt cho mát nhanh.",
                 expect=["kết hợp"]),
            dict(name="Điều hòa đã kéo nhiệt xuống", duration=30, temperature=28.4, humidity=60, people=2,
                 note="Phòng gần nhiệt độ đặt → bớt quạt, giữ điều hòa 28°C (KẾT HỢP ổn định).",
                 expect=["kết hợp"]),
            dict(name="Đông người", duration=30, temperature=28.4, humidity=60, people=6,
                 note="6 người toả nhiệt → thêm quạt / hạ nhiệt độ đặt.", expect=["kết hợp"]),
            dict(name="Mọi người ra về", duration=30, temperature=28.4, humidity=60, people=0,
                 note="Phòng trống → tắt quạt ngay, giữ điều hòa một lúc rồi tắt hết.", expect=["tắt hết"]),
            dict(name="Có người, trời nồm ẩm", duration=30, temperature=27.0, humidity=85, people=2,
                 note="Độ ẩm 85%: gió quạt kém hiệu quả → CHỈ ĐIỀU HÒA để làm mát và hút ẩm.",
                 expect=["chỉ điều hòa"]),
            dict(name="Điều hòa đã hút ẩm", duration=30, temperature=27.0, humidity=68, people=2,
                 note="Độ ẩm giảm → quạt hiệu quả trở lại → KẾT HỢP, nâng nhiệt độ đặt để tiết kiệm.",
                 expect=["kết hợp"]),
        ],
    },
    "chi-quat": {
        "title": "Vùng CHỈ QUẠT: số quạt tăng dần theo nhiệt độ",
        "steps": [
            dict(name="Phòng mát", duration=20, temperature=25.5, humidity=60, people=2, expect=["tắt hết"],
                 note="Đủ mát, không bật gì."),
            dict(name="27°C", duration=20, temperature=27.0, humidity=60, people=2, expect=["chỉ quạt"],
                 note="1 quạt."),
            dict(name="28.5°C", duration=20, temperature=28.5, humidity=60, people=2, expect=["chỉ quạt"],
                 note="2 quạt."),
            dict(name="29°C", duration=20, temperature=29.0, humidity=60, people=2, expect=["chỉ quạt"],
                 note="3 quạt — giới hạn của chế độ chỉ quạt."),
        ],
    },
    "ket-hop": {
        "title": "Vùng KẾT HỢP: điều hòa đặt cao + quạt",
        "steps": [
            dict(name="Nóng 31°C", duration=30, temperature=31.0, humidity=62, people=3, expect=["kết hợp"],
                 note="Bật điều hòa 28°C kèm hết quạt để mát nhanh."),
            dict(name="Ổn định 28.4°C", duration=30, temperature=28.4, humidity=60, people=2, expect=["kết hợp"],
                 note="Bớt quạt, giữ điều hòa đặt cao."),
            dict(name="Oi 28.4°C / 72%", duration=30, temperature=28.4, humidity=72, people=2, expect=["kết hợp"],
                 note="Ẩm hơn → quạt kém hiệu quả hơn → điều chỉnh nhiệt độ đặt / số quạt."),
        ],
    },
    "chi-dieu-hoa": {
        "title": "Vùng CHỈ ĐIỀU HÒA: trời nồm ẩm",
        "steps": [
            dict(name="Phòng mát", duration=15, temperature=25.0, humidity=60, people=2, expect=["tắt hết"],
                 note="Khởi đầu: không bật gì."),
            dict(name="Nồm ẩm 27°C / 85%", duration=30, temperature=27.0, humidity=85, people=2,
                 expect=["chỉ điều hòa"], note="Quạt chỉ còn ½ hiệu quả → chỉ bật điều hòa."),
            dict(name="Đã hút ẩm còn 68%", duration=30, temperature=27.0, humidity=68, people=2,
                 expect=["kết hợp"], note="Chuyển sang kết hợp, đặt nhiệt độ cao hơn."),
        ],
    },
    "phong-trong": {
        "title": "Phòng trống: tắt quạt ngay, giữ điều hòa một lúc rồi tắt",
        "steps": [
            dict(name="Đang có người, nóng", duration=30, temperature=30.0, humidity=60, people=3,
                 expect=["kết hợp"], note="Điều hòa + quạt đang chạy."),
            dict(name="Mọi người ra ngoài", duration=10, temperature=29.5, humidity=60, people=0,
                 expect=[], note="Tắt quạt ngay, điều hòa chờ (đặt 28°C) phòng khi có người quay lại."),
            dict(name="Vẫn trống", duration=20, temperature=29.5, humidity=60, people=0,
                 expect=["tắt hết"], note="Hết thời gian chờ → tắt điều hòa."),
            dict(name="Có người quay lại", duration=25, temperature=29.0, humidity=60, people=2,
                 expect=["chỉ quạt"], note="29°C, 2 người: quạt là đủ, chưa cần bật lại điều hòa."),
        ],
    },
}


def load_scenario(name_or_path: str) -> tuple[str, str, list[Step]]:
    """Kịch bản dựng sẵn (tên) hoặc file JSON {"title": ..., "steps": [...]}."""
    if name_or_path in SCENARIOS:
        data, name = SCENARIOS[name_or_path], name_or_path
    else:
        path = Path(name_or_path)
        if not path.exists():
            raise FileNotFoundError(f"Không có kịch bản '{name_or_path}'. Có sẵn: {', '.join(SCENARIOS)}")
        data, name = json.loads(path.read_text(encoding="utf-8")), path.stem
    steps = [Step(**s) for s in data["steps"]]
    return name, data.get("title", name), steps


# ======================================================================
# Backend dùng cho giả lập
# ======================================================================
class InMemoryBackend:
    """Backend giả trong bộ nhớ (mode offline) — cùng giao diện với BackendClient."""

    def __init__(self):
        self.state = {"mode": "AUTO", "fan1": False, "fan2": False, "fan3": False, "ac_on": False,
                      "ac_temp": 26, "people_count": 0, "people_source": "NONE", "people_updated_at": None,
                      "temperature": None, "humidity": None, "sensor_timestamp": None, "ai_reason": None}
        self.commands: list[dict] = []

    def get_context(self) -> dict:
        return dict(self.state)

    def send_command(self, fans, ac_on, ac_temp, ai_reason) -> dict:
        f1, f2, f3 = (bool(x) for x in fans)
        self.state.update(fan1=f1, fan2=f2, fan3=f3, ac_on=bool(ac_on), ac_temp=int(ac_temp), ai_reason=ai_reason)
        self.commands.append({"time": datetime.now().isoformat(timespec="seconds"), **self.state})
        return self.state

    def push_people_count(self, count: int, source: str = "YOLO") -> dict:
        self.state.update(people_count=int(count), people_source=source,
                          people_updated_at=datetime.now().isoformat())
        return self.state

    def push_sensor(self, temperature: float, humidity: float) -> None:
        self.state.update(temperature=temperature, humidity=humidity, sensor_timestamp=datetime.now().isoformat())

    def get_snapshot(self, url=None) -> bytes:
        raise BackendError("offline: không có camera")


class SimulatedBackend:
    """
    Bọc backend thật/giả: thay dữ liệu môi trường bằng số giả lập.
      mode="override": chỉ thay trong context AI đọc (không ghi vào backend).
      mode="backend" : POST số giả lên /api/sensor (+ /api/people/count) rồi đọc lại như bình thường.
      mode="offline" : dùng InMemoryBackend.
    Mọi lệnh gửi đi được gắn tiền tố "[Mô phỏng]".
    dry_run=True: KHÔNG gửi lệnh lên backend, nhưng giữ một "trạng thái ảo" để các bước sau của kịch bản
    vẫn diễn ra như thật (AI thấy quạt/AC đang chạy theo lệnh trước của nó).
    """

    def __init__(self, inner, mode: str, tag_reason: bool = True, dry_run: bool = False):
        self.inner = inner
        self.mode = mode
        self.tag_reason = tag_reason
        self.dry_run = dry_run
        self.virtual: dict = {}
        self.sent: list[dict] = []
        self.temperature: Optional[float] = None
        self.humidity: Optional[float] = None
        self.people: Optional[int] = None
        self.last_people_push: Optional[int] = None

    # ---------- cập nhật giá trị giả lập ----------
    def set_values(self, temperature: float, humidity: float, people: Optional[int]) -> None:
        self.temperature, self.humidity, self.people = temperature, humidity, people
        if self.mode == "offline":
            self.inner.push_sensor(temperature, humidity)
            if people is not None:
                self.inner.push_people_count(people, source="MANUAL")
        elif self.mode == "backend":
            self._post_sensor(temperature, humidity)
            if people is not None:
                try:
                    self.inner.push_people_count(people, source="MANUAL")
                except BackendError as exc:
                    logger.warning("Không gửi được số người giả lập: %s", exc)

    def _post_sensor(self, temperature: float, humidity: float) -> None:
        try:
            resp = self.inner.session.post(f"{self.inner.base_url}/api/sensor",
                                           json={"temperature": temperature, "humidity": humidity},
                                           timeout=self.inner.timeout)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Không gửi được dữ liệu cảm biến giả lập: %s", exc)

    # ---------- giao diện giống BackendClient ----------
    def get_context(self) -> dict:
        ctx = self.inner.get_context()
        if self.mode == "override" and self.temperature is not None:
            now = datetime.now().isoformat()
            ctx.update(temperature=self.temperature, humidity=self.humidity, sensor_timestamp=now)
            if self.people is not None:
                ctx.update(people_count=self.people, people_source="MANUAL", people_updated_at=now)
        ctx.update(self.virtual)
        return ctx

    def send_command(self, fans, ac_on, ac_temp, ai_reason):
        reason = ((SIM_TAG + ai_reason) if self.tag_reason else ai_reason)[:500]
        f1, f2, f3 = (bool(x) for x in fans)
        record = dict(fan1=f1, fan2=f2, fan3=f3, ac_on=bool(ac_on), ac_temp=int(ac_temp), ai_reason=reason)
        self.sent.append(record)
        if self.dry_run:
            self.virtual = record
            return {**self.inner.get_context(), **record}
        return self.inner.send_command(fans, ac_on, ac_temp, reason)

    def push_people_count(self, count, source="YOLO"):
        return self.inner.push_people_count(count, source)

    def get_snapshot(self, url=None):
        return self.inner.get_snapshot(url)


def classify_state(ctx: dict) -> str:
    fans = sum(1 for k in ("fan1", "fan2", "fan3") if ctx.get(k))
    if ctx.get("ac_on"):
        return "kết hợp" if fans else "chỉ điều hòa"
    return "chỉ quạt" if fans else "tắt hết"


def state_text(ctx: dict) -> str:
    fans = [str(i) for i, k in enumerate(("fan1", "fan2", "fan3"), start=1) if ctx.get(k)]
    fan = f"quạt {','.join(fans)}" if fans else "không quạt"
    ac = f"AC {ctx.get('ac_temp')}°C" if ctx.get("ac_on") else "AC tắt"
    return f"{fan}, {ac}"


# ======================================================================
# Chạy kịch bản
# ======================================================================
@dataclass
class StepResult:
    name: str
    temperature: float
    humidity: float
    people: Optional[int]
    state: str
    mode_label: str
    expect: list
    ok: Optional[bool]
    decisions: int
    reason: str


def run_scenario(engine, sim: SimulatedBackend, steps: list[Step], speed: float = 1.0,
                 tick: float = 2.0, printer=print, check_manual=True) -> list[StepResult]:
    results: list[StepResult] = []
    for idx, step in enumerate(steps, start=1):
        duration = max(tick, step.duration / max(speed, 0.01))
        t_text = (f"{step.temperature[0]}→{step.temperature[-1]}" if isinstance(step.temperature, list)
                  else f"{step.temperature}")
        h_text = (f"{step.humidity[0]}→{step.humidity[-1]}" if isinstance(step.humidity, list)
                  else f"{step.humidity}")
        ppl = "YOLO thật" if step.people is None else step.people
        printer("")
        printer(f"━━ Bước {idx}/{len(steps)}: {step.name} ━━  {t_text}°C, {h_text}%, người: {ppl}  ({duration:.0f}s)")
        if step.note:
            printer(f"   {step.note}")

        started = time.monotonic()
        n_decisions = 0
        last_reason = ""
        temp = hum = None
        while True:
            frac = (time.monotonic() - started) / duration
            temp, hum = step.values_at(frac)
            sim.set_values(temp, hum, step.people)
            decision = engine.step()
            if decision is not None:
                n_decisions += 1
                last_reason = decision.ai_reason
                printer(f"   → [{decision.source}] {decision.mode_label}: {state_text_from_decision(decision)}"
                        f"  (~{decision.est_power_w:.0f} W)  T={temp} H={hum}")
            if frac >= 1.0:
                break
            remaining = duration - (time.monotonic() - started)
            time.sleep(max(0.05, min(tick, remaining)))

        ctx = sim.get_context()
        if check_manual and ctx.get("mode") != "AUTO":
            printer("   ⚠ Thiết bị đang MANUAL → AI không điều khiển. Chuyển sang AUTO rồi chạy lại.")
        label = classify_state(ctx)
        state = ("(dry-run) " if sim.dry_run else "") + state_text(ctx)
        ok = (label in step.expect) if step.expect else None
        mark = "" if ok is None else ("✓" if ok else f"✗ (mong đợi: {', '.join(step.expect)})")
        printer(f"   Kết thúc bước: {state} → {label} {mark}")
        results.append(StepResult(step.name, temp, hum, step.people, state, label, step.expect, ok,
                                  n_decisions, last_reason))
    return results


def state_text_from_decision(d) -> str:
    fans = [str(i + 1) for i, f in enumerate(d.fans) if f]
    fan = f"quạt {','.join(fans)}" if fans else "không quạt"
    return f"{fan}, " + (f"AC {d.ac_temp}°C" if d.ac_on else "AC tắt")


def print_summary(results: list[StepResult], printer=print) -> int:
    printer("")
    printer("TỔNG KẾT")
    printer(f"{'#':>2}  {'Bước':<30} {'T':>5} {'RH':>4} {'Người':>5}  {'Kết quả':<14} {'Mong đợi':<14} ")
    passed = failed = 0
    for i, r in enumerate(results, start=1):
        exp = ", ".join(r.expect) if r.expect else "—"
        mark = "" if r.ok is None else ("✓" if r.ok else "✗")
        passed += r.ok is True
        failed += r.ok is False
        people = "YOLO" if r.people is None else r.people
        printer(f"{i:>2}  {r.name[:30]:<30} {r.temperature:>5} {r.humidity:>4} {people!s:>5}  "
                f"{r.mode_label:<14} {exp:<14} {mark}")
    printer(f"Đạt {passed}/{passed + failed} bước có kết quả mong đợi.")
    return failed
