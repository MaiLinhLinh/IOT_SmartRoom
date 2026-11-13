"""
tests/test_logic.py — Test phần logic không cần camera/Gemini thật.

Chạy:  python -m pytest tests -q      (hoặc: python tests/test_logic.py)
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings  # noqa: E402
from decision_engine import DecisionEngine, validate_decision  # noqa: E402
from llm_client import GeminiClient, LLMError, extract_json  # noqa: E402
from people_counter import CountSmoother  # noqa: E402
from rules import rule_decision  # noqa: E402


# ---------------- rules ----------------
def test_rules_empty_room_turns_off():
    d = rule_decision(32, 70, 0, {"ac_temp": 26})
    assert d["fan_level"] == 0 and d["ac_on"] is False


def test_rules_unknown_people_keeps_state():
    assert rule_decision(32, 70, None, {"ac_temp": 26}) is None


def test_rules_levels():
    assert rule_decision(24, 50, 2, {})["fan_level"] == 0
    assert rule_decision(26, 50, 2, {})["fan_level"] == 1
    hot = rule_decision(29.5, 50, 2, {})
    assert hot["fan_level"] == 2 and hot["ac_on"] is True
    very_hot = rule_decision(32, 50, 2, {})
    assert very_hot["fan_level"] == 3 and very_hot["ac_on"] is True
    # 27.5°C nhưng độ ẩm 75% → cảm nhận 28.5 → bật AC
    assert rule_decision(27.5, 75, 2, {})["ac_on"] is True


# ---------------- smoother ----------------
def test_smoother_median_filters_flicker():
    s = CountSmoother(5)
    for v in [3, 3, 3]:
        s.add(v)
    assert s.add(0) == 3   # 1 khung YOLO bỏ sót không làm số người tụt về 0
    assert s.add(3) == 3


# ---------------- JSON / validate ----------------
def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('Kết quả: {"a": 3} xong') == {"a": 3}


def test_validate_clamps_and_types():
    d = validate_decision({"fan_level": "5", "ac_on": "true", "ac_temp": 10, "ai_reason": "x"}, 26, 20, 30)
    assert d == {"fan_level": 3, "ac_on": True, "ac_temp": 20, "ai_reason": "x"}
    d = validate_decision({"fan_level": 1, "ac_on": False, "ac_temp": 18}, 27, 20, 30)
    assert d["ac_temp"] == 27 and d["ai_reason"]


def test_validate_missing_field():
    try:
        validate_decision({"fan_level": 1}, 26, 20, 30)
    except ValueError:
        return
    raise AssertionError("phải báo thiếu trường")


# ---------------- Gemini client (giả lập HTTP) ----------------
class FakeResp:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.ok = 200 <= status < 300
        self.text = str(payload)

    def json(self):
        return self._payload


def _ok(text):
    return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]})


def test_gemini_success_and_model_fallback():
    client = GeminiClient("k", ["m1", "m2"])
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw["json"]))
        if "m1" in url:
            return FakeResp(429, {"error": {"message": "quota"}})
        return _ok('{"fan_level": 2, "ac_on": true, "ac_temp": 25, "ai_reason": "ok"}')

    with mock.patch.object(client.session, "post", side_effect=fake_post):
        out = client.generate_json("sys", "user", {"type": "OBJECT"})
    assert out["fan_level"] == 2 and client.last_model == "m2"
    body = calls[-1][1]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"


def test_gemini_drops_unsupported_thinking_and_schema():
    client = GeminiClient("k", ["m1"])
    bodies = []

    def fake_post(url, **kw):
        cfg = kw["json"]["generationConfig"]
        bodies.append(dict(cfg))
        if "thinkingConfig" in cfg:
            return FakeResp(400, {"error": {"message": "Thinking level is not supported for this model."}})
        if "responseSchema" in cfg:
            return FakeResp(400, {"error": {"message": "Invalid JSON payload: unknown field in response_schema"}})
        return _ok('{"fan_level": 1, "ac_on": false, "ac_temp": 26, "ai_reason": "ok"}')

    with mock.patch.object(client.session, "post", side_effect=fake_post):
        out = client.generate_json("s", "u", {"type": "OBJECT"})
    assert out["fan_level"] == 1 and len(bodies) == 3


def test_gemini_skips_thought_parts():
    client = GeminiClient("k", ["m1"])
    resp = FakeResp(200, {"candidates": [{"content": {"parts": [
        {"text": "suy nghĩ...", "thought": True},
        {"text": '{"fan_level": 0, "ac_on": false, "ac_temp": 26, "ai_reason": "ok"}'}]}}]})
    with mock.patch.object(client.session, "post", return_value=resp):
        assert client.generate_json("s", "u")["fan_level"] == 0


def test_gemini_all_fail_raises():
    client = GeminiClient("k", ["m1"])
    with mock.patch.object(client.session, "post", return_value=FakeResp(429, {})):
        try:
            client.generate_json("s", "u")
        except LLMError:
            return
    raise AssertionError("phải ném LLMError")


# ---------------- DecisionEngine (backend giả) ----------------
class FakeBackend:
    def __init__(self, ctx):
        self.ctx = ctx
        self.sent = []

    def get_context(self):
        return dict(self.ctx)

    def send_command(self, fan_level, ac_on, ac_temp, ai_reason):
        self.sent.append((fan_level, ac_on, ac_temp, ai_reason))
        self.ctx.update(fan_level=fan_level, ac_on=ac_on, ac_temp=ac_temp, ai_reason=ai_reason)
        return self.ctx


def _ctx(**kw):
    from datetime import datetime
    now = datetime.now().isoformat()
    base = {"mode": "AUTO", "fan_level": 0, "ac_on": False, "ac_temp": 26, "people_count": 3,
            "people_source": "YOLO", "people_updated_at": now, "temperature": 30.0, "humidity": 70.0,
            "sensor_timestamp": now, "ai_reason": None}
    base.update(kw)
    return base


def _engine(backend, llm=None):
    s = Settings()
    s.decision_log_path = ""
    s.llm_min_interval = 0
    s.dry_run = False
    return DecisionEngine(s, backend, llm)


def test_engine_falls_back_to_rules_when_llm_fails():
    llm = mock.Mock()
    llm.generate_json.side_effect = LLMError("quota")
    llm.last_model = None
    be = FakeBackend(_ctx())
    d = _engine(be, llm).step()
    assert d.source == "RULE" and be.sent and be.sent[0][3].startswith("[Luật dự phòng]")


def test_engine_uses_llm_and_does_not_repeat_without_change():
    llm = mock.Mock()
    llm.generate_json.return_value = {"fan_level": 2, "ac_on": True, "ac_temp": 25, "ai_reason": "nóng"}
    llm.last_model = "m"
    be = FakeBackend(_ctx())
    eng = _engine(be, llm)
    assert eng.step().source == "GEMINI"
    assert be.sent[0][:3] == (2, True, 25) and be.sent[0][3].startswith("[Gemini]")
    assert eng.step() is None            # không đổi gì → không gọi lại
    assert llm.generate_json.call_count == 1
    be.ctx["people_count"] = 6           # số người đổi → gọi lại
    eng.step()
    assert llm.generate_json.call_count == 2


def test_engine_guardrail_empty_room():
    llm = mock.Mock()
    llm.generate_json.return_value = {"fan_level": 1, "ac_on": True, "ac_temp": 26, "ai_reason": "x"}
    llm.last_model = "m"
    be = FakeBackend(_ctx(people_count=0, fan_level=2, ac_on=True))
    _engine(be, llm).step()
    assert be.sent[0][:2] == (0, False)


def test_engine_skips_manual_and_stale_sensor():
    be = FakeBackend(_ctx(mode="MANUAL"))
    assert _engine(be).step() is None and not be.sent
    be = FakeBackend(_ctx(sensor_timestamp="2020-01-01T00:00:00"))
    assert _engine(be).step() is None and not be.sent


def test_engine_stale_people_keeps_state_with_rules():
    be = FakeBackend(_ctx(people_updated_at="2020-01-01T00:00:00", fan_level=2))
    assert _engine(be).step() is None and not be.sent


if __name__ == "__main__":
    import inspect
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and inspect.isfunction(f)]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")
