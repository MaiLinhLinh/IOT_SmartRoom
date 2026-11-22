"""
tests/test_logic.py — Test phần logic không cần camera/Gemini thật.

Chạy:  python -m pytest tests -q      (hoặc: python tests/test_logic.py)
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings  # noqa: E402
from decision_engine import DecisionEngine, parse_fans, validate_decision  # noqa: E402
import device_profile  # noqa: E402
from llm_client import GeminiClient, LLMError, extract_json  # noqa: E402
from people_counter import CountSmoother  # noqa: E402
import rules  # noqa: E402

CFG = rules.PlannerConfig()
OFF = {"fan_count": 0, "ac_on": False, "ac_temp": 26}


def _plan(t, rh, n, current=OFF, natural=None):
    best, steady, _ = rules.plan(t, rh, n, current, CFG, natural)
    return best, steady


# ---------------- planner: 3 vùng chỉ quạt / kết hợp / chỉ điều hòa ----------------
def test_comfortable_room_all_off():
    best, _ = _plan(25.5, 60, 2)
    assert (best.fans, best.ac_on) == (0, False)


def test_fan_only_zone_uses_fewest_fans():
    for t, n in [(27.0, 1), (28.5, 2), (29.0, 3)]:
        best = _plan(t, 60, 2)[0]
        assert (best.fans, best.ac_on, best.label) == (n, False, "chỉ quạt")


def test_combined_zone_raises_setpoint_with_fans():
    _, steady = _plan(30.0, 60, 2)
    assert steady.label == "kết hợp" and steady.ac_temp >= 27 and steady.fans >= 1
    assert steady.power_w < rules.baseline_power(30.0, 60, 2, CFG)  # rẻ hơn "chỉ AC 25°C"


def test_ac_only_zone_when_very_humid():
    _, steady = _plan(27.0, 82, 2)
    assert steady.label == "chỉ điều hòa"


def test_pulldown_boosts_fans_when_room_far_above_setpoint():
    best, steady = _plan(31.0, 60, 2)
    assert best.label == "làm mát nhanh" and best.fans == 3 and steady.fans < 3


def test_ac_running_not_turned_off_without_natural_temp():
    running = {"fan_count": 1, "ac_on": True, "ac_temp": 27}
    assert _plan(27.0, 65, 2, running)[0].ac_on is True             # nhiệt độ đo bị AC kéo xuống
    assert _plan(27.0, 65, 2, running, natural=27.2)[0].ac_on is False  # biết nhiệt độ tự nhiên → tắt được
    assert _plan(25.8, 65, 2, running)[0].ac_on is False            # phòng mát hơn Tset 1°C → tắt


def test_hysteresis_keeps_fan_near_threshold():
    one_fan = {"fan_count": 1, "ac_on": False, "ac_temp": 26}
    assert _plan(26.2, 60, 2, one_fan)[0].fans == 1   # chưa giảm đủ 0.5°C dưới ngưỡng → giữ quạt
    assert _plan(25.4, 60, 2, one_fan)[0].fans == 0


def test_select_fans_minimal_toggles():
    sel = lambda n, cur: rules.select_fans(n, cur, CFG)  # noqa: E731
    assert sel(2, (False, False, False)) == (True, True, False)
    assert sel(2, (False, False, True)) == (True, False, True)   # giữ quạt 3 đang chạy
    assert sel(1, (True, True, True)) == (True, False, False)
    assert sel(0, (True, False, True)) == (False, False, False)


def test_select_fans_prefers_low_power_and_skips_unavailable():
    cfg = rules.PlannerConfig(fan_powers=(60.0, 30.0, 45.0), fans_available=(True, True, False))
    assert rules.select_fans(1, (False, False, False), cfg) == (False, True, False)  # quạt 2 rẻ nhất
    assert rules.select_fans(3, (False, False, False), cfg) == (True, True, False)   # quạt 3 hỏng
    assert cfg.max_fans == 2


def test_room_without_ac_only_uses_fans():
    cfg = rules.PlannerConfig(ac_available=False)
    best, _, options = rules.plan(32.0, 70, 4, OFF, cfg)
    assert not any(o.ac_on for o in options)
    assert best.fans == 3 and not best.ac_on   # không đủ mát → bật tối đa quạt


def test_ac_only_when_no_fans_available():
    cfg = rules.PlannerConfig(fans_available=(False, False, False))
    best, _, _ = rules.plan(30.0, 60, 2, OFF, cfg)
    assert best.ac_on and best.fans == 0


def test_higher_ac_power_shifts_toward_fans():
    # AC tốn điện hơn → kết hợp có lợi hơn; AC rất tiết kiệm → có thể chỉ cần AC
    big = rules.PlannerConfig(ac_power_w=2000.0)
    small = rules.PlannerConfig(ac_power_w=300.0)
    assert rules.plan(30.0, 60, 2, OFF, big)[1].fans >= 1
    assert rules.plan(30.0, 60, 2, OFF, small)[1].fans == 0


# ---------------- hồ sơ thiết bị ----------------
def test_profile_defaults_env_and_backend_override():
    p, warn = device_profile.from_env({})
    assert p.fan_power_w == [45.0] * 3 and p.ac_power_w == 900.0 and not warn
    p, warn = device_profile.from_env({"FAN_POWER_W": "60", "FANS_AVAILABLE": "1,0,1", "AC_POWER_W": "1200"})
    assert p.fan_power_w == [60.0] * 3 and p.fans_available == [True, False, True] and p.sources["ac_power_w"] == "env"
    warn = p.apply({"ac_power_w": 50000, "comfort_max_temp": "27", "unknown": 1, "fan_power_w": [40, 50]}, "backend")
    assert p.ac_power_w == 1200.0 and p.comfort_max_temp == 27.0 and len(warn) == 2   # ngoài khoảng + sai số lượng
    assert {f["name"] for f in device_profile.schema()} >= {"fan_power_w", "ac_power_w", "comfort_max_temp"}


def test_parse_fans_v4_and_v3():
    assert parse_fans({"fan1": True, "fan2": False, "fan3": True}) == (True, False, True)
    assert parse_fans({"fan_level": 2}) == (True, True, False)


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
    d = validate_decision({"fan_count": "5", "ac_on": "true", "ac_temp": 10, "ai_reason": "x"}, 26, 20, 30)
    assert d == {"fan_count": 3, "ac_on": True, "ac_temp": 20, "ai_reason": "x"}
    d = validate_decision({"fan_count": 1, "ac_on": False, "ac_temp": 18}, 27, 20, 30)
    assert d["ac_temp"] == 27 and d["ai_reason"]
    d = validate_decision({"fan1": True, "fan2": False, "fan3": True, "ac_on": False}, 26, 20, 30)
    assert d["fan_count"] == 2


def test_validate_missing_field():
    try:
        validate_decision({"ac_on": True}, 26, 20, 30)
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

    def send_command(self, fans, ac_on, ac_temp, ai_reason):
        self.sent.append((tuple(fans), ac_on, ac_temp, ai_reason))
        self.ctx.update(fan1=fans[0], fan2=fans[1], fan3=fans[2], ac_on=ac_on, ac_temp=ac_temp, ai_reason=ai_reason)
        return self.ctx


def _ctx(**kw):
    from datetime import datetime
    now = datetime.now().isoformat()
    base = {"mode": "AUTO", "fan1": False, "fan2": False, "fan3": False, "ac_on": False, "ac_temp": 26,
            "people_count": 2, "people_source": "YOLO", "people_updated_at": now, "temperature": 30.0,
            "humidity": 60.0, "sensor_timestamp": now, "ai_reason": None}
    base.update(kw)
    return base


def _engine(backend, llm=None, **overrides):
    s = Settings()
    s.decision_log_path = ""
    s.llm_min_interval = 0
    s.dry_run = False
    for k, v in overrides.items():
        setattr(s, k, v)
    return DecisionEngine(s, backend, llm)


def _llm(answer):
    llm = mock.Mock()
    llm.generate_json.return_value = answer
    llm.last_model = "m"
    llm.models = ["m"]
    return llm


def test_engine_falls_back_to_planner_when_llm_fails():
    llm = _llm(None)
    llm.generate_json.side_effect = LLMError("quota")
    be = FakeBackend(_ctx(temperature=28.5))
    d = _engine(be, llm).step()
    assert d.source == "RULE" and be.sent[0][:2] == ((True, True, False), False)
    assert be.sent[0][3].startswith("[Luật dự phòng]") and "W)" in be.sent[0][3]


def test_engine_uses_llm_v4_payload_and_does_not_repeat():
    llm = _llm({"fan_count": 2, "ac_on": True, "ac_temp": 28, "ai_reason": "kết hợp"})
    be = FakeBackend(_ctx())
    eng = _engine(be, llm)
    assert eng.step().source == "GEMINI"
    assert be.sent[0][:3] == ((True, True, False), True, 28) and be.sent[0][3].startswith("[Gemini]")
    prompt = llm.generate_json.call_args[0][1]
    assert "BẢNG PHƯƠNG ÁN" in prompt and "PHƯƠNG ÁN ĐỀ XUẤT" in prompt
    assert eng.step() is None and llm.generate_json.call_count == 1   # không đổi → không gọi lại
    be.ctx["people_count"] = 6
    eng.step()
    assert llm.generate_json.call_count == 2


def test_engine_comfort_guard_overrides_too_hot_choice():
    llm = _llm({"fan_count": 0, "ac_on": False, "ac_temp": 26, "ai_reason": "tắt hết"})
    be = FakeBackend(_ctx(temperature=31.0))
    _engine(be, llm).step()
    assert be.sent[0][1] is True   # vẫn bật AC dù Gemini chọn tắt hết


def test_engine_empty_room_no_llm_and_ac_grace():
    llm = _llm({"fan_count": 3, "ac_on": True, "ac_temp": 24, "ai_reason": "x"})
    be = FakeBackend(_ctx(people_count=0, fan1=True, fan2=True, ac_on=True, ac_temp=26))
    eng = _engine(be, llm, empty_ac_grace_seconds=180)
    d = eng.step()
    assert llm.generate_json.call_count == 0
    assert d.source == "AUTO" and be.sent[0][:3] == ((False, False, False), True, 28)  # tắt quạt, giữ AC 28°C
    eng._empty_since -= 200                                                        # giả lập đã trống 200s
    d = eng.step()
    assert be.sent[-1][:2] == ((False, False, False), False)


def test_engine_ac_min_off_time_uses_fans_instead():
    llm = _llm({"fan_count": 2, "ac_on": True, "ac_temp": 28, "ai_reason": "bật AC"})
    be = FakeBackend(_ctx())
    eng = _engine(be, llm, ac_min_off_seconds=300)
    eng._last_seen_ac, eng._ac_changed_at = False, __import__("time").monotonic() - 30  # AC vừa tắt 30s
    eng.step()
    assert be.sent[0][:2] == ((True, True, True), False)


def test_engine_skips_manual_stale_sensor_and_unknown_people():
    be = FakeBackend(_ctx(mode="MANUAL"))
    assert _engine(be).step() is None and not be.sent
    be = FakeBackend(_ctx(sensor_timestamp="2020-01-01T00:00:00"))
    assert _engine(be).step() is None and not be.sent
    be = FakeBackend(_ctx(people_updated_at="2020-01-01T00:00:00", fan1=True))
    assert _engine(be).step() is None and not be.sent


def test_engine_skips_llm_when_state_already_optimal():
    llm = _llm({"fan_count": 1, "ac_on": False, "ac_temp": 26, "ai_reason": "x"})
    be = FakeBackend(_ctx(temperature=27.0, fan1=True))   # 1 quạt đang chạy là tối ưu ở 27°C
    eng = _engine(be, llm)
    eng._last_inputs = {"people": 2, "temperature": 26.4, "humidity": 60.0,
                        "device": ((True, False, False), False, 26)}   # nhiệt độ đổi nhẹ
    assert eng.step() is None and llm.generate_json.call_count == 0 and not be.sent


def test_engine_reads_profile_from_backend_config():
    llm = _llm(None)
    llm.generate_json.side_effect = LLMError("x")
    be = FakeBackend(_ctx(temperature=31.0, config={"ac_available": False, "fan_power_w": [50, 40, 70]}))
    eng = _engine(be, llm)
    eng.step()
    assert eng.profile.sources["ac_available"] == "backend:config"
    assert be.sent[0][1] is False and be.sent[0][0] == (True, True, True)   # không AC → bật hết quạt
    assert "không có điều hòa" in eng.system_prompt.lower() or "KHÔNG có điều hòa" in eng.system_prompt


# ---------------- bộ giả lập demo ----------------
def test_simulator_offline_scenarios_reach_expected_modes():
    import simulator
    for name in ("chi-quat", "chi-dieu-hoa"):
        _, _, steps = simulator.load_scenario(name)
        s = Settings()
        s.decision_log_path, s.llm_min_interval, s.decision_check_interval = "", 0, 0.05
        sim = simulator.SimulatedBackend(simulator.InMemoryBackend(), "offline")
        eng = DecisionEngine(s, sim, None)
        results = simulator.run_scenario(eng, sim, steps, speed=100, tick=0.05, printer=lambda *_: None)
        assert all(r.ok is not False for r in results), [(r.name, r.mode_label, r.expect) for r in results]
        assert all(c["ai_reason"].startswith("[Mô phỏng]") for c in sim.sent)


def test_simulator_dry_run_keeps_virtual_state_without_sending():
    import simulator
    inner = simulator.InMemoryBackend()
    sim = simulator.SimulatedBackend(inner, "offline", dry_run=True)
    sim.set_values(30.0, 60.0, 2)
    sim.send_command((True, True, False), True, 28, "x")
    assert inner.commands == [] and sim.get_context()["ac_on"] is True


def test_custom_scenario_file_loads():
    import simulator
    name, title, steps = simulator.load_scenario(str(Path(__file__).resolve().parents[1] / "scenarios/vi_du_tu_tao.json"))
    assert len(steps) == 3 and steps[1].values_at(0.5) == (30.0, 58.0)


def test_profile_from_backend_v5_device_info():
    items = [
        {"device_key": "fan1", "power_watts": 45.0, "power_is_default": True},
        {"device_key": "fan2", "power_watts": 60.0, "power_is_default": False},
        {"device_key": "fan3", "power_watts": 45.0, "power_is_default": True},
        {"device_key": "ac", "power_watts": 1200.0, "power_is_default": False},
    ]
    data = device_profile.from_device_info(items)
    assert data == {"fan_power_w": [None, 60.0, None], "ac_power_w": 1200.0}
    p, _ = device_profile.from_env({"FAN_POWER_W": "40"})
    assert not p.apply(data, "backend:devices/info")
    assert p.fan_power_w == [40.0, 60.0, 40.0] and p.ac_power_w == 1200.0   # quạt chưa nhập giữ .env
    assert device_profile.from_device_info([{"device_key": "ac", "power_is_default": True,
                                             "power_watts": 900}]) == {}


def test_engine_reads_power_from_devices_info_api():
    be = FakeBackend(_ctx(temperature=30.0, humidity=72.0))
    be.get_device_info = lambda: [{"device_key": "ac", "power_watts": 300.0, "power_is_default": False}]
    eng = _engine(be)
    eng.step()
    assert eng.profile.ac_power_w == 300.0 and eng.profile.sources["ac_power_w"] == "backend:devices/info"
    assert be.sent[-1][1] is True
    best, steady, _ = rules.plan(30.0, 72.0, 2, OFF, eng.cfg)
    assert steady.label == "chỉ điều hòa"   # AC inverter rất tiết kiệm → không cần kèm quạt


if __name__ == "__main__":
    import inspect
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and inspect.isfunction(f)]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")
