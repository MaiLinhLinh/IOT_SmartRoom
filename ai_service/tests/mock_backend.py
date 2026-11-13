"""
tests/mock_backend.py — Backend giả lập (không cần SQL Server) để test AI service.

Mô phỏng đúng hành vi các API AI dùng trong backend thật:
    GET  /api/ai/context, POST /api/people/count, POST /api/ai/command (409 khi MANUAL),
    GET  /api/camera/snapshot, POST /test/sensor, PATCH /test/mode, GET /test/state

Chạy:  uvicorn tests.mock_backend:app --port 8006
       (đặt biến MOCK_SNAPSHOT=duong_dan_anh.jpg để snapshot trả ảnh đó)
"""
import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

app = FastAPI(title="Mock AIoT backend")

STATE = {
    "mode": "AUTO", "fan_level": 0, "ac_on": False, "ac_temp": 26,
    "people_count": 0, "people_source": "NONE", "people_updated_at": None,
    "temperature": 30.5, "humidity": 72.0, "sensor_timestamp": datetime.now().isoformat(),
    "ai_reason": None,
}
COMMANDS: list[dict] = []
PEOPLE_KEY = os.getenv("PEOPLE_API_KEY", "")
AI_KEY = os.getenv("AI_API_KEY", "")


class PeopleCountIn(BaseModel):
    count: int = Field(..., ge=0, le=1000)
    source: str = Field(default="YOLO", pattern="^(YOLO|MANUAL|AI|SENSOR)$")


class AIDecisionIn(BaseModel):
    fan_level: int = Field(..., ge=0, le=3)
    ac_on: bool
    ac_temp: int = Field(default=26, ge=16, le=32)
    ai_reason: str = Field(..., min_length=1, max_length=500)


class SensorIn(BaseModel):
    temperature: float
    humidity: float


@app.get("/api/ai/context")
def context():
    return STATE


@app.post("/api/people/count")
def people(body: PeopleCountIn, x_api_key: Optional[str] = Header(default=None)):
    if PEOPLE_KEY and x_api_key != PEOPLE_KEY:
        raise HTTPException(401, "X-API-Key không hợp lệ")
    STATE.update(people_count=body.count, people_source=body.source,
                 people_updated_at=datetime.now().isoformat())
    return {"status": "ok", "people_count": body.count}


@app.post("/api/ai/command")
def command(body: AIDecisionIn, x_api_key: Optional[str] = Header(default=None)):
    if AI_KEY and x_api_key != AI_KEY:
        raise HTTPException(401, "X-API-Key không hợp lệ")
    if STATE["mode"] != "AUTO":
        raise HTTPException(409, "Thiết bị đang MANUAL. Chuyển sang AUTO trước khi AI điều khiển.")
    STATE.update(fan_level=body.fan_level, ac_on=body.ac_on, ac_temp=body.ac_temp, ai_reason=body.ai_reason)
    COMMANDS.append(body.model_dump())
    return STATE


@app.get("/api/camera/snapshot")
def snapshot():
    path = os.getenv("MOCK_SNAPSHOT")
    if not path or not os.path.exists(path):
        raise HTTPException(503, "Không có ảnh snapshot (đặt MOCK_SNAPSHOT)")
    with open(path, "rb") as f:
        return Response(f.read(), media_type="image/jpeg")


# ---------- Gemini giả lập (đặt GEMINI_API_URL=http://localhost:8006/mock-gemini/{model}) ----------
GEMINI_CALLS: list[dict] = []


@app.post("/mock-gemini/{model}")
def mock_gemini(model: str, body: dict, x_goog_api_key: Optional[str] = Header(default=None)):
    import json
    import re
    GEMINI_CALLS.append({"model": model, "body": body})
    if not x_goog_api_key:
        raise HTTPException(403, "missing key")
    cfg = body.get("generationConfig", {})
    if model.endswith("-nothink") and "thinkingConfig" in cfg:
        return Response(json.dumps({"error": {"message": "Thinking level is not supported"}}),
                        status_code=400, media_type="application/json")
    if model.endswith("-quota"):
        return Response("{}", status_code=429, media_type="application/json")
    prompt = body["contents"][0]["parts"][0]["text"]
    people = re.search(r"Số người trong phòng: (\d+)", prompt)
    temp = float(re.search(r"Nhiệt độ phòng \(DHT11\): ([\d.]+)", prompt).group(1))
    n = int(people.group(1)) if people else None
    if n == 0:
        out = {"fan_level": 0, "ac_on": False, "ac_temp": 26, "ai_reason": f"Phòng trống, {temp}°C → tắt hết."}
    elif temp >= 29:
        out = {"fan_level": 2, "ac_on": True, "ac_temp": 25, "ai_reason": f"{n} người, {temp}°C nóng → AC 25°C + quạt 2."}
    else:
        out = {"fan_level": 1, "ac_on": False, "ac_temp": 26, "ai_reason": f"{n} người, {temp}°C dễ chịu → quạt 1."}
    text = "```json\n" + json.dumps(out, ensure_ascii=False) + "\n```"
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}


# ---------- endpoint chỉ dùng cho test ----------
@app.post("/test/sensor")
def set_sensor(body: SensorIn):
    STATE.update(temperature=body.temperature, humidity=body.humidity,
                 sensor_timestamp=datetime.now().isoformat())
    return STATE


@app.patch("/test/mode/{mode}")
def set_mode(mode: str):
    STATE["mode"] = mode
    return STATE


@app.get("/test/state")
def state():
    return {"state": STATE, "commands": COMMANDS,
            "gemini_calls": [{"model": c["model"], "config": c["body"].get("generationConfig")} for c in GEMINI_CALLS]}
