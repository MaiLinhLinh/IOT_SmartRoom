# AI Service — YOLO đếm người + Gemini ra lệnh điều khiển

Service Python chạy **riêng** với backend, giao tiếp hoàn toàn qua các API đã có trong `backend/api.md`
(mục 3, 3b, 6). Không sửa code backend, không cần biết IP của ESP32.

```
                 ┌──────────────────── ai_service ─────────────────────┐
ESP32-CAM ──JPEG──► [1] people_counter.py                               │
 /capture        │   YOLO (class person) → trung vị 5 khung            │
                 │   └── POST /api/people/count {count, "YOLO"} ───────┼──► BACKEND
                 │                                                     │     │ device_state
                 │ [2] decision_engine.py (mỗi 5s)                     │     │ sensor_logs ◄── ESP32 (DHT11)
                 │   GET /api/ai/context  ◄────────────────────────────┼─────┘
                 │   có thay đổi đáng kể? → prompt → Gemini → JSON      │
                 │   kiểm tra/kẹp giá trị (lỗi → luật ngưỡng dự phòng)  │
                 │   └── POST /api/ai/command {fan, ac, temp, reason} ─┼──► BACKEND → ESP32 poll 2s → LED/LCD
                 └─────────────────────────────────────────────────────┘
```

## Cấu trúc

| File | Vai trò |
|---|---|
| `main.py` | Chạy 2 chức năng song song (2 luồng), tham số dòng lệnh |
| `people_counter.py` | **Chức năng 1** — lấy ảnh, YOLO đếm người, làm mượt, gửi backend |
| `decision_engine.py` | **Chức năng 2** — đọc context, quyết định khi nào gọi LLM, kiểm tra kết quả, gửi lệnh |
| `llm_client.py` | Gọi Gemini REST `generateContent`, ép trả JSON theo schema, đổi model khi hết quota |
| `prompts.py` | System prompt (nguyên tắc điều khiển + ví dụ) và prompt dữ liệu |
| `rules.py` | Luật ngưỡng dự phòng (nhiệt độ cảm nhận theo độ ẩm và số người) |
| `backend_client.py` | Gọi API backend (`/api/people/count`, `/api/ai/context`, `/api/ai/command`, snapshot) |
| `config.py`, `.env.example` | Cấu hình |
| `tests/` | Unit test + backend giả lập để test không cần SQL Server |

## Cài đặt (Windows, conda env `IOT` như backend)

```powershell
cd IOT_SmartRoom\ai_service
conda activate IOT                 # hoặc: python -m venv venv; venv\Scripts\activate
# Có GPU NVIDIA thì cài torch bản CUDA trước (xem pytorch.org), không có thì bỏ qua
pip install -r requirements.txt
copy .env.example .env             # rồi mở .env điền các giá trị
```

Các giá trị bắt buộc trong `.env`:

- `BACKEND_URL` — IP laptop chạy backend, ví dụ `http://10.18.2.49:8006`.
- `GEMINI_API_KEY` — lấy miễn phí tại <https://aistudio.google.com/apikey>.
- `CAMERA_SOURCE` + `CAM_IP` — `esp32` nếu máy chạy AI cùng mạng với ESP32-CAM;
  `backend` nếu AI ở mạng khác (lấy ảnh qua `/api/camera/snapshot` của backend).
- `PEOPLE_API_KEY`, `AI_API_KEY` — chỉ khi `.env` của backend có đặt.

**Phía backend** phải đặt `AI_ENGINE_ENABLED=false` trong `backend/.env` rồi khởi động lại,
nếu không AI nội bộ (bảng ngưỡng cũ) sẽ ghi đè quyết định của Gemini.

## Chạy

```powershell
python main.py                               # chạy cả 2 chức năng
python main.py --only counter --show         # chỉ đếm người, mở cửa sổ xem bounding box (q để thoát)
python main.py --only llm                    # chỉ ra quyết định (số người lấy từ backend)
python main.py --source webcam:0 --only counter --show --dry-run   # test bằng webcam, không gửi gì
python main.py --only llm --once --dry-run   # gọi Gemini 1 lần, in quyết định, không gửi lệnh
```

Lần đầu chạy, Ultralytics tự tải `yolo11n.pt` (~5 MB).

## Chức năng 1 — đếm người bằng YOLO

- Model mặc định `yolo11n.pt` (bản nano, chạy CPU khoảng 50–150 ms/ảnh). Cần chính xác hơn thì dùng `yolo11s.pt`.
- Chỉ lấy class `0 = person` (COCO), ngưỡng `YOLO_CONF=0.40`.
- **Làm mượt**: lấy trung vị 5 lần đếm gần nhất, nên 1 khung bị bỏ sót / nhận nhầm không làm số người nhảy lung tung
  (tránh LLM bật/tắt thiết bị liên tục).
- Chỉ gửi backend khi số người thay đổi, cộng thêm "heartbeat" mỗi 10s để `people_updated_at` luôn mới.
- Camera lỗi liên tiếp → **không gửi số cũ**; `people_updated_at` cũ dần và bộ quyết định sẽ coi số người là "không rõ".
- ESP32-CAM trong sketch đang để độ phân giải QVGA (320×240); người ở xa sẽ khó nhận. Nên tăng lên VGA/SVGA
  trên trang web của CAM (`http://<CAM_IP>`) nếu Wi-Fi đủ khoẻ.

## Chức năng 2 — LLM phân tích và ra lệnh

1. Mỗi 5s đọc `GET /api/ai/context`. Bỏ qua nếu mode ≠ `AUTO` hoặc cảm biến cũ hơn 60s.
2. Chỉ gọi Gemini khi **có thay đổi đáng kể**: số người đổi, nhiệt độ lệch ≥ 0.5°C, độ ẩm lệch ≥ 5%,
   trạng thái thiết bị bị đổi, vừa chuyển sang AUTO, hoặc đã 2 phút chưa đánh giá lại.
   Hai lần gọi cách nhau tối thiểu 10s → vừa với giới hạn của gói miễn phí.
3. Prompt gồm: nguyên tắc (tiện nghi 24–27°C, ưu tiên quạt vì tiết kiệm điện, chỉ bật AC khi nóng/ẩm/đông người,
   tránh bật tắt liên tục, phòng trống thì tắt hết…), 3 ví dụ mẫu, và dữ liệu hiện tại (số người + độ mới,
   nhiệt độ, độ ẩm, trạng thái quạt/AC, lý do lần trước).
4. Gemini bắt buộc trả JSON theo schema `{fan_level, ac_on, ac_temp, ai_reason}`.
5. **Kiểm tra đầu ra**: ép kiểu, kẹp `fan_level` 0–3, `ac_temp` trong `AC_MIN_TEMP`–`AC_MAX_TEMP` (20–30°C);
   guardrail: phòng chắc chắn trống → luôn tắt hết.
6. Gửi `POST /api/ai/command`. `ai_reason` có tiền tố `[Gemini]` hoặc `[Luật dự phòng]` để Dashboard và trang lịch sử
   cho biết quyết định đến từ đâu.

**Dự phòng**: Gemini lỗi mạng, hết quota (HTTP 429), sai key, trả JSON hỏng → thử model tiếp theo trong
`GEMINI_MODELS` → vẫn lỗi thì dùng `rules.py`. Nếu số người "không rõ" và Gemini không khả dụng → giữ nguyên trạng thái.

Mỗi quyết định được ghi vào `logs/decisions.jsonl` (đầu vào, nguồn quyết định, model, độ trễ, lỗi) —
dùng để thống kê trong báo cáo (tỉ lệ Gemini/luật, thời gian phản hồi…).

## Test

```powershell
python tests/test_logic.py                   # 16 unit test, không cần camera/Gemini/backend

# Test end-to-end với backend giả lập (không cần SQL Server):
set MOCK_SNAPSHOT=duong_dan_anh_co_nguoi.jpg
uvicorn tests.mock_backend:app --port 8006
# cửa sổ khác:
python tests/e2e_scenario.py                # 7 bước: YOLO thật + Gemini giả (hết quota, đổi model…), AUTO/MANUAL
# hoặc chạy service thật trỏ vào backend giả:  python main.py --source backend -v
# xem kết quả: http://localhost:8006/test/state
```

## Lưu ý

- Thời gian `people_updated_at` / `sensor_timestamp` là giờ local của backend (không có múi giờ):
  máy chạy AI và máy chạy backend phải cùng múi giờ, đồng hồ đúng.
- Tên model Gemini thay đổi theo thời gian; nếu báo `HTTP 404 không có model`, sửa `GEMINI_MODELS`
  theo danh sách tại <https://ai.google.dev/gemini-api/docs/models>.
- Giới hạn request miễn phí xem trong AI Studio (giới hạn tính theo project, quota ngày reset lúc 0h giờ Thái Bình Dương).
