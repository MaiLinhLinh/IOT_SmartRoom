"""
tests/e2e_scenario.py — Kịch bản end-to-end qua HTTP thật với tests/mock_backend.py
(backend giả + Gemini giả), kèm YOLO thật đếm người trên ảnh snapshot.

    set MOCK_SNAPSHOT=<ảnh có người>
    uvicorn tests.mock_backend:app --port 8006
    python tests/e2e_scenario.py
"""
import os
import sys
import threading
from pathlib import Path

import requests

BASE = os.getenv("BACKEND_URL", "http://localhost:8006")
os.environ["GEMINI_API_URL"] = BASE + "/mock-gemini/{model}"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend_client import BackendClient, ModeIsManual  # noqa: E402
from config import Settings  # noqa: E402
from decision_engine import DecisionEngine  # noqa: E402
from llm_client import GeminiClient  # noqa: E402
from people_counter import run_counter_loop  # noqa: E402


def sensor(t, h):
    requests.post(f"{BASE}/test/sensor", json={"temperature": t, "humidity": h}).raise_for_status()


def mode(m):
    requests.patch(f"{BASE}/test/mode/{m}").raise_for_status()


def state():
    return requests.get(f"{BASE}/test/state").json()


s = Settings()
s.backend_url = BASE
s.llm_min_interval = 0
s.decision_log_path = ""
s.dry_run = False
backend = BackendClient(BASE)

# 1) YOLO thật đếm người từ snapshot backend
mode("AUTO")
sensor(30.5, 72)
n = run_counter_loop(s, backend, threading.Event(), source_spec="backend", once=True)
print("1. YOLO đếm:", n, "| backend people_count =", state()["state"]["people_count"])
assert n and state()["state"]["people_count"] == n

# 2) Gemini: model 1 hết quota, model 2 không nhận thinkingConfig → tự gửi lại → thành công
llm = GeminiClient("fake-key", ["m-quota", "m-nothink"], thinking_level="low")
eng = DecisionEngine(s, backend, llm)
d = eng.step()
st = state()
print("2.", d.source, (d.fan_level, d.ac_on, d.ac_temp), "|", st["state"]["ai_reason"])
print("   các lần gọi Gemini:", [(c["model"], "thinkingConfig" in (c["config"] or {})) for c in st["gemini_calls"]])
assert d.source == "GEMINI" and st["state"]["ac_on"] is True

# 3) Không có thay đổi → không gọi lại Gemini
calls_before = len(state()["gemini_calls"])
assert eng.step() is None and len(state()["gemini_calls"]) == calls_before
print("3. Không thay đổi → không gọi Gemini: OK")

# 4) Nhiệt độ giảm → gọi lại → quạt 1, tắt AC
sensor(26.0, 60)
d = eng.step()
print("4.", d.source, (d.fan_level, d.ac_on, d.ac_temp), "|", d.ai_reason)
assert (d.fan_level, d.ac_on) == (1, False)

# 5) Phòng trống → tắt hết
backend.push_people_count(0)
d = eng.step()
print("5.", d.source, (d.fan_level, d.ac_on), "|", d.ai_reason)
assert (d.fan_level, d.ac_on) == (0, False)

# 6) MANUAL → AI dừng, gửi lệnh bị 409
mode("MANUAL")
backend.push_people_count(3)
assert eng.step() is None
try:
    backend.send_command(1, False, 26, "test")
    raise AssertionError("phải bị 409")
except ModeIsManual:
    print("6. MANUAL → AI bỏ qua, /api/ai/command trả 409: OK")

# 7) Về AUTO → quyết định lại ngay
mode("AUTO")
d = eng.step()
print("7. AUTO lại →", d.source, (d.fan_level, d.ac_on, d.ac_temp))
assert d is not None
print("E2E PASSED. Tổng lệnh backend nhận:", len(state()["commands"]))
