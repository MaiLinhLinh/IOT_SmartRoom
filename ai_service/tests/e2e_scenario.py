"""
tests/e2e_scenario.py — Kịch bản end-to-end qua HTTP thật với tests/mock_backend.py
(backend giả theo contract v4 + Gemini giả), kèm YOLO thật đếm người trên ảnh snapshot.

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
from decision_engine import DecisionEngine, fans_text  # noqa: E402
from llm_client import GeminiClient  # noqa: E402

SKIP_YOLO = os.getenv("SKIP_YOLO") == "1"


def sensor(t, h):
    requests.post(f"{BASE}/test/sensor", json={"temperature": t, "humidity": h}).raise_for_status()


def mode(m):
    requests.patch(f"{BASE}/test/mode/{m}").raise_for_status()


def state():
    return requests.get(f"{BASE}/test/state").json()


def show(step, d):
    st = state()["state"]
    fans = (st["fan1"], st["fan2"], st["fan3"])
    ac = f"AC {st['ac_temp']}°C" if st["ac_on"] else "AC tắt"
    print(f"{step} [{d.source}/{d.mode_label}] {fans_text(fans)}, {ac} | {st['ai_reason']}")


s = Settings()
s.backend_url = BASE
s.llm_min_interval = 0
s.decision_log_path = ""
s.dry_run = False
s.ac_min_on_seconds = s.ac_min_off_seconds = 0   # tắt bảo vệ máy nén để chạy kịch bản nhanh
s.empty_ac_grace_seconds = 0
backend = BackendClient(BASE)

requests.post(f"{BASE}/test/reset").raise_for_status()
mode("AUTO")
# 1) YOLO thật đếm người từ snapshot backend
if SKIP_YOLO:
    backend.push_people_count(4)
    print("1. (bỏ qua YOLO) people_count = 4")
else:
    from people_counter import run_counter_loop
    n = run_counter_loop(s, backend, threading.Event(), source_spec="backend", once=True)
    print("1. YOLO đếm:", n, "| backend people_count =", state()["state"]["people_count"])
    assert n and state()["state"]["people_count"] == n

eng = DecisionEngine(s, backend, GeminiClient("fake-key", ["m-quota", "m-nothink"], thinking_level="low"))

# 2) Hơi ấm, độ ẩm vừa → CHỈ QUẠT
sensor(27.5, 60)
d = eng.step(); show("2. 27.5°C 60%:", d)
assert d.source == "GEMINI" and not d.ac_on and d.fan_count >= 1

# 3) Không đổi → không gọi lại
calls = len(state()["gemini_calls"])
assert eng.step() is None and len(state()["gemini_calls"]) == calls
print("3. Không thay đổi → không gọi Gemini: OK")

# 4) Nóng → KẾT HỢP (pull-down bật 3 quạt vì phòng còn nóng hơn Tset nhiều)
sensor(31.0, 65)
d = eng.step(); show("4. 31°C 65%:", d)
assert d.ac_on and d.fan_count == 3

# 5) AC đã kéo phòng về gần Tset, còn 2 người → KẾT HỢP ổn định, bớt quạt, AC đặt cao
sensor(28.4, 60)
backend.push_people_count(2)
d = eng.step(); show("5. 28.4°C 60%, 2 người (AC đang chạy):", d)
assert d.ac_on and d.ac_temp >= 27 and d.fan_count < 3

# 6) Phiên mới, trời nồm ẩm: nhiệt độ không cao nhưng độ ẩm rất cao → CHỈ ĐIỀU HÒA
requests.post(f"{BASE}/test/reset").raise_for_status()
backend.push_people_count(2)
eng = DecisionEngine(s, backend, GeminiClient("fake-key", ["m-nothink"], thinking_level=""))
sensor(27.0, 84)
d = eng.step(); show("6. 27°C 84% (phiên mới):", d)
assert d.ac_on and d.fan_count == 0
sensor(27.0, 68)   # AC đã hút ẩm → quạt hiệu quả trở lại → KẾT HỢP, nâng nhiệt độ đặt
d = eng.step(); show("   AC hút ẩm còn 68%:", d)
assert d.ac_on and d.fan_count >= 1 and d.ac_temp >= 27

# 7) Gemini chọn sai (tắt hết khi nóng) → comfort guard sửa lại
bad = DecisionEngine(s, backend, GeminiClient("fake-key", ["m-hot"], thinking_level=""))
sensor(31.5, 60)
d = bad.step(); show("7. Gemini chọn tắt hết lúc 31.5°C:", d)
assert d.ac_on

# 7b) Người dùng khai báo trên app: phòng KHÔNG có điều hòa, quạt 3 hỏng → chỉ dùng quạt 1, 2
requests.put(f"{BASE}/test/config", json={"ac_available": False, "fans_available": [True, True, False]})
sensor(30.0, 60)
backend.push_people_count(3)
d = eng.step(); show("7b. Hồ sơ mới (không AC, quạt 3 hỏng), 30°C:", d)
assert not d.ac_on and d.fans == (True, True, False)
requests.put(f"{BASE}/test/config", json={})

# 8) Phòng trống → [Tự động] tắt hết, không gọi Gemini
calls = len(state()["gemini_calls"])
backend.push_people_count(0)
d = eng.step(); show("8. Phòng trống:", d)
assert d.source == "AUTO" and len(state()["gemini_calls"]) == calls

# 9) MANUAL → AI dừng, /api/ai/command trả 409
mode("MANUAL")
backend.push_people_count(3)
assert eng.step() is None
try:
    backend.send_command((True, False, False), False, 26, "test")
    raise AssertionError("phải bị 409")
except ModeIsManual:
    print("9. MANUAL → AI bỏ qua, /api/ai/command trả 409: OK")

mode("AUTO")
print("E2E PASSED. Lệnh backend nhận:", len(state()["commands"]),
      "| model Gemini đã dùng:", sorted({c["model"] for c in state()["gemini_calls"]}))
