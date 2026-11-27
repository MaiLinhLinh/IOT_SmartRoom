"""
decision_engine.py — Chức năng 2: phân tích bằng LLM (Gemini) và ra lệnh 3 quạt + điều hòa (backend v4).

Mỗi DECISION_CHECK_SECONDS (mặc định 5s):
  1. GET /api/ai/context → mode, nhiệt độ, độ ẩm, số người, fan1/fan2/fan3, ac_on, ac_temp.
  2. Chỉ xử lý khi mode = AUTO và dữ liệu cảm biến còn mới.
  3. Chỉ ra quyết định khi có thay đổi đáng kể (số người, nhiệt độ ≥ TEMP_DELTA, độ ẩm ≥ HUMIDITY_DELTA,
     trạng thái thiết bị bị đổi, hết thời gian chờ phòng trống) hoặc quá LLM_MAX_INTERVAL_SECONDS.
  4. rules.plan() tính nhiệt độ cảm nhận + công suất của 8 phương án (0–3 quạt × AC bật/tắt)
     và chọn phương án ít điện nhất vẫn đạt tiện nghi.
  5. Có người → Gemini xem bảng phương án + bối cảnh, chọn cấu hình cuối và viết lý do.
     Gemini lỗi → dùng phương án của planner. Phòng trống → xử lý tự động, không gọi Gemini.
  6. Ràng buộc vận hành: thời gian bật/tắt tối thiểu của AC, chờ trước khi tắt AC khi phòng trống.
  7. Chọn quạt cụ thể sao cho ít bật/tắt nhất → POST /api/ai/command {fan1, fan2, fan3, ...}.
  8. Ghi logs/decisions.jsonl (đầu vào, phương án planner, quyết định cuối, công suất ước tính,
     công suất nếu "chỉ bật AC 25°C" để so sánh).
"""
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend_client import BackendClient, BackendError, ModeIsManual
from config import Settings
from llm_client import GeminiClient, LLMError
from prompts import RESPONSE_SCHEMA, build_system_prompt, build_user_prompt
import device_profile
import events
import rules
from rules import fans_text

logger = logging.getLogger("decision")


@dataclass
class Decision:
    fan_count: int
    ac_on: bool
    ac_temp: int
    ai_reason: str
    source: str            # "GEMINI" | "RULE" | "AUTO"
    fans: tuple = (False, False, False)
    mode_label: str = ""   # chỉ quạt / kết hợp / chỉ điều hòa / tắt hết / làm mát nhanh / chờ tắt
    est_power_w: float = 0.0


# ----------------------------------------------------------------------
# Tiện ích
# ----------------------------------------------------------------------
def parse_time(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def age_seconds(value) -> Optional[float]:
    ts = parse_time(value)
    if ts is None:
        return None
    now = datetime.now(timezone.utc) if ts.tzinfo else datetime.now()
    return max(0.0, (now - ts).total_seconds())


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("true", "1", "yes", "on", "bật", "bat")


def parse_fans(ctx: dict) -> tuple:
    """Đọc trạng thái 3 quạt từ context v4; vẫn hiểu được context v3 (fan_level)."""
    if any(k in ctx for k in ("fan1", "fan2", "fan3")):
        return tuple(_to_bool(ctx.get(k, False)) for k in ("fan1", "fan2", "fan3"))
    level = int(ctx.get("fan_level") or 0)
    return tuple(i < level for i in range(3))


def _backend_hint(msg: str, url: str) -> str:
    if "401" in msg:
        return "API key sai: PEOPLE_API_KEY / AI_API_KEY trong ai_service/.env phải giống backend/.env."
    if "500" in msg:
        return "Lỗi phía backend — xem log backend."
    if "localhost" in url or "127.0.0.1" in url:
        return "BACKEND_URL đang là máy này nhưng không có backend. Sửa .env thành IP máy chạy backend."
    return "Kiểm tra backend đang chạy, đúng IP/cổng, cùng mạng."


def _dev_text(dev: tuple) -> str:
    fans, ac_on, ac_temp = dev
    return f"{fans_text(fans)}, " + (f"AC {ac_temp}°C" if ac_on else "AC tắt")


def validate_decision(raw: dict, current_ac_temp: int, ac_min: int, ac_max: int,
                      max_fans: int = 3, ac_available: bool = True) -> dict:
    """Kiểm tra JSON của LLM, kẹp về khoảng hợp lệ. Thiếu field → ValueError."""
    if "fan_count" in raw:
        fan_count = int(round(float(raw["fan_count"])))
    elif any(k in raw for k in ("fan1", "fan2", "fan3")):
        fan_count = sum(_to_bool(raw.get(k, False)) for k in ("fan1", "fan2", "fan3"))
    else:
        raise ValueError("LLM thiếu fan_count")
    if "ac_on" not in raw:
        raise ValueError("LLM thiếu ac_on")

    fan_count = min(max_fans, max(0, fan_count))
    ac_on = _to_bool(raw["ac_on"]) and ac_available
    try:
        ac_temp = int(round(float(raw.get("ac_temp", current_ac_temp))))
    except (TypeError, ValueError):
        ac_temp = current_ac_temp
    if not ac_on:
        ac_temp = current_ac_temp  # tắt AC thì giữ giá trị đặt cũ
    ac_temp = min(ac_max, max(ac_min, ac_temp))

    reason = str(raw.get("ai_reason") or "").strip().replace("\n", " ")
    return {"fan_count": fan_count, "ac_on": ac_on, "ac_temp": ac_temp,
            "ai_reason": (reason or "Gemini không ghi lý do.")[:300]}


# ----------------------------------------------------------------------
# Bộ quyết định
# ----------------------------------------------------------------------
class DecisionEngine:
    def __init__(self, settings: Settings, backend: BackendClient, llm: Optional[GeminiClient]):
        self.s = settings
        self.backend = backend
        self.llm = llm
        self.profile, warnings = device_profile.from_env()
        for w in warnings:
            logger.warning("Hồ sơ thiết bị: %s", w)
        self._profile_raw: Optional[str] = None
        self._device_info_at: float = 0.0
        self._device_info_data: Optional[dict] = None
        self._device_info_missing_logged = False
        self._rebuild_config()
        self._last_inputs: Optional[dict] = None
        self._last_call: float = 0.0
        self._last_eval: float = 0.0          # lần cuối xem xét quyết định (kể cả khi giữ nguyên)
        self._last_seen_ac: Optional[bool] = None
        self._ac_changed_at: Optional[float] = None       # lần cuối AC đổi bật/tắt (monotonic)
        self._recheck_at: Optional[float] = None          # hết khoá máy nén → quyết định lại ngay
        self._expected_device: Optional[tuple] = None     # trạng thái ai_service vừa gửi
        self._last_external_warn: float = 0.0
        self._while_ac_off: Optional[tuple] = None        # (nhiệt độ, độ ẩm) đo gần nhất khi AC tắt
        self._natural: Optional[tuple] = None             # (nhiệt độ, độ ẩm "tự nhiên", thời điểm)
        self._empty_since: Optional[float] = None
        self._log_path = Path(settings.decision_log_path) if settings.decision_log_path else None
        if self._log_path and not self._log_path.is_absolute():
            self._log_path = Path(__file__).resolve().parent / self._log_path

    # ---------- hồ sơ thiết bị (mặc định → .env → backend "config") ----------
    def _rebuild_config(self) -> None:
        self.cfg = rules.build_config(self.profile, self.s)
        self.system_prompt = build_system_prompt(self.cfg)

    def _fetch_device_info(self) -> Optional[dict]:
        """Đọc GET /api/devices/info (backend v5) định kỳ. Trả dict cho DeviceProfile.apply() hoặc None."""
        if not hasattr(self.backend, "get_device_info"):
            return None
        now = time.monotonic()
        if self._device_info_at and now - self._device_info_at < self.s.device_info_refresh_seconds:
            return self._device_info_data
        self._device_info_at = now
        try:
            items = self.backend.get_device_info()
        except BackendError as exc:
            logger.debug("Không đọc được /api/devices/info: %s", exc)
            return self._device_info_data      # giữ dữ liệu lần trước
        if items is None:
            if not self._device_info_missing_logged:
                logger.info("Backend chưa có /api/devices/info → dùng công suất từ .env/mặc định")
                self._device_info_missing_logged = True
            self._device_info_data = None
        else:
            self._device_info_data = device_profile.from_device_info(items)
        return self._device_info_data

    def _update_profile(self, ctx: dict) -> None:
        """Ghép hồ sơ: mặc định → .env → /api/devices/info → context["config"].
        Chỉ dựng lại cấu hình khi nội dung thay đổi."""
        info = self._fetch_device_info()
        raw = ctx.get("config") if isinstance(ctx.get("config"), dict) else None
        key = json.dumps([info, raw], sort_keys=True, default=str)
        if key == self._profile_raw:
            return
        self._profile_raw = key
        base, _ = device_profile.from_env()
        warnings = base.apply(info or {}, "backend:devices/info") + base.apply(raw or {}, "backend:config")
        for w in warnings:
            logger.warning("Hồ sơ thiết bị: %s", w)
        if base.signature() != self.profile.signature():
            logger.info("Cập nhật hồ sơ thiết bị từ backend: %s", base.to_dict())
            self.profile = base
            self._rebuild_config()
            self._last_inputs = None  # cấu hình đổi → quyết định lại ngay
        else:
            self.profile.sources = base.sources

    # ---------- theo dõi trạng thái AC ----------
    def _observe(self, temperature: float, humidity: Optional[float], ac_on: bool) -> None:
        now = time.monotonic()
        if self._last_seen_ac is not None and ac_on != self._last_seen_ac:
            self._ac_changed_at = now
            if ac_on:  # AC vừa bật (do ai cũng được) → nhớ điều kiện phòng trước khi bật
                t, h = self._while_ac_off or (temperature, humidity)
                self._natural = (t, h, now)
        self._last_seen_ac = ac_on
        if not ac_on:
            self._while_ac_off = (temperature, humidity)
            self._natural = None

    def _natural_estimate(self) -> tuple:
        """(nhiệt độ, độ ẩm) của phòng nếu không có AC, hoặc (None, None) nếu không biết / quá cũ."""
        if not self._natural:
            return None, None
        t, h, since = self._natural
        if time.monotonic() - since > self.s.natural_temp_ttl_seconds:
            return None, None
        return t, h

    # ---------- khi nào cần quyết định lại ----------
    def _trigger_reason(self, inputs: dict) -> Optional[str]:
        last = self._last_inputs
        if last is None:
            return "lần đầu / vừa chuyển sang AUTO"
        if inputs["people"] != last["people"]:
            return f"số người đổi {last['people']} → {inputs['people']}"
        if abs(inputs["temperature"] - last["temperature"]) >= self.s.temp_delta:
            return f"nhiệt độ đổi {last['temperature']:.1f} → {inputs['temperature']:.1f}°C"
        if (inputs["humidity"] is not None and last["humidity"] is not None
                and abs(inputs["humidity"] - last["humidity"]) >= self.s.humidity_delta):
            return f"độ ẩm đổi {last['humidity']:.0f} → {inputs['humidity']:.0f}%"
        if self._recheck_at is not None and time.monotonic() >= self._recheck_at:
            return "hết thời gian bảo vệ máy nén điều hòa"
        if inputs["device"] != last["device"]:
            return "trạng thái thiết bị bị thay đổi từ bên ngoài"
        if (inputs["people"] == 0 and inputs["device"][1] and self._empty_since is not None
                and time.monotonic() - self._empty_since >= self.s.empty_ac_grace_seconds):
            return "phòng trống đủ lâu"
        if time.monotonic() - max(self._last_call, self._last_eval) >= self.s.llm_max_interval:
            return "đánh giá lại định kỳ"
        return None

    # ---------- một bước ----------
    def step(self) -> Optional[Decision]:
        try:
            ctx = self.backend.get_context()
        except BackendError as exc:
            logger.warning("Không lấy được context: %s", exc)
            events.alert("context", "error", "Không đọc được dữ liệu từ backend",
                         "Không có phản hồi" if "lỗi kết nối" in str(exc) else str(exc)[:160],
                         _backend_hint(str(exc), self.s.backend_url))
            return None
        events.clear_alert("context")

        if ctx.get("mode") != "AUTO":
            if self._last_inputs is not None:
                logger.info("Thiết bị chuyển sang %s → tạm dừng AI", ctx.get("mode"))
            events.alert("manual", "warn", f"Thiết bị đang {ctx.get('mode')} — AI tạm dừng",
                         "", "Chuyển AUTO ở tab Thiết bị (hoặc trên app).")
            self._last_inputs = None
            return None
        events.clear_alert("manual")

        self._update_profile(ctx)
        temperature = ctx.get("temperature")
        if temperature is None:
            logger.info("Chưa có dữ liệu nhiệt độ từ ESP32 → bỏ qua")
            events.alert("sensor", "warn", "Chưa có nhiệt độ từ ESP32", "", "Kiểm tra ESP32 + DHT11 đã gửi /api/sensor.")
            return None
        temperature = float(temperature)
        sensor_age = age_seconds(ctx.get("sensor_timestamp"))
        if sensor_age is not None and sensor_age > self.s.sensor_stale_seconds:
            logger.warning("Dữ liệu cảm biến đã cũ %.0fs → không ra quyết định", sensor_age)
            events.alert("sensor", "warn", "Cảm biến ngừng cập nhật", "AI tạm không ra lệnh.",
                         "Kiểm tra ESP32 còn kết nối WiFi / backend.")
            return None
        events.clear_alert("sensor")

        people_age = age_seconds(ctx.get("people_updated_at"))
        people_fresh = people_age is not None and people_age <= self.s.people_stale_seconds
        people = int(ctx.get("people_count") or 0) if people_fresh else None
        humidity = ctx.get("humidity")
        humidity = float(humidity) if humidity is not None else None

        fans_now = parse_fans(ctx)
        current = {
            "fans": fans_now,
            "fan_count": sum(fans_now),
            "ac_on": _to_bool(ctx.get("ac_on", False)),
            "ac_temp": int(ctx.get("ac_temp") or 26),
        }
        self._check_external_change(current)
        self._observe(temperature, humidity, current["ac_on"])
        if people == 0:
            self._empty_since = self._empty_since or time.monotonic()
        elif people is not None:
            self._empty_since = None

        inputs = {
            "people": people,
            "temperature": temperature,
            "humidity": humidity,
            "device": (fans_now, current["ac_on"], current["ac_temp"]),
        }
        why = self._trigger_reason(inputs)
        if why is None:
            return None
        if time.monotonic() - self._last_call < self.s.llm_min_interval:
            return None  # giữ trigger, xử lý ở vòng sau

        if people is None:
            logger.info("Số người không rõ (camera chưa gửi / dữ liệu cũ) → giữ nguyên trạng thái")
            events.alert("people", "warn", "Chưa có số người", "AI giữ nguyên thiết bị.",
                         "Bật YOLO đếm người (Chạy AI thật → Cả hai).")
            self._remember(inputs)
            self._last_eval = time.monotonic()
            return None

        events.clear_alert("people")
        self._last_call = time.monotonic()
        self._recheck_at = None
        natural = self._natural_estimate() if current["ac_on"] else (None, None)
        best, steady, options = rules.plan(temperature, humidity, people, current, self.cfg, *natural)
        logger.info("Ra quyết định (%s): người=%d T=%.1f H=%s | hiện tại %s, AC %s | planner: %s (%s, ~%.0f W)",
                    why, people, temperature, humidity, fans_text(fans_now),
                    f"{current['ac_temp']}°C" if current["ac_on"] else "tắt",
                    best.describe(), best.label, best.power_w)

        keeps_state = (best.fan_set == fans_now and best.ac_on == current["ac_on"]
                       and (not best.ac_on or best.ac_temp == current["ac_temp"]))
        minor = why == "đánh giá lại định kỳ" or why.startswith(("nhiệt độ đổi", "độ ẩm đổi"))
        if people > 0 and keeps_state and minor:
            # Tối ưu: planner xác nhận trạng thái hiện tại vẫn tối ưu → không gọi Gemini, không gửi lệnh
            logger.info("Trạng thái hiện tại vẫn là phương án tốt nhất (%s) → giữ nguyên, không gọi Gemini",
                        best.label)
            events.emit("keep", label=best.label, power=round(best.power_w), temperature=temperature,
                        humidity=humidity, people=people)
            self._remember(inputs)
            return None

        notes = self._constraint_notes(current)
        llm_error = latency = None
        if people == 0:
            decision = self._empty_room_decision(temperature, current)
        else:
            decision, latency, llm_error = self._ask_llm(ctx, temperature, humidity, people, people_age,
                                                         current, best, options, notes)
            if decision is not None:
                decision = self._comfort_guard(decision, best, temperature, humidity, people, natural)
            if decision is None:
                decision = Decision(fan_count=best.fans, ac_on=best.ac_on,
                                    ac_temp=best.ac_temp if best.ac_on else current["ac_temp"],
                                    ai_reason=rules.explain(best, temperature, humidity, people, self.cfg),
                                    source="RULE", mode_label=best.label)

        held = self._apply_ac_timing(decision, current)
        if decision.fan_count == best.fans:
            decision.fans = best.fan_set
        else:
            decision.fans = rules.select_fans(decision.fan_count, fans_now, self.cfg)
        final = rules.evaluate(decision.fans, decision.ac_on, decision.ac_temp, temperature, humidity,
                               people, self.cfg, *natural)
        decision.est_power_w = final.power_w
        decision.mode_label = decision.mode_label or final.label

        prefix = {"GEMINI": "[Gemini] ", "RULE": "[Luật dự phòng] ", "AUTO": "[Tự động] "}[decision.source]
        reason = f"{prefix}{decision.ai_reason} (~{decision.est_power_w:.0f} W)"[:500]

        sent, send_error = False, None
        if self.s.dry_run:
            logger.info("DRY-RUN, không gửi: %s, AC %s | %s", fans_text(decision.fans),
                        f"{decision.ac_temp}°C" if decision.ac_on else "tắt", reason)
        else:
            try:
                self.backend.send_command(decision.fans, decision.ac_on, decision.ac_temp, reason)
                sent = True
                logger.info("→ Đã gửi lệnh: %s, AC %s | %s", fans_text(decision.fans),
                            f"ON {decision.ac_temp}°C" if decision.ac_on else "OFF", reason)
            except ModeIsManual:
                send_error = "MANUAL"
                logger.info("Người dùng vừa chuyển MANUAL → bỏ lệnh")
            except BackendError as exc:
                send_error = str(exc)
                logger.warning("Không gửi được lệnh: %s", exc)
                events.alert("send", "error", "Không gửi được lệnh", send_error,
                             _backend_hint(send_error, self.s.backend_url))
        if sent:
            events.clear_alert("send")

        if sent:
            self._expected_device = (tuple(decision.fans), decision.ac_on, decision.ac_temp)
            self._observe(temperature, humidity, decision.ac_on)
            device = (decision.fans, decision.ac_on, decision.ac_temp)
        else:
            device = inputs["device"]
        self._remember({**inputs, "device": device})
        self._write_log(why, inputs, current, best, steady, decision, latency, llm_error, sent, send_error)
        events.emit("decision", source=decision.source, label=decision.mode_label, fans=list(decision.fans),
                    ac_on=decision.ac_on, ac_temp=decision.ac_temp, power=round(decision.est_power_w),
                    baseline=round(rules.baseline_power(temperature, humidity, people, self.cfg)), reason=decision.ai_reason, trigger=why,
                    temperature=temperature, humidity=humidity, people=people, sent=sent,
                    dry_run=self.s.dry_run, send_error=send_error, llm_error=llm_error, latency=latency,
                    model=getattr(self.llm, "last_model", None) if decision.source == "GEMINI" else None,
                    held=held)
        return decision

    # ---------- các nhánh quyết định ----------
    def _empty_room_decision(self, temperature: float, current: dict) -> Decision:
        """Phòng trống: tắt quạt ngay; AC chờ EMPTY_AC_GRACE_SECONDS (người có thể quay lại ngay,
        khởi động lại máy nén tốn điện hơn) nhưng nâng nhiệt độ đặt lên để tiết kiệm."""
        empty_for = time.monotonic() - (self._empty_since or time.monotonic())
        if current["ac_on"] and empty_for < self.s.empty_ac_grace_seconds:
            hold = max(current["ac_temp"], self.cfg.ac_set_max)
            remain = self.s.empty_ac_grace_seconds - empty_for
            return Decision(0, True, hold,
                            f"Phòng vừa trống → tắt quạt, giữ điều hòa {hold}°C thêm "
                            f"{f'~{remain / 60:.0f} phút' if remain >= 90 else f'{remain:.0f} giây'} "
                            f"phòng khi có người quay lại.", "AUTO", mode_label="chờ tắt")
        return Decision(0, False, current["ac_temp"],
                        f"Phòng trống, {temperature:.1f}°C → tắt toàn bộ quạt và điều hòa để tiết kiệm điện.",
                        "AUTO", mode_label="tắt hết")

    def _ask_llm(self, ctx, temperature, humidity, people, people_age, current, best, options, notes):
        if self.llm is None:
            return None, None, None
        user_prompt = build_user_prompt(
            people_count=people, people_age=people_age, temperature=temperature, humidity=humidity,
            feels_like=rules.feels_like_still(temperature, humidity, people),
            current=current, options=options, recommended=best, notes=notes,
            last_reason=ctx.get("ai_reason"),
        )
        started = time.monotonic()
        try:
            raw = self.llm.generate_json(self.system_prompt, user_prompt, RESPONSE_SCHEMA)
            latency = round(time.monotonic() - started, 2)
            checked = validate_decision(raw, current["ac_temp"], self.cfg.ac_min_temp, self.cfg.ac_set_max,
                                        self.cfg.max_fans, self.cfg.ac_available)
            logger.info("Gemini (%s, %.1fs): %s", self.llm.last_model, latency, raw)
            return Decision(**checked, source="GEMINI"), latency, None
        except (LLMError, ValueError, TypeError) as exc:
            latency = round(time.monotonic() - started, 2)
            logger.warning("Gemini lỗi → dùng phương án của planner: %s", exc)
            return None, latency, str(exc)

    def _comfort_guard(self, d: Decision, best, temperature, humidity, people, natural) -> Decision:
        """Gemini được quyền chọn khác planner, nhưng không được để phòng nóng rõ rệt
        (cảm nhận vượt ngưỡng tiện nghi > 1°C) khi planner có phương án đạt tiện nghi."""
        fan_set = rules.select_fans(d.fan_count, best.fan_set, self.cfg)
        chosen = rules.evaluate(fan_set, d.ac_on, d.ac_temp, temperature, humidity, people, self.cfg, *natural)
        if best.comfortable and chosen.feels_like > self.cfg.comfort_max + 1.0:
            logger.warning("Gemini chọn %s (cảm nhận %.1f°C) quá nóng → dùng phương án planner",
                           chosen.describe(), chosen.feels_like)
            return Decision(best.fans, best.ac_on, best.ac_temp if best.ac_on else d.ac_temp,
                            d.ai_reason + f" (Hệ thống điều chỉnh: {best.describe()} để đủ mát.)",
                            "GEMINI", mode_label=best.label)
        return d

    def _constraint_notes(self, current: dict) -> list[str]:
        notes = []
        if self._ac_changed_at is not None:
            elapsed = time.monotonic() - self._ac_changed_at
            if current["ac_on"] and elapsed < self.s.ac_min_on_seconds:
                notes.append(f"điều hòa vừa bật {elapsed / 60:.0f} phút, chưa nên tắt "
                             f"(tối thiểu {self.s.ac_min_on_seconds / 60:.0f} phút)")
            if not current["ac_on"] and elapsed < self.s.ac_min_off_seconds:
                notes.append(f"điều hòa vừa tắt {elapsed / 60:.0f} phút, chưa nên bật lại "
                             f"(tối thiểu {self.s.ac_min_off_seconds / 60:.0f} phút) — dùng quạt tạm")
        return notes

    def _check_external_change(self, current: dict) -> None:
        """Quạt/AC bị đổi mà không phải do ai_service gửi (đang AUTO) → thường là AI nội bộ của backend
        (AI_ENGINE_ENABLED=true) ghi đè theo nhiệt độ THẬT của cảm biến: quạt/AC lệch nhau, AC bị trễ."""
        expected = self._expected_device
        if expected is None:
            return
        now_dev = (tuple(current["fans"]), current["ac_on"], current["ac_temp"] if current["ac_on"] else expected[2])
        exp_dev = (expected[0], expected[1], expected[2])
        if now_dev != exp_dev:
            self._expected_device = None
            if time.monotonic() - self._last_external_warn > 30:
                self._last_external_warn = time.monotonic()
                logger.warning("Quạt/điều hòa bị thay đổi bởi bên khác (không phải ai_service): mong đợi %s, "
                               "backend đang là %s. Nếu không ai bấm tay trên app thì AI nội bộ của backend "
                               "đang ghi đè → đặt AI_ENGINE_ENABLED=false trong backend/.env rồi khởi động lại.",
                               _dev_text(exp_dev), _dev_text(now_dev))
                events.alert("external", "warn", "Thiết bị bị đổi bởi bên khác",
                             f"AI gửi {_dev_text(exp_dev)}, backend đang {_dev_text(now_dev)}.",
                             "Nếu không ai bấm trên app: đặt AI_ENGINE_ENABLED=false trong backend/.env.")

    def _apply_ac_timing(self, d: Decision, current: dict) -> Optional[float]:
        """Bảo vệ máy nén: không đổi bật/tắt AC khi chưa đủ thời gian tối thiểu.
        Trả về số giây còn phải chờ nếu phải hoãn, None nếu không."""
        if self._ac_changed_at is None or d.ac_on == current["ac_on"]:
            return None
        elapsed = time.monotonic() - self._ac_changed_at
        if current["ac_on"] and elapsed < self.s.ac_min_on_seconds and d.source != "AUTO":
            d.ac_on, d.ac_temp = True, current["ac_temp"]
            d.ai_reason += " (Giữ điều hòa: chưa đủ thời gian chạy tối thiểu.)"
            self._recheck_at = self._ac_changed_at + self.s.ac_min_on_seconds
        elif not current["ac_on"] and elapsed < self.s.ac_min_off_seconds:
            d.ac_on, d.fan_count = False, self.cfg.max_fans
            wait = self.s.ac_min_off_seconds - elapsed
            d.ai_reason += f" (Tạm bật hết quạt: điều hòa vừa tắt, {wait:.0f}s nữa mới được bật lại.)"
            self._recheck_at = self._ac_changed_at + self.s.ac_min_off_seconds
        else:
            return None
        wait = max(0.0, self._recheck_at - time.monotonic())
        logger.info("Bảo vệ máy nén: hoãn đổi điều hòa, sẽ quyết định lại sau %.0fs", wait)
        d.mode_label = ""
        return round(wait)

    def _remember(self, inputs: dict) -> None:
        self._last_inputs = dict(inputs)

    def _write_log(self, why, inputs, current, best, steady, decision, latency, llm_error, sent, send_error):
        if not self._log_path:
            return
        base = rules.baseline_power(inputs["temperature"], inputs["humidity"], inputs["people"] or 0, self.cfg)
        record = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "trigger": why,
            "people": inputs["people"],
            "temperature": inputs["temperature"],
            "humidity": inputs["humidity"],
            "before": {"fans": list(current["fans"]), "ac_on": current["ac_on"], "ac_temp": current["ac_temp"]},
            "planner": {"fans": best.fans, "ac_on": best.ac_on, "ac_temp": best.ac_temp,
                        "label": best.label, "power_w": best.power_w,
                        "steady_label": steady.label, "steady_power_w": steady.power_w},
            "decision": {**asdict(decision), "fans": list(decision.fans)},
            "baseline_ac_only_w": base,
            "profile": self.profile.to_dict(),
            "simulation": getattr(self.s, "simulation", None),   # tên kịch bản khi chạy simulate.py
            "model": self.llm.last_model if (self.llm and decision.source == "GEMINI") else None,
            "llm_latency_s": latency,
            "llm_error": llm_error,
            "sent": sent,
            "send_error": send_error,
        }
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.debug("Không ghi được log quyết định: %s", exc)

    # ---------- vòng lặp ----------
    def run_loop(self, stop_event: threading.Event, once: bool = False) -> Optional[Decision]:
        logger.info("Bộ quyết định chạy: kiểm tra mỗi %.0fs, gọi LLM cách nhau %.0f–%.0fs, LLM=%s",
                    self.s.decision_check_interval, self.s.llm_min_interval, self.s.llm_max_interval,
                    ",".join(self.llm.models) if self.llm else "TẮT (chỉ dùng planner)")
        logger.info("Hồ sơ thiết bị: %s (nguồn: %s)", self.profile.to_dict(),
                    {k: v for k, v in self.profile.sources.items() if v != "default"} or "mặc định")
        result = None
        while not stop_event.is_set():
            try:
                result = self.step()
            except Exception:  # không để 1 lỗi bất ngờ làm chết cả service
                logger.exception("Lỗi không mong muốn trong bộ quyết định")
            if once:
                break
            stop_event.wait(self.s.decision_check_interval)
        return result
