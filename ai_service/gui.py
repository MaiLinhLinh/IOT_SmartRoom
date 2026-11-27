"""
gui.py — Bảng điều khiển cho AI service (mở trên trình duyệt), thay cho việc gõ từng lệnh.

    python gui.py                 # mở http://127.0.0.1:8090 trên trình duyệt
    python gui.py --port 9000     # đổi cổng
    python gui.py --host 0.0.0.0  # cho máy khác / điện thoại cùng mạng mở được (KHÔNG có đăng nhập!)

Chức năng:
  • Chạy / dừng AI thật (main.py): cả hai chức năng, chỉ đếm người, hoặc chỉ ra quyết định.
  • Chạy / dừng demo giả lập (simulate.py): chọn kịch bản, chế độ, tốc độ...; hiện tiến độ từng bước.
  • Trạng thái phòng đọc trực tiếp từ backend: nhiệt độ, độ ẩm, số người, quạt 1-3, điều hòa, lý do AI.
  • Đăng nhập backend để chuyển AUTO / MANUAL.
  • Kiểm tra kết nối backend, camera, Gemini key; xem log trực tiếp.

Bảng điều khiển chỉ chạy MỘT tiến trình tại một thời điểm (main.py và simulate.py không được chạy cùng lúc).
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from typing import Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from config import BASE_DIR, settings
import events
import simulator

GUI_DIR = BASE_DIR / "gui"


class RunState:
    """Trạng thái có cấu trúc của tiến trình đang chạy, dựng từ các dòng @@EVENT (xem events.py)."""

    def __init__(self):
        self.scenario: Optional[dict] = None
        self.current = 0
        self.frac = 0.0
        self.values: dict = {}
        self.device: Optional[dict] = None
        self.feed: deque = deque(maxlen=40)
        self.keep: Optional[dict] = None
        self.alerts: dict = {}
        self.summary: Optional[dict] = None
        self.people: Optional[dict] = None
        self.n_decisions = 0

    def apply(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "scenario_start":
            ev["steps"] = [{**st, "status": "pending"} for st in ev.get("steps", [])]
            self.scenario, self.current, self.summary = ev, 0, None
        elif t == "step_start" and self.scenario:
            self.current, self.frac = ev["i"], 0.0
            step = self.scenario["steps"][ev["i"] - 1]
            step.update(status="running", started=ev["ts"], run_seconds=ev.get("duration"))
        elif t == "tick":
            self.frac = ev.get("frac", 0.0)
            self.values = {k: ev.get(k) for k in ("temperature", "humidity", "people")}
            if ev.get("device"):
                self.device = ev["device"]
        elif t == "step_end" and self.scenario:
            step = self.scenario["steps"][ev["i"] - 1]
            ok = ev.get("ok")
            step.update(status="ok" if ok else ("fail" if ok is False else "done"), result=ev.get("label"),
                        device=ev.get("device"), manual=ev.get("manual"))
            if ev.get("device"):
                self.device = ev["device"]
        elif t == "summary":
            self.summary = ev
            self.current = 0
        elif t == "decision":
            self.n_decisions += 1
            ev["step"] = self.current or None
            self.feed.appendleft(ev)
            self.keep = None
            self.device = {"fans": ev["fans"], "ac_on": ev["ac_on"], "ac_temp": ev["ac_temp"],
                           "label": ev.get("label")} if (ev.get("sent") or ev.get("dry_run")) else self.device
            self.values = {"temperature": ev.get("temperature"), "humidity": ev.get("humidity"),
                           "people": ev.get("people")}
        elif t == "keep":
            self.keep = ev
        elif t == "people":
            self.people = ev
        elif t == "alert":
            self.alerts[ev["key"]] = ev
        elif t == "alert_clear":
            self.alerts.pop(ev.get("key"), None)

    def to_dict(self) -> dict:
        return {"scenario": self.scenario, "current": self.current, "frac": self.frac, "values": self.values,
                "device": self.device, "feed": list(self.feed), "keep": self.keep,
                "alerts": sorted(self.alerts.values(), key=lambda a: a["ts"]), "summary": self.summary,
                "people": self.people, "n_decisions": self.n_decisions}


# ======================================================================
# Quản lý tiến trình con (main.py / simulate.py)
# ======================================================================
class ProcessManager:
    def __init__(self, max_lines: int = 3000):
        self.proc: Optional[subprocess.Popen] = None
        self.kind: Optional[str] = None          # "ai" | "sim"
        self.cmd: list[str] = []
        self.started_at: Optional[str] = None
        self.exit_code: Optional[int] = None
        self.run = RunState()
        self.lines: deque = deque(maxlen=max_lines)
        self.line_no = 0
        self.lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def log(self, text: str, level: str = "gui") -> None:
        with self.lock:
            self.line_no += 1
            self.lines.append({"n": self.line_no, "t": datetime.now().strftime("%H:%M:%S"),
                               "text": text.rstrip("\n"), "src": level})

    def start(self, kind: str, script: str, args: list[str]) -> None:
        if self.running:
            raise HTTPException(409, f"Đang chạy {self.kind_text()} — bấm Dừng trước.")
        cmd = [sys.executable, "-u", script, *args]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "GUI_EVENTS": "1"}
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen(cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     env=env, text=True, encoding="utf-8", errors="replace", bufsize=1,
                                     creationflags=flags)
        self.kind, self.cmd, self.exit_code = kind, cmd, None
        self.started_at = datetime.now().strftime("%H:%M:%S")
        self.run = RunState()
        self.log(f"▶ Bắt đầu: python {' '.join(cmd[2:])}")
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def _reader(self, proc: subprocess.Popen) -> None:
        run = self.run
        for line in proc.stdout:
            if line.startswith(events.PREFIX):
                try:
                    ev = json.loads(line[len(events.PREFIX):])
                    with self.lock:
                        run.apply(ev)
                except (ValueError, KeyError, IndexError, TypeError):
                    pass
                continue
            self.log(line, "proc")
        code = proc.wait()
        if proc is self.proc:
            self.exit_code = code
        self.log(f"■ Kết thúc ({'bình thường' if code == 0 else f'mã {code}'})")

    def stop(self) -> None:
        if not self.running:
            return
        self.log("■ Đang dừng...")
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def kind_text(self) -> str:
        return {"ai": "AI thật (main.py)", "sim": "Demo giả lập (simulate.py)"}.get(self.kind or "", "—")

    def status(self) -> dict:
        return {"running": self.running, "kind": self.kind, "kind_text": self.kind_text(),
                "started_at": self.started_at, "exit_code": self.exit_code,
                "cmd": " ".join(self.cmd[2:]) if self.cmd else ""}

    def logs_since(self, since: int) -> list[dict]:
        with self.lock:
            return [line for line in self.lines if line["n"] > since]


pm = ProcessManager()
backend_token: dict = {"token": None, "user": None}

# ======================================================================
# API cho giao diện
# ======================================================================
app = FastAPI(title="AIoT AI service — bảng điều khiển", docs_url=None, redoc_url=None)


class RunAI(BaseModel):
    only: Optional[str] = Field(default=None, pattern="^(counter|llm)$")
    source: Optional[str] = None
    show: bool = False
    dry_run: bool = False


class RunSim(BaseModel):
    scenario: str = "full"
    mode: str = Field(default="override", pattern="^(offline|override|backend)$")
    speed: float = Field(default=1.0, ge=0.25, le=20)
    dry_run: bool = False
    with_counter: bool = False
    real_people: bool = False


class ModeIn(BaseModel):
    mode: str = Field(pattern="^(AUTO|MANUAL)$")
    username: Optional[str] = None
    password: Optional[str] = None


@app.get("/")
def index():
    return FileResponse(GUI_DIR / "index.html")


@app.get("/api/info")
def info():
    scenarios = []
    names = list(simulator.SCENARIOS) + [f"scenarios/{p.name}" for p in sorted((BASE_DIR / "scenarios").glob("*.json"))]
    for name in names:
        try:
            key, title, steps = simulator.load_scenario(name if name in simulator.SCENARIOS else str(BASE_DIR / name))
        except Exception as exc:  # noqa: BLE001 — file kịch bản viết sai
            scenarios.append({"name": name, "title": f"Lỗi file: {exc}", "steps": [], "seconds": 0})
            continue
        ev = simulator.scenario_event(name, title, steps)
        scenarios.append({**ev, "seconds": sum(s.duration for s in steps)})
    return {
        "backend_url": settings.backend_url,
        "camera_source": settings.camera_source,
        "cam_ip": settings.cam_ip,
        "gemini_models": settings.gemini_models,
        "gemini_key": bool(settings.gemini_api_key),
        "people_key": bool(settings.people_api_key),
        "ai_key": bool(settings.ai_api_key),
        "env_exists": (BASE_DIR / ".env").exists(),
        "scenarios": scenarios,
    }


_cache: dict = {"at": 0.0, "data": None}


@app.get("/api/status")
def status():
    now = time.monotonic()
    if now - _cache["at"] > 1.0:
        try:
            resp = requests.get(f"{settings.backend_url}/api/ai/context", timeout=2)
            if resp.ok:
                backend = {"ok": True, "context": resp.json()}
            else:
                hint = " — backend chưa áp bản vá AI v4?" if resp.status_code == 500 else ""
                backend = {"ok": False, "error": f"HTTP {resp.status_code}{hint}"}
        except requests.RequestException as exc:
            backend = {"ok": False, "error": f"Không kết nối được: {type(exc).__name__}"}
        _cache.update(at=now, data=backend)
    with pm.lock:
        run = pm.run.to_dict()
    return {"process": pm.status(), "run": run, "backend": _cache["data"], "logged_in": backend_token["user"]}


@app.get("/api/logs")
def logs(since: int = 0):
    return {"lines": pm.logs_since(since), "last": pm.line_no}


@app.post("/api/run/ai")
def run_ai(body: RunAI):
    args = []
    if body.only:
        args += ["--only", body.only]
    if body.source:
        args += ["--source", body.source]
    if body.show and body.only == "counter":
        args.append("--show")
    if body.dry_run:
        args.append("--dry-run")
    pm.start("ai", "main.py", args)
    return pm.status()


@app.post("/api/run/sim")
def run_sim(body: RunSim):
    args = ["--scenario", body.scenario, "--mode", body.mode, "--speed", f"{body.speed:g}"]
    if body.dry_run:
        args.append("--dry-run")
    if body.with_counter and body.mode != "offline":
        args.append("--with-counter")
    if body.real_people and body.mode != "offline":
        args.append("--real-people")
    pm.start("sim", "simulate.py", args)
    return pm.status()


@app.post("/api/stop")
def stop():
    pm.stop()
    return pm.status()


@app.post("/api/logs/clear")
def clear_logs():
    with pm.lock:
        pm.lines.clear()
    return {"ok": True}


@app.post("/api/mode")
def set_mode(body: ModeIn):
    """Đăng nhập backend (nếu chưa) rồi PATCH /api/device/mode."""
    base = settings.backend_url
    if body.username and body.password:
        try:
            resp = requests.post(f"{base}/auth/login", json={"username": body.username, "password": body.password},
                                 timeout=5)
        except requests.RequestException as exc:
            raise HTTPException(502, f"Không kết nối được backend: {exc}")
        if not resp.ok:
            raise HTTPException(401, "Sai tài khoản hoặc mật khẩu backend")
        backend_token.update(token=resp.json()["access_token"], user=body.username)
    if not backend_token["token"]:
        raise HTTPException(401, "Cần đăng nhập backend (tài khoản trên app) trước")
    try:
        resp = requests.patch(f"{base}/api/device/mode", json={"mode": body.mode},
                              headers={"Authorization": f"Bearer {backend_token['token']}"}, timeout=5)
    except requests.RequestException as exc:
        raise HTTPException(502, f"Không kết nối được backend: {exc}")
    if resp.status_code == 401:
        backend_token.update(token=None, user=None)
        raise HTTPException(401, "Phiên đăng nhập hết hạn, đăng nhập lại")
    if not resp.ok:
        raise HTTPException(resp.status_code, resp.text[:200])
    _cache["at"] = 0
    pm.log(f"Đã chuyển thiết bị sang {body.mode}")
    return resp.json()


@app.post("/api/check")
def check():
    """Kiểm tra nhanh: backend, phần AI của backend, camera, Gemini key, API key."""
    results = []

    def add(name, ok, detail):
        results.append({"name": name, "ok": ok, "detail": detail})

    base = settings.backend_url
    try:
        r = requests.get(f"{base}/health", timeout=3)
        add("Backend", r.status_code < 500, f"{base} → phản hồi HTTP {r.status_code}")
    except requests.RequestException as exc:
        add("Backend", False, f"{base} → {type(exc).__name__}")
    try:
        r = requests.get(f"{base}/api/ai/context", timeout=3)
        if r.ok:
            ctx = r.json()
            v4 = "fan1" in ctx
            add("API cho AI (v4)", v4, "có fan1/fan2/fan3" if v4 else "vẫn dạng cũ fan_level → áp bản vá AI v4")
            add("Chế độ thiết bị", ctx.get("mode") == "AUTO",
                f"{ctx.get('mode')}" + ("" if ctx.get("mode") == "AUTO" else " → chuyển AUTO thì AI mới điều khiển"))
        else:
            add("API cho AI (v4)", False, f"HTTP {r.status_code}" + (" → chưa áp bản vá AI v4" if r.status_code == 500 else ""))
    except requests.RequestException as exc:
        add("API cho AI (v4)", False, type(exc).__name__)
    try:
        r = requests.get(f"{base}/api/devices/info", timeout=3)
        if r.ok:
            add("Công suất thiết bị (v5)", True, "backend có /api/devices/info → AI dùng công suất người dùng nhập")
        else:
            add("Công suất thiết bị (v5)", None, f"HTTP {r.status_code} → dùng giá trị mặc định / .env")
    except requests.RequestException as exc:
        add("Công suất thiết bị (v5)", None, type(exc).__name__)
    if settings.camera_source == "esp32":
        try:
            r = requests.get(settings.esp32_capture_url, timeout=4)
            add("Camera ESP32-CAM", r.ok and r.content[:2] == b"\xff\xd8",
                f"{settings.esp32_capture_url} → HTTP {r.status_code}, {len(r.content)} byte")
        except requests.RequestException as exc:
            add("Camera ESP32-CAM", False, f"{settings.esp32_capture_url} → {type(exc).__name__}")
    else:
        add("Camera", True, f"CAMERA_SOURCE={settings.camera_source}")
    add("Gemini API key", bool(settings.gemini_api_key),
        f"model: {', '.join(settings.gemini_models)}" if settings.gemini_api_key
        else "chưa có → chỉ dùng phương án tính toán dự phòng")
    add("API key backend", None,
        f"PEOPLE_API_KEY {'có' if settings.people_api_key else 'trống'}, AI_API_KEY "
        f"{'có' if settings.ai_api_key else 'trống'} (phải giống backend/.env, nếu không sẽ lỗi 401)")
    return {"results": results}


@app.post("/api/open-env")
def open_env():
    path = BASE_DIR / ".env"
    if not path.exists():
        example = BASE_DIR / ".env.example"
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        pm.log("Đã tạo .env từ .env.example")
    try:
        if os.name == "nt":
            os.startfile(str(path))  # noqa: S606 — mở bằng trình soạn thảo mặc định của Windows
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        raise HTTPException(500, f"Không mở được {path}: {exc}")
    return {"ok": True, "path": str(path), "note": "Sửa xong .env thì khởi động lại gui.py để áp dụng."}


def main() -> int:
    parser = argparse.ArgumentParser(description="Bảng điều khiển AI service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}"
    print(f"Bảng điều khiển: {url}  (Ctrl+C để thoát)")
    if args.host == "0.0.0.0":
        print("⚠ Đang mở cho mọi máy trong mạng — bảng điều khiển KHÔNG có đăng nhập.")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    finally:
        pm.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
