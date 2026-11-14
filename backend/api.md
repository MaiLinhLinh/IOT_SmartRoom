# AIoT Smart Room — API Contract v4

> Base URL: `http://<IP_LAPTOP>:8006` (dev: `http://localhost:8006`)
> Swagger: `http://localhost:8006/docs`
> DB: SQL Server `aiot_db` (schema v4)
> Version: `4.0.0`
>
> **v4 thay đổi:** 1 quạt 3 mức (`fan_level` 0–3) → **3 quạt độc lập bật/tắt**
> (`fan1`, `fan2`, `fan3`, kiểu `bool`). Không tương thích ngược với log cũ.
> Phần AI (`/api/ai/*`, ai_engine nội bộ, ai_service Gemini) **chưa cập nhật** theo v4 —
> endpoint AI vẫn nhận schema cũ nhưng sẽ lỗi khi thực thi cho đến khi được sửa sau.

---

## 0. Quy ước chung

### Xác thực
Các API có 🔒 yêu cầu header:

```
Authorization: Bearer <access_token>
```

Token lấy từ `POST /auth/login`, hết hạn sau 7 ngày (`ACCESS_TOKEN_EXPIRE_MINUTES=10080`).

### Kiểu dữ liệu
| Field | Giá trị |
|---|---|
| `fan1` / `fan2` / `fan3` | `true` = quạt bật, `false` = quạt tắt (3 quạt độc lập) |
| `ac_on` | `true` / `false` |
| `ac_temp` | `16`–`32` (°C) |
| `mode` | `"MANUAL"` hoặc `"AUTO"` |
| `actor_type` | `"USER"` hoặc `"AI"` |
| `source` | `"MANUAL"` hoặc `"AI"` |
| `people_source` | `"YOLO"`, `"MANUAL"`, `"AI"`, `"SENSOR"`, `"NONE"` |
| Thời gian | Giờ local của server, dạng ISO 8601 (không có timezone) |

### Lỗi
```json
{ "detail": "Mô tả lỗi" }
```
| Code | Ý nghĩa |
|---|---|
| 400 | Dữ liệu không hợp lệ / mật khẩu cũ sai |
| 401 | Chưa đăng nhập / token sai / sai mật khẩu |
| 403 | Không đủ quyền |
| 404 | Không tìm thấy dữ liệu |
| 409 | Đang AUTO nhưng cố điều khiển thủ công |
| 422 | Sai kiểu/thiếu field (Pydantic) |

### 0.1 Ai gọi API nào?

| API | Người gọi | Mục đích | Auth |
|---|---|---|---|
| `POST /auth/register` | Flutter / admin | Tạo tài khoản | Không |
| `POST /auth/login` | Flutter | Lấy JWT + user | Không |
| `POST /auth/token` | Swagger UI | Login form OAuth2 | Không |
| `POST /auth/logout` | Flutter | Xoá token phía client | Không |
| `POST /auth/stream-token` | Flutter | Token ngắn hạn xem camera | JWT |
| `GET/PUT /auth/profile` | Flutter | Xem/sửa profile | JWT |
| `PUT /auth/change-password` | Flutter | Đổi mật khẩu | JWT |
| `POST /api/sensor` | **ESP32** | Gửi nhiệt độ/độ ẩm mỗi 5s | Không |
| `GET /api/sensor/latest` | Flutter | Dashboard | Không |
| `GET /api/device/state` | **ESP32 + Flutter** | Trạng thái hiện tại | Không |
| `POST /api/device/command` | **Flutter** (MANUAL) | Điều khiển quạt/AC | JWT |
| `PATCH /api/device/mode` | **Flutter** | Đổi AUTO/MANUAL | JWT |
| `POST /api/people/count` | **YOLO module** | Đẩy số người | `X-API-Key` (tuỳ chọn) |
| `GET /api/people/latest` | Flutter | Overlay số người | Không |
| `GET /api/ai/context` | **AI service** | Lấy dữ liệu để quyết định | Không |
| `POST /api/ai/command` | **AI service** | Gửi quyết định bật/tắt | `X-API-Key` (tuỳ chọn) |
| `GET /api/logs` | Flutter | Sensor logs (trang 4) | Không |
| `GET /api/control-logs` | Flutter | Lịch sử thao tác (trang 6) | Không |
| `GET /api/control-logs/summary` | Flutter | Thống kê nhanh | Không |
| `GET /api/camera/stream-url` | Flutter | URL LAN + URL proxy | Không |
| `GET /api/camera/stream?token=` | Flutter (4G/WiFi) | MJPEG qua backend | Stream token / JWT |
| `GET /api/camera/snapshot` | YOLO / Flutter | Ảnh tĩnh | Không |
| `GET /api/camera/status` | Flutter | Online/Offline | Không |

---

## 1. Auth — Trang Login & Profile

### `POST /auth/register`
```json
{
  "username": "user1",
  "password": "123456",
  "full_name": "Nguyen Van A",
  "email": "a@example.com"
}
```
`201`:
```json
{ "message": "Tạo tài khoản 'user1' thành công", "id": 2 }
```

### `POST /auth/login`
```json
{ "username": "admin", "password": "admin123" }
```
`200`:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "user": {
    "id": 1,
    "username": "admin",
    "full_name": "Quan tri vien",
    "email": null,
    "role": "admin",
    "created_at": "2026-10-31T21:58:40"
  }
}
```

### `POST /auth/token`
Form OAuth2 (`username`, `password`) — chỉ dùng cho nút Authorize trên Swagger.

### `POST /auth/logout`
```json
{ "message": "Đã đăng xuất" }
```

### `POST /auth/stream-token` 🔒
Lấy token sống ngắn (mặc định 120s) để gắn vào URL stream thay vì đưa access token dài hạn lên query string.
```json
{
  "stream_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 120,
  "stream_path": "/api/camera/stream"
}
```
Flutter ghép URL: `GET /api/camera/stream?token=<stream_token>`.
Hết hạn → gọi lại API này. Không dùng header `Authorization` cho WebView vì thẻ `<img>` không gửi header.

### `GET /auth/profile` 🔒
```json
{
  "id": 1,
  "username": "admin",
  "full_name": "Quan tri vien",
  "email": null,
  "role": "admin",
  "created_at": "2026-10-31T21:58:40"
}
```

### `PUT /auth/profile` 🔒
```json
{ "full_name": "Nguyen Van A", "email": "a@example.com" }
```
Trả về `UserOut` như trên.

### `PUT /auth/change-password` 🔒
```json
{ "old_password": "admin123", "new_password": "newpass123" }
```
```json
{ "message": "Đổi mật khẩu thành công" }
```

---

## 2. Dashboard — Sensor + Device + People

### `GET /api/sensor/latest`
```json
{
  "id": 2,
  "timestamp": "2026-10-31T22:03:15",
  "temperature": 30.5,
  "humidity": 70.0,
  "people_count": 3,
  "fan1": true,
  "fan2": true,
  "fan3": false,
  "ac_on": true,
  "ac_temp": 25,
  "mode": "MANUAL"
}
```
`404` nếu chưa có dữ liệu cảm biến.

### `POST /api/sensor`
ESP32 gọi mỗi 5 giây (không cần auth).
```json
{ "temperature": 30.5, "humidity": 70.0 }
```
`201`:
```json
{ "status": "ok", "log_id": 2, "message": "Đã lưu T=30.5°C H=70.0%" }
```

### `GET /api/device/state`
Nguồn chính cho Dashboard. ESP32 poll mỗi 2 giây.
```json
{
  "fan1": true,
  "fan2": true,
  "fan3": false,
  "ac_on": true,
  "ac_temp": 26,
  "mode": "AUTO",
  "updated_at": "2026-10-31T22:03:24",
  "ai_reason": "Phát hiện 3 người, nhiệt độ 30.5°C khá nóng → Bật quạt 1, 2 + điều hòa 26°C",
  "people_count": 3,
  "people_source": "YOLO",
  "people_updated_at": "2026-10-31T22:03:15"
}
```
- `ai_reason`: hiển thị ở card chấm hồng. `null` khi chưa có quyết định AI hoặc người dùng vừa điều khiển.
- `people_count`: số người mới nhất do module YOLO đẩy lên.

### `POST /api/device/command` 🔒 (MANUAL)
```json
{
  "fan1": true,
  "fan2": true,
  "fan3": false,
  "ac_on": true,
  "ac_temp": 25
}
```
Chỉ dùng khi thiết bị đang `MANUAL`; body không nhận `mode` (gửi thêm field `mode` sẽ nhận `422`). Trả về `DeviceStateOut`. Không cần gửi `actor` — backend lấy từ JWT.
`409` nếu mode hiện tại là `AUTO`; hãy gọi `PATCH /api/device/mode` sang `MANUAL` trước.

### `PATCH /api/device/mode` 🔒
Dùng cho nút AUTO / MANUAL trên Dashboard.
```json
{ "mode": "AUTO" }
```
Trả về `DeviceStateOut`.

> Luồng UI đề xuất:
> 1. Bấm `MANUAL` → `PATCH /api/device/mode {"mode":"MANUAL"}`
> 2. Bấm nút quạt / toggle AC / slider → `POST /api/device/command`
> 3. Bấm `AUTO` → `PATCH /api/device/mode {"mode":"AUTO"}` rồi disable vùng điều khiển

---

## 3. People — cho module YOLO chạy riêng

### `POST /api/people/count`
Module YOLO gọi mỗi 1–2 giây (không cần JWT).
Nếu `.env` có `PEOPLE_API_KEY` thì bắt buộc header `X-API-Key`.

Header (nếu bật key):
```
X-API-Key: <PEOPLE_API_KEY>
```
Body:
```json
{ "count": 3, "source": "YOLO" }
```
`200`:
```json
{
  "status": "ok",
  "people_count": 3,
  "people_source": "YOLO",
  "people_updated_at": "2026-10-31T22:03:15"
}
```

### `GET /api/people/latest`
```json
{
  "people_count": 3,
  "people_source": "YOLO",
  "people_updated_at": "2026-10-31T22:03:15"
}
```

---

## 3b. AI External — AI service bên ngoài điều khiển thiết bị

> AI **không gửi trực tiếp xuống ESP32**. Luồng:
> `AI → POST /api/ai/command → device_state → ESP32 poll GET /api/device/state (2s) → LED/LCD`
>
> ⚠️ **Từ v4, phần AI chưa được cập nhật** (sẽ sửa sau): endpoint vẫn nhận `fan_level`
> theo schema cũ nhưng sẽ lỗi khi thực thi. Chờ ai_service chuyển sang `fan1/fan2/fan3`.
>
> Khi dùng AI bên ngoài, đặt `AI_ENGINE_ENABLED=false` trong `.env` để AI engine nội bộ không ghi đè.

Nếu `.env` có `AI_API_KEY`, bắt buộc header:
```
X-API-Key: <AI_API_KEY>
```

### `GET /api/ai/context`
Dữ liệu gộp để AI ra quyết định trong 1 lần gọi.
```json
{
  "mode": "AUTO",
  "fan1": true,
  "fan2": true,
  "fan3": false,
  "ac_on": true,
  "ac_temp": 26,
  "people_count": 3,
  "people_source": "YOLO",
  "people_updated_at": "2026-10-31T22:03:15",
  "temperature": 30.5,
  "humidity": 70.0,
  "sensor_timestamp": "2026-10-31T22:03:15",
  "ai_reason": null
}
```

### `POST /api/ai/command`
Chỉ hợp lệ khi thiết bị đang `AUTO` (nếu MANUAL trả `409`).
```json
{
  "fan1": true,
  "fan2": true,
  "fan3": false,
  "ac_on": true,
  "ac_temp": 25,
  "ai_reason": "Phát hiện 3 người, nhiệt độ 30.5°C khá nóng → Bật quạt 1, 2 + điều hòa 25°C"
}
```
`200`: trả `DeviceStateOut`.
Lệnh có thay đổi sẽ ghi `control_logs` với `actor="AI"`, `actor_type="AI"`, `source="AI"`.

---

## 4. Sensor Logs — Trang 4

### `GET /api/logs`
Query:
| Param | Mặc định | Mô tả |
|---|---|---|
| `page` | `1` | Trang |
| `size` | `20` | 1–100 |
| `mode` | — | `MANUAL` / `AUTO` |
| `from_date` | — | ISO 8601 |
| `to_date` | — | ISO 8601 |

Ví dụ: `/api/logs?page=1&size=20&mode=AUTO&from_date=2026-10-01T00:00:00`

`200`:
```json
{
  "total": 120,
  "page": 1,
  "size": 20,
  "items": [
    {
      "id": 2,
      "timestamp": "2026-10-31T22:03:15",
      "temperature": 30.5,
      "humidity": 70.0,
      "people_count": 3,
      "fan1": true,
      "fan2": true,
      "fan3": false,
      "ac_on": true,
      "ac_temp": 25,
      "mode": "MANUAL"
    }
  ]
}
```

### `GET /api/logs/{id}`
Trả về 1 item như trên; `404` nếu không có.

---

## 5. Control Logs — Trang 6 (Lịch sử thao tác)

### `GET /api/control-logs`
Query:
| Param | Mô tả |
|---|---|
| `page`, `size` | Phân trang |
| `actor_type` | `USER` / `AI` |
| `actor` | username hoặc `AI` |
| `source` | `MANUAL` / `AI` |
| `search` | Tìm trong mô tả hoặc actor |
| `from_date`, `to_date` | ISO 8601 |

`200`:
```json
{
  "total": 7,
  "page": 1,
  "size": 20,
  "items": [
    {
      "id": 7,
      "timestamp": "2026-10-31T22:03:24",
      "actor": "AI",
      "actor_type": "AI",
      "actor_user_id": null,
      "source": "AI",
      "description": "Chỉnh điều hòa 26°C",
      "fan1": true,
      "fan2": true,
      "fan3": false,
      "ac_on": true,
      "ac_temp": 26,
      "mode": "AUTO",
      "fan1_prev": true,
      "fan2_prev": true,
      "fan3_prev": false,
      "ac_on_prev": true,
      "ac_temp_prev": 25,
      "ai_reason": "Phát hiện 3 người, nhiệt độ 30.5°C khá nóng → Bật quạt 1, 2 + điều hòa 26°C",
      "temperature": 30.5,
      "humidity": 70.0,
      "people_count": 3
    },
    {
      "id": 6,
      "timestamp": "2026-10-31T22:03:15",
      "actor": "admin",
      "actor_type": "USER",
      "actor_user_id": 1,
      "source": "MANUAL",
      "description": "Chỉnh điều hòa 25°C",
      "fan1": true,
      "fan2": true,
      "fan3": false,
      "ac_on": true,
      "ac_temp": 25,
      "mode": "MANUAL",
      "fan1_prev": true,
      "fan2_prev": true,
      "fan3_prev": false,
      "ac_on_prev": true,
      "ac_temp_prev": 26,
      "ai_reason": null,
      "temperature": 30.5,
      "humidity": 70.0,
      "people_count": 3
    }
  ]
}
```
> Chỉ ghi log khi trạng thái **thực sự thay đổi**. Các trường `*_prev` là giá trị trước lệnh; `temperature/humidity/people_count` là bối cảnh lúc thao tác.

### `GET /api/control-logs/summary`
```json
{
  "total_actions": 7,
  "user_actions": 5,
  "ai_actions": 2,
  "actions_last_24h": 7,
  "top_actors": [
    { "actor": "admin", "count": 5 },
    { "actor": "AI", "count": 2 }
  ],
  "latest": {
    "actor": "AI",
    "description": "Chỉnh điều hòa 26°C",
    "timestamp": "2026-10-31T22:03:24"
  }
}
```

### `GET /api/control-logs/{id}`
Trả về 1 item như trên; `404` nếu không có.

---

## 6. Camera — Trang 3

### `GET /api/camera/stream-url`
```json
{
  "stream_url": "http://192.168.1.100:81/stream",
  "lan_stream_url": "http://192.168.1.100:81/stream",
  "snapshot_url": "http://192.168.1.100:80/capture",
  "proxy_stream_path": "/api/camera/stream",
  "proxy_stream_url": "/api/camera/stream",
  "proxy_snapshot_url": "/api/camera/snapshot",
  "requires_token": true,
  "max_stream_clients": 2,
  "cam_ip": "192.168.1.100",
  "cam_port": "80",
  "cam_stream_port": "81"
}
```
- `stream_url` / `lan_stream_url`: URL trực tiếp ESP32-CAM, **chỉ mở được khi client cùng LAN**.
- `proxy_stream_url`: dùng cho Flutter 4G / AI ở nhà khác; ghép thêm `?token=<stream_token>`. Nếu `.env` có `PUBLIC_BASE_URL` (Cloudflare Tunnel) thì đây là URL tuyệt đối.
- `proxy_snapshot_url`: ảnh tĩnh qua backend, không cần token (dành cho AI/YOLO).
- Số người overlay lấy từ `GET /api/people/latest`.

`CAM_PORT=80` dùng cho `/capture` và `/status`; `CAM_STREAM_PORT=81` dùng cho `/stream`.

### `GET /api/camera/stream?token=<token>` — MJPEG proxy
- Token lấy từ `POST /auth/stream-token` (sống ngắn), hoặc dùng access token JWT.
- Cũng chấp nhận header `Authorization: Bearer <token>`; WebView/`<img>` không gửi header nên phải dùng query `token`.
- Trả `multipart/x-mixed-replace`; backend giữ kết nối tới ESP32-CAM và chuyển tiếp từng frame.
- `401`: thiếu / sai / hết hạn token.
- `429`: vượt `CAM_STREAM_MAX_CLIENTS` (mặc định 2 client cùng lúc).
- `503`: không kết nối được ESP32-CAM.

### `GET /api/camera/snapshot`
Trả binary `image/jpeg`; `503` nếu không kết nối được CAM.

### `GET /api/camera/status`
```json
{ "online": true, "cam_ip": "192.168.1.100", "cam_port": "80", "cam_stream_port": "81" }
```

---

## 7. Luồng dữ liệu

```
[ESP32] -- POST /api/sensor (5s) --> [BACKEND] --> sensor_logs
[ESP32] <-- GET /api/device/state (2s) -- [BACKEND] --> điều khiển LED/LCD

[YOLO module riêng] -- POST /api/people/count --> [BACKEND] --> device_state.people_count
[AI service riêng] -- POST /api/ai/command --> [BACKEND] --> device_state + control_logs (actor=AI)
                     (AI_ENGINE_ENABLED=false)
[AI Engine nội bộ 10s] -- đọc sensor + people --> nếu AUTO: apply_command()
                                       --> device_state + control_logs (actor=AI, ai_reason)

[Flutter] -- POST /auth/login --> token
[Flutter] -- GET /api/device/state + /api/sensor/latest --> Dashboard
[Flutter] -- PATCH /api/device/mode + POST /api/device/command --> điều khiển
[Flutter] -- GET /api/logs / /api/control-logs --> lịch sử
[Flutter] -- GET /api/camera/stream-url + /api/people/latest --> Camera
[Flutter] -- POST /auth/stream-token --> stream token (120s)
[Flutter 4G] -- GET /api/camera/stream?token=... --> [BACKEND] -- MJPEG --> [ESP32-CAM]
[AI nhà khác] -- GET /api/camera/snapshot --> [BACKEND] -- JPEG --> [ESP32-CAM]
```

---

## 8. Mapping 6 trang Flutter

| Trang | API chính |
|---|---|
| 1. Login | `POST /auth/login` |
| 2. Dashboard | `GET /api/sensor/latest`, `GET /api/device/state`, `PATCH /api/device/mode`, `POST /api/device/command`, `GET /api/people/latest` |
| 3. Camera | `POST /auth/stream-token`, `GET /api/camera/stream?token=`, `GET /api/camera/stream-url`, `GET /api/camera/status`, `GET /api/people/latest`, `GET /api/camera/snapshot` |
| 4. Sensor Logs | `GET /api/logs`, `GET /api/logs/{id}` |
| 5. Profile | `GET /auth/profile`, `PUT /auth/profile`, `PUT /auth/change-password` |
| 6. Control History | `GET /api/control-logs`, `GET /api/control-logs/summary`, `GET /api/control-logs/{id}` |

---

## 9. Khởi động / reset DB

```powershell
conda activate IOT
cd D:\IOT\backend

# Reset toàn bộ DB + tạo admin
python init_db.py --reset --admin-username admin --admin-password admin123

# Chạy server
uvicorn main:app --host 0.0.0.0 --port 8006 --reload
```

> Tài khoản mặc định sau khi init: `admin` / `admin123` — đổi mật khẩu qua `PUT /auth/change-password`.
> Nếu chạy YOLO trên máy khác, đặt `CAM_IP` trong `.env` backend và trỏ module YOLO về `http://<IP_LAPTOP>:8006/api/people/count`.

---

## 10. Lưu ý bắt buộc cho từng bạn

### 10.1 Bạn Flutter

**Xác thực**
- `POST /auth/login` trả `access_token` + `user`. Lưu token (SharedPreferences/SecureStorage) và gắn header:
  `Authorization: Bearer <access_token>` cho mọi API có 🔒.
- Token sống 7 ngày, **không có refresh token**. Gặp `401` → xoá token, quay về Login.
- Không gửi `actor` trong `/api/device/command`; backend lấy từ JWT.

**Kết nối**
- Base URL phải là IP laptop (ví dụ `http://192.168.1.5:8006`), không dùng `localhost` khi chạy máy thật.
- Backend chạy `--host 0.0.0.0`, mở firewall port `8006`; điện thoại và laptop cùng mạng LAN.
- Android 9+ chặn HTTP thường: bật `android:usesCleartextTraffic="true"` trong `AndroidManifest.xml`.
- Không có WebSocket. Poll `/api/device/state` + `/api/sensor/latest` mỗi **2–3 giây**, tránh poll <1s.

**Dashboard**
- `/api/sensor/latest` trả `404` khi ESP32 chưa gửi dữ liệu → hiển thị `--` / “Chưa có dữ liệu”, không crash.
- Nút AUTO/MANUAL dùng `PATCH /api/device/mode`, **không** dùng `/api/device/command` để đổi mode.
- Khi đang AUTO: disable vùng điều khiển. Nếu vẫn gọi `/api/device/command` sẽ nhận `409`.
- `ai_reason` có thể `null` (MANUAL hoặc AI chưa chạy) → ẩn card lý do AI.
- Slider nhiệt độ chỉ bật khi `ac_on = true`; gửi `ac_temp` trong khoảng `16–32`.
- `people_count` lấy từ `/api/device/state` hoặc `/api/people/latest`; `people_updated_at` cho biết lần cập nhật cuối (có thể cũ nếu module YOLO dừng).

**Camera**
- Gọi `GET /api/camera/stream-url` để lấy cả URL LAN và URL proxy.
- **Cùng LAN:** có thể dùng `lan_stream_url` / `stream_url` trực tiếp trong WebView (không cần token).
- **4G / WiFi khác nhà:** gọi `POST /auth/stream-token`, sau đó mở
  `proxy_stream_url + "?token=" + stream_token` bằng WebView/MJPEG player.
- Stream token sống 120s (đổi bằng `STREAM_TOKEN_EXPIRE_SECONDS`). Khi stream đóng hoặc gặp `401`, lấy token mới rồi kết nối lại.
- Gặp `429`: đang có người xem khác (giới hạn `CAM_STREAM_MAX_CLIENTS`), hiện thông báo và cho phép thử lại.
- Gặp `503`: ESP32-CAM offline; hiển thị ảnh fallback bằng `/api/camera/snapshot` hoặc trạng thái Offline.
- Overlay số người lấy từ `/api/people/latest`; trạng thái Online/Offline lấy từ `/api/camera/status`.
- `/api/camera/snapshot` trả JPEG, có thể dùng cho nút chụp ảnh.

**Lịch sử**
- `/api/logs` và `/api/control-logs` dùng `page` + `size` (tối đa 100). Đọc `total` để biết còn trang.
- Trang 6 hiển thị: `actor_type` (`USER`/`AI`) để chọn icon, `source`, `description`, thời gian, `*_prev` → giá trị mới, `ai_reason`, `temperature/humidity/people_count`.
- Bộ lọc có sẵn: `actor_type`, `actor`, `source`, `search`, `from_date`, `to_date`.

**Khác**
- Thời gian là giờ local dạng ISO 8601 (`2026-10-31T22:03:15`), parse bằng `DateTime.parse`.
- Hiển thị lỗi theo `detail` trong JSON; phân biệt `401`, `409`, `422`.
- Flutter **không gọi** `/api/people/count` và `/api/ai/*` (dành cho module YOLO/AI).

---

### 10.2 Bạn AI / YOLO

**Hai kênh riêng biệt**
1. Đếm người → `POST /api/people/count` với `{"count": N, "source": "YOLO"}`.
2. Ra quyết định → `POST /api/ai/command` với `fan_level`, `ac_on`, `ac_temp`, `ai_reason`
   ⚠️ (chưa cập nhật v4 — sau sẽ đổi thành `fan1/fan2/fan3`).

**Điều khiển**
- AI **không gửi trực tiếp xuống ESP32**. Gửi lên backend; ESP32 tự poll `/api/device/state` mỗi 2s.
- Trước khi dùng AI bên ngoài, phối hợp đặt `AI_ENGINE_ENABLED=false` trong `.env` backend, nếu không AI nội bộ sẽ ghi đè quyết định.
- `/api/ai/command` chỉ hợp lệ khi thiết bị đang `AUTO`. Nếu `MANUAL` → `409`.
- `ai_reason` **bắt buộc**, viết tiếng Việt rõ ràng (hiển thị Dashboard + trang 6).
- `fan_level`: `0–3` (⚠️ chưa cập nhật v4, sẽ đổi thành `fan1/fan2/fan3`); `ac_temp`: `16–32`; không gửi `mode` (backend luôn ghi `AUTO`).
- Gửi lặp cùng giá trị không tạo log rác; chỉ ghi `control_logs` khi trạng thái thực sự đổi.

**Dữ liệu đầu vào**
- Gọi `GET /api/ai/context` để lấy 1 lần: `mode`, trạng thái quạt/AC, `temperature`, `humidity`, `people_count`, `people_updated_at`.
- Nếu tự lấy riêng: `GET /api/sensor/latest`, `GET /api/people/latest`, `GET /api/device/state`.
- `people_count` chỉ mới nếu `people_updated_at` gần đây; module YOLO dừng gửi thì số người bị cũ.

**Camera**
- Lấy frame trực tiếp `http://<CAM_IP>/capture` nếu máy AI cùng mạng với CAM.
- Nếu AI ở nhà khác, dùng `proxy_snapshot_url` từ `GET /api/camera/stream-url` (qua backend / Cloudflare Tunnel), không cần JWT.
- AI **không cần** mở MJPEG proxy; chỉ cần snapshot để đếm người.
- Tần suất đếm đề xuất: 1–2 giây/lần; tránh gửi dồn dập.

**Bảo mật**
- Nếu `.env` đặt `PEOPLE_API_KEY` / `AI_API_KEY`, gửi header `X-API-Key: <key>` tương ứng.
- Không dùng token JWT của user để điều khiển; nếu dùng sẽ bị ghi lịch sử là `USER` thay vì `AI`.
- Không gọi `/api/device/command` (endpoint đó dành cho Flutter MANUAL).

---

### 10.3 ESP32 / vận hành

- `SERVER_URL` trong `esp32_main.ino` phải là IP laptop + port 8006.
- `CAM_IP` trong `.env` phải là IP thật của ESP32-CAM; sau khi đổi phải restart backend.
- ESP32 chỉ cần 2 API: `POST /api/sensor` (5s) và `GET /api/device/state` (2s); không cần JWT.
- Nên đặt IP tĩnh (hoặc DHCP reservation) cho laptop và ESP32-CAM để tránh đổi IP.
- Nếu stream không lên: kiểm tra `GET /api/camera/status`, đảm bảo CAM cùng mạng và pin/nguồn ổn định.
- `CAM_STREAM_MAX_CLIENTS` mặc định 2; ESP32-CAM tải kém, không nên tăng cao.
- `CAM_STREAM_READ_TIMEOUT_SECONDS` mặc định 30; CAM không gửi frame quá lâu sẽ bị đóng để nhả slot.
- `STREAM_TOKEN_EXPIRE_SECONDS` mặc định 120; đổi xong phải restart backend.
- `PUBLIC_BASE_URL` chỉ điền khi đã có Cloudflare Tunnel (ví dụ `https://aiot.example.com`); để trống khi chạy LAN.
- Chạy uvicorn **1 worker** (mặc định) để giới hạn `CAM_STREAM_MAX_CLIENTS` chính xác; nhiều worker thì mỗi worker đếm riêng.

---

### 10.4 Mô hình 3 nơi (Flutter 4G + AI nhà khác + backend tại nhà)

```
[Flutter điện thoại 4G] ─┐
                          ├─ HTTPS Cloudflare Tunnel → backend tại nhà
[AI service nhà khác] ────┘                              ├─ SQL Server local
                                                         ├─ ESP32 (poll state → LED/LCD)
                                                         └─ ESP32-CAM (proxy stream/snapshot)
```

- Backend, laptop và Cloudflare Tunnel phải bật **24/7** khi test từ xa.
- ESP32, ESP32-CAM và SQL Server vẫn ở LAN nhà, **không mở port** trực tiếp ra Internet.
- Flutter 4G dùng `proxy_stream_url` + stream token; AI nhà khác dùng `proxy_snapshot_url`.
- Tunnel chưa cấu hình ở bước này; khi làm, trỏ tunnel về `http://localhost:8006`, sau đó điền `PUBLIC_BASE_URL` và restart backend.

---

### 10.5 Checklist trước demo

- [ ] `python init_db.py --reset ...` tạo DB sạch + admin; đổi mật khẩu admin.
- [ ] Backend chạy `--host 0.0.0.0`, mở firewall 8006.
- [ ] ESP32 gửi sensor OK (`GET /api/sensor/latest` có dữ liệu).
- [ ] ESP32-CAM online (`GET /api/camera/status` → `online: true`).
- [ ] `POST /auth/stream-token` trả token; `GET /api/camera/stream?token=...` xem được MJPEG trong LAN.
- [ ] Thử stream bằng 4G qua Cloudflare Tunnel (sau khi cấu hình `PUBLIC_BASE_URL`).
- [ ] Module YOLO gửi `/api/people/count` đều; `people_updated_at` mới.
- [ ] Chọn đúng 1 bộ điều khiển AUTO: AI nội bộ (`AI_ENGINE_ENABLED=true`) hoặc AI bên ngoài (`false` + `/api/ai/command`).
- [ ] Flutter login được, Dashboard cập nhật, Manual bật/tắt đúng LED/LCD.
- [ ] `/api/control-logs` hiện đúng `USER` và `AI`, kèm lý do + bối cảnh.
- [ ] Kiểm tra Swagger `http://<IP_LAPTOP>:8006/docs` trước khi bàn giao.
