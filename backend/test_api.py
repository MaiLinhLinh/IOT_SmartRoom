import sys
import requests, json

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "http://localhost:8006"

# 1. Health check
r = requests.get(f"{BASE}/")
print("GET /:", r.status_code, r.json()["message"])

# 2. Login
r = requests.post(f"{BASE}/auth/login", json={"username":"admin","password":"admin123"})
print("POST /auth/login:", r.status_code)
data = r.json()
token = data["access_token"]
user = data["user"]
print(f"  User: {user['username']} (role={user['role']})")
headers = {"Authorization": f"Bearer {token}"}

# 3. Device state
r = requests.get(f"{BASE}/api/device/state")
print("GET /api/device/state:", r.status_code, json.dumps(r.json(), indent=2))

# 4. Sensor (simulate ESP32)
r = requests.post(f"{BASE}/api/sensor", json={"temperature": 29.5, "humidity": 68.0})
print("POST /api/sensor:", r.status_code, r.json()["message"])

# 5. Sensor latest
r = requests.get(f"{BASE}/api/sensor/latest")
print("GET /api/sensor/latest:", r.status_code)

# 6. Device command (Manual) — chuyển MANUAL trước nếu đang AUTO, rồi bật quạt 1+2
r = requests.patch(f"{BASE}/api/device/mode", json={"mode":"MANUAL"}, headers=headers)
print("PATCH /api/device/mode MANUAL:", r.status_code)
r = requests.post(f"{BASE}/api/device/command",
                  json={"fan1": True, "fan2": True, "fan3": False,
                        "ac_on": True, "ac_temp": 25}, headers=headers)
s = r.json()
print("POST /api/device/command:", r.status_code,
      f"fan1={s['fan1']} fan2={s['fan2']} fan3={s['fan3']} ac_temp={s['ac_temp']}")

# 7. Control logs
r = requests.get(f"{BASE}/api/control-logs", headers=headers)
print("GET /api/control-logs:", r.status_code, "total=", r.json()["total"])

# 8. Profile
r = requests.get(f"{BASE}/auth/profile", headers=headers)
print("GET /auth/profile:", r.status_code, r.json()["username"])

# 9. Camera stream-url
r = requests.get(f"{BASE}/api/camera/stream-url")
print("GET /api/camera/stream-url:", r.status_code)

# 10. People latest
r = requests.get(f"{BASE}/api/people/latest")
print("GET /api/people/latest:", r.status_code)

# 11. AI context
r = requests.get(f"{BASE}/api/ai/context")
print("GET /api/ai/context:", r.status_code)

# 12. Logs
r = requests.get(f"{BASE}/api/logs")
print("GET /api/logs:", r.status_code, "total=", r.json()["total"])

# 13. Change mode
r = requests.patch(f"{BASE}/api/device/mode", json={"mode":"AUTO"}, headers=headers)
print("PATCH /api/device/mode:", r.status_code, "mode=", r.json()["mode"])

# 14. Switch back to MANUAL
r = requests.patch(f"{BASE}/api/device/mode", json={"mode":"MANUAL"}, headers=headers)
print("PATCH /api/device/mode:", r.status_code, "mode=", r.json()["mode"])

print()
print("=== ALL TESTS PASSED ===")
