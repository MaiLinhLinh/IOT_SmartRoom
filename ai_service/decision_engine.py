"""
decision_engine.py — Chức năng 2: gọi LLM (Gemini) phân tích và đưa ra lệnh điều khiển.

Mỗi DECISION_CHECK_SECONDS (mặc định 5s):
  1. GET /api/ai/context  → mode, nhiệt độ, độ ẩm, số người, trạng thái thiết bị.
  2. Chỉ xử lý khi mode = AUTO và dữ liệu cảm biến còn mới.
  3. Chỉ gọi LLM khi có thay đổi đáng kể (số người đổi, nhiệt độ lệch ≥ TEMP_DELTA,
     độ ẩm lệch ≥ HUMIDITY_DELTA...) hoặc đã quá LLM_MAX_INTERVAL_SECONDS,
     và cách lần gọi trước ≥ LLM_MIN_INTERVAL_SECONDS → tiết kiệm quota miễn phí.
  4. Gemini trả JSON → kiểm tra/kẹp giá trị → POST /api/ai/command.
     Gemini lỗi / hết quota / JSON sai → dùng luật ngưỡng dự phòng (rules.py).
  5. Ghi mỗi quyết định ra logs/decisions.jsonl (phục vụ báo cáo, đánh giá).
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
from rules import rule_decision

logger = logging.getLogger("decision")


@dataclass
class Decision:
    fan_level: int
    ac_on: bool
    ac_temp: int
    ai_reason: str
    source: str  # "GEMINI" | "RULE"


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


def validate_decision(raw: dict, current_ac_temp: int, ac_min: int, ac_max: int) -> dict:
    """Kiểm tra JSON của LLM, kẹp về khoảng hợp lệ. Thiếu field → ValueError."""
    missing = [k for k in ("fan_level", "ac_on", "ac_temp") if k not in raw]
    if missing:
        raise ValueError(f"LLM thiếu trường {missing}")

    fan = int(round(float(raw["fan_level"])))
    fan = min(3, max(0, fan))
    ac_on = _to_bool(raw["ac_on"])
    try:
        ac_temp = int(round(float(raw["ac_temp"])))
    except (TypeError, ValueError):
        ac_temp = current_ac_temp
    ac_temp = min(ac_max, max(ac_min, ac_temp))
    if not ac_on:
        ac_temp = min(ac_max, max(ac_min, current_ac_temp))  # tắt AC thì giữ giá trị đặt cũ

    reason = str(raw.get("ai_reason") or "").strip().replace("\n", " ")
    if not reason:
        reason = "Gemini không ghi lý do."
    return {"fan_level": fan, "ac_on": ac_on, "ac_temp": ac_temp, "ai_reason": reason[:300]}


# ----------------------------------------------------------------------
# Bộ quyết định
# ----------------------------------------------------------------------
class DecisionEngine:
    def __init__(self, settings: Settings, backend: BackendClient, llm: Optional[GeminiClient]):
        self.s = settings
        self.backend = backend
        self.llm = llm
        self.system_prompt = build_system_prompt(settings.ac_min_temp, settings.ac_max_temp)
        self._last_inputs: Optional[dict] = None
        self._last_call: float = 0.0
        self._log_path = Path(settings.decision_log_path) if settings.decision_log_path else None
        if self._log_path and not self._log_path.is_absolute():
            self._log_path = Path(__file__).resolve().parent / self._log_path

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
        if inputs["device"] != last["device"]:
            return "trạng thái thiết bị bị thay đổi từ bên ngoài"
        if time.monotonic() - self._last_call >= self.s.llm_max_interval:
            return "đánh giá lại định kỳ"
        return None

    # ---------- một bước ----------
    def step(self) -> Optional[Decision]:
        try:
            ctx = self.backend.get_context()
        except BackendError as exc:
            logger.warning("Không lấy được context: %s", exc)
            return None

        if ctx.get("mode") != "AUTO":
            if self._last_inputs is not None:
                logger.info("Thiết bị chuyển sang %s → tạm dừng AI", ctx.get("mode"))
            self._last_inputs = None
            return None

        temperature = ctx.get("temperature")
        if temperature is None:
            logger.info("Chưa có dữ liệu nhiệt độ từ ESP32 → bỏ qua")
            return None
        sensor_age = age_seconds(ctx.get("sensor_timestamp"))
        if sensor_age is not None and sensor_age > self.s.sensor_stale_seconds:
            logger.warning("Dữ liệu cảm biến đã cũ %.0fs → không ra quyết định", sensor_age)
            return None

        people_age = age_seconds(ctx.get("people_updated_at"))
        people_fresh = people_age is not None and people_age <= self.s.people_stale_seconds
        people = int(ctx.get("people_count") or 0) if people_fresh else None

        humidity = ctx.get("humidity")
        current = {
            "fan_level": int(ctx.get("fan_level") or 0),
            "ac_on": bool(ctx.get("ac_on")),
            "ac_temp": int(ctx.get("ac_temp") or 26),
        }
        inputs = {
            "people": people,
            "temperature": float(temperature),
            "humidity": float(humidity) if humidity is not None else None,
            "device": (current["fan_level"], current["ac_on"], current["ac_temp"]),
        }

        why = self._trigger_reason(inputs)
        if why is None:
            return None
        if time.monotonic() - self._last_call < self.s.llm_min_interval:
            return None  # giữ trigger, xử lý ở vòng sau

        logger.info("Ra quyết định (%s): người=%s T=%.1f H=%s | hiện tại %s",
                    why, people if people is not None else "?", inputs["temperature"],
                    humidity, current)
        self._last_call = time.monotonic()

        decision, latency, llm_error = self._decide(ctx, inputs, current, people_age, sensor_age)

        if decision is None:
            logger.info("Giữ nguyên trạng thái (số người không rõ, Gemini không khả dụng)")
            self._remember(inputs, current)
            return None

        # Guardrail: phòng trống chắc chắn → luôn tắt hết
        if people == 0 and (decision.fan_level > 0 or decision.ac_on):
            decision.fan_level, decision.ac_on = 0, False
            decision.ac_temp = current["ac_temp"]
            decision.ai_reason += " (Hệ thống: phòng trống nên tắt toàn bộ.)"

        prefix = "[Gemini] " if decision.source == "GEMINI" else "[Luật dự phòng] "
        reason = (prefix + decision.ai_reason)[:500]

        sent, send_error = False, None
        if self.s.dry_run:
            logger.info("DRY-RUN, không gửi: %s", decision)
        else:
            try:
                self.backend.send_command(decision.fan_level, decision.ac_on, decision.ac_temp, reason)
                sent = True
                logger.info("→ Đã gửi lệnh: quạt=%d AC=%s %d°C | %s", decision.fan_level,
                            "ON" if decision.ac_on else "OFF", decision.ac_temp, reason)
            except ModeIsManual:
                send_error = "MANUAL"
                logger.info("Người dùng vừa chuyển MANUAL → bỏ lệnh")
            except BackendError as exc:
                send_error = str(exc)
                logger.warning("Không gửi được lệnh: %s", exc)

        new_device = (decision.fan_level, decision.ac_on, decision.ac_temp) if sent else inputs["device"]
        self._remember(inputs, dict(zip(("fan_level", "ac_on", "ac_temp"), new_device)))
        self._write_log(ctx, inputs, why, decision, latency, llm_error, sent, send_error)
        return decision

    # ---------- LLM hoặc luật ----------
    def _decide(self, ctx, inputs, current, people_age, sensor_age):
        llm_error = None
        latency = None
        if self.llm is not None:
            user_prompt = build_user_prompt(
                people_count=inputs["people"],
                people_age=people_age,
                people_source=ctx.get("people_source") or "NONE",
                temperature=inputs["temperature"],
                humidity=inputs["humidity"],
                sensor_age=sensor_age,
                fan_level=current["fan_level"],
                ac_on=current["ac_on"],
                ac_temp=current["ac_temp"],
                last_reason=ctx.get("ai_reason"),
            )
            started = time.monotonic()
            try:
                raw = self.llm.generate_json(self.system_prompt, user_prompt, RESPONSE_SCHEMA)
                latency = round(time.monotonic() - started, 2)
                checked = validate_decision(raw, current["ac_temp"], self.s.ac_min_temp, self.s.ac_max_temp)
                logger.info("Gemini (%s, %.1fs): %s", self.llm.last_model, latency, raw)
                return Decision(**checked, source="GEMINI"), latency, None
            except (LLMError, ValueError, TypeError) as exc:
                latency = round(time.monotonic() - started, 2)
                llm_error = str(exc)
                logger.warning("Gemini lỗi → dùng luật dự phòng: %s", exc)

        ruled = rule_decision(inputs["temperature"], inputs["humidity"], inputs["people"], current)
        if ruled is None:
            return None, latency, llm_error
        ruled["ac_temp"] = min(self.s.ac_max_temp, max(self.s.ac_min_temp, ruled["ac_temp"]))
        return Decision(**ruled, source="RULE"), latency, llm_error

    def _remember(self, inputs: dict, device: dict) -> None:
        self._last_inputs = {**inputs, "device": (device["fan_level"], device["ac_on"], device["ac_temp"])}

    def _write_log(self, ctx, inputs, why, decision, latency, llm_error, sent, send_error) -> None:
        if not self._log_path:
            return
        record = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "trigger": why,
            "people": inputs["people"],
            "temperature": inputs["temperature"],
            "humidity": inputs["humidity"],
            "before": {"fan_level": ctx.get("fan_level"), "ac_on": ctx.get("ac_on"), "ac_temp": ctx.get("ac_temp")},
            "decision": asdict(decision),
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
                    ",".join(self.llm.models) if self.llm else "TẮT (chỉ dùng luật)")
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
