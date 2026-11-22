# Kế hoạch cập nhật Backend & Frontend để khớp AI service

> Đối tượng: bạn Backend (FastAPI) và bạn Frontend (Flutter).
> Cơ sở: backend **v4** trên nhánh `mailinh` (3 quạt `fan1/fan2/fan3`) + `ai_service/` (YOLO + planner năng lượng + Gemini).
> Chi tiết thuật toán: `docs/TOI_UU_HE_THONG.md`. Cập nhật: 20/11/2026.

---

## 0. Hiện trạng

| Thành phần | Trạng thái |
|---|---|
| Backend v4 (device, sensor, logs, control-logs) | ✅ Đã chuyển 3 quạt (nhánh `mailinh`, chưa merge `master`) |
| Backend phần AI (`/api/ai/context`, `/api/ai/command`, `ai_engine.py`) | ❌ Vẫn dùng `fan_level` → `/api/ai/context` trả **500**, `/api/ai/command` lỗi |
| `ai_service` | ✅ Đã theo v4, có bản vá sẵn cho backend: `ai_service/docs/backend_ai_v4.patch` |
| Người dùng nhập công suất thiết bị | ⏳ Phía AI đã sẵn sàng (đọc khối `config`), backend/Flutter chưa làm |
| Flutter | ⏳ Đang chuyển sang 3 quạt theo `change.md` |

---

## 1. Lộ trình

| Giai đoạn | Mục tiêu | Việc | Ai làm |
|---|---|---|---|
| **1 — P0** | AUTO chạy được end-to-end để demo | B1, B2, B3, F1 | Backend, Frontend |
| **2 — P1** | Người dùng nhập thông số thiết bị trên app | B4, F2, F3 | Backend, Frontend |
| **3 — P2** | Dashboard hiển thị rõ AI + thống kê điện năng | B5, B6, F4, F5, A1 | Backend, Frontend, AI |

---

## 2. Backend

### B1 — Sửa phần AI sang v4 (P0)

Áp bản vá có sẵn (đã chạy thử với backend thật trên SQLite: context trả `fan1/2/3`, lệnh AI ghi `control_logs`,
MANUAL → 409, body cũ `fan_level` → 422):

```powershell
cd IOT_SmartRoom
git apply --ignore-whitespace ai_service/docs/backend_ai_v4.patch
```

Nội dung bản vá:

| File | Thay đổi |
|---|---|
| `routers/ai.py` | `/context` trả `fan1, fan2, fan3`; `/command` gọi `apply_command(fan1=..., fan2=..., fan3=...)` |
| `schemas.py` | `AIDecisionIn`: bỏ `fan_level`, thêm `fan1/fan2/fan3: bool`, `extra="forbid"` |
| `services/ai_engine.py` | AI nội bộ (dự phòng) đổi "mức n" thành bật quạt 1..n |

**Nghiệm thu**: `GET /api/ai/context` trả 200 có `fan1/fan2/fan3`; `python ai_service/main.py --only llm --once`
gửi lệnh thành công và `/api/control-logs?actor_type=AI` có bản ghi.

### B2 — Merge & cấu hình (P0)

- Merge `mailinh` (kèm B1) vào `master` sau khi cả nhóm đồng ý.
- `backend/.env`: `AI_ENGINE_ENABLED=false`, `CAM_IP=10.18.3.98`; nếu đặt `PEOPLE_API_KEY` / `AI_API_KEY`
  thì báo bạn AI điền giống hệt vào `ai_service/.env`.
- Nhắc người dùng: `device_state` mặc định **MANUAL** → phải chuyển AUTO thì AI mới điều khiển.

### B3 — Dọn repo (P0)

- Thêm vào `.gitignore` gốc: `__pycache__/`, `*.pyc`, `logs/`, `*.pt`; gỡ file đã lỡ commit:
  `git rm -r --cached backend/__pycache__ backend/routers/__pycache__ backend/services/__pycache__`.
- `backend/esp32_sim.py`: bỏ API key ghi cứng (`--people-key` mặc định `""`, đọc từ biến môi trường).
- Cập nhật `api.md`: bỏ các cảnh báo "phần AI chưa cập nhật v4" sau khi xong B1.

### B4 — Cấu hình thiết bị `device_config` (P1)

Lưu thông số người dùng nhập; `ai_service` tự đọc qua `/api/ai/context` (không cần sửa code AI).

**Bảng (SQL Server)** — `migration_v5.sql`:

```sql
CREATE TABLE dbo.device_config (
    id               INT      NOT NULL DEFAULT 1 PRIMARY KEY,   -- luôn 1 dòng
    fan1_power_w     FLOAT    NOT NULL DEFAULT 45,
    fan2_power_w     FLOAT    NOT NULL DEFAULT 45,
    fan3_power_w     FLOAT    NOT NULL DEFAULT 45,
    fan1_available   BIT      NOT NULL DEFAULT 1,
    fan2_available   BIT      NOT NULL DEFAULT 1,
    fan3_available   BIT      NOT NULL DEFAULT 1,
    ac_available     BIT      NOT NULL DEFAULT 1,
    ac_power_w       FLOAT    NOT NULL DEFAULT 900,
    comfort_max_temp FLOAT    NOT NULL DEFAULT 26,
    ac_set_min       INT      NOT NULL DEFAULT 20,
    ac_set_max       INT      NOT NULL DEFAULT 28,
    updated_at       DATETIME NULL,
    updated_by       INT      NULL REFERENCES dbo.users(id) ON DELETE SET NULL
);
INSERT INTO dbo.device_config (id) VALUES (1);
```

**Model** (`models.py`) — theo đúng kiểu các model hiện có:

```python
class DeviceConfig(Base):
    """device_config: thông số thiết bị người dùng nhập (1 row, id=1)."""
    __tablename__ = "device_config"
    id               = Column(Integer, primary_key=True, default=1)
    fan1_power_w     = Column(Float, nullable=False, default=45)
    fan2_power_w     = Column(Float, nullable=False, default=45)
    fan3_power_w     = Column(Float, nullable=False, default=45)
    fan1_available   = Column(Boolean, nullable=False, default=True)
    fan2_available   = Column(Boolean, nullable=False, default=True)
    fan3_available   = Column(Boolean, nullable=False, default=True)
    ac_available     = Column(Boolean, nullable=False, default=True)
    ac_power_w       = Column(Float, nullable=False, default=900)
    comfort_max_temp = Column(Float, nullable=False, default=26)
    ac_set_min       = Column(Integer, nullable=False, default=20)
    ac_set_max       = Column(Integer, nullable=False, default=28)
    updated_at       = Column(DateTime, nullable=True)
    updated_by       = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
```

**API** — JSON dùng mảng 3 phần tử cho quạt (khớp `ai_service/docs/device_config_schema.json`):

| Endpoint | Quyền | Mô tả |
|---|---|---|
| `GET /api/device/config` | JWT | Trả cấu hình hiện tại |
| `PUT /api/device/config` | JWT **admin** (`get_current_admin`) | Cập nhật; chỉ gửi trường cần đổi, trường thiếu giữ nguyên |
| `GET /api/ai/context` | như cũ | Thêm khối `"config": {...}` (cùng định dạng GET ở trên) |

```json
// GET /api/device/config  — và khối "config" trong /api/ai/context
{
  "fan_power_w": [45, 45, 60],
  "fans_available": [true, true, false],
  "ac_available": true,
  "ac_power_w": 900,
  "comfort_max_temp": 26.0,
  "ac_set_min": 20,
  "ac_set_max": 28,
  "updated_at": "2026-11-20T21:00:00"
}
```

**Kiểm tra hợp lệ** (Pydantic `Field`, giống khoảng bên AI để hai bên thống nhất):

| Trường | Ràng buộc |
|---|---|
| `fan_power_w` | đúng 3 số, mỗi số 5–300 |
| `fans_available` | đúng 3 bool |
| `ac_power_w` | 200–4000 |
| `comfort_max_temp` | 23–29 |
| `ac_set_min`, `ac_set_max` | 16–32 và `ac_set_min ≤ ac_set_max` |

Ghi `control_logs` (actor = admin, description "Cập nhật cấu hình thiết bị: ...") để có lịch sử thay đổi.

**Nghiệm thu**: đổi `ac_available=false` trên Swagger → trong ≤ 5 s log `ai_service` in
"Cập nhật hồ sơ thiết bị từ backend" và lệnh tiếp theo không bật điều hòa.

### B5 — Trường AI có cấu trúc (P2)

Hiện Dashboard chỉ có chuỗi `ai_reason` (ví dụ `[Gemini] ... (~774 W)`). Để Flutter không phải tách chuỗi:

- Thêm cột vào `device_state` và `control_logs`: `ai_source NVARCHAR(10)` (`GEMINI|RULE|AUTO`),
  `ai_mode NVARCHAR(20)` (`chỉ quạt|kết hợp|chỉ điều hòa|tắt hết|làm mát nhanh|chờ tắt`), `est_power_w FLOAT`.
- `AIDecisionIn`: thêm 3 trường **tùy chọn** trên; `DeviceStateOut`, `ControlLogOut` trả thêm.
- Phối hợp với **A1** (bên AI chỉ gửi khi backend đã hỗ trợ, vì `AIDecisionIn` dùng `extra="forbid"`).

### B6 — Thống kê điện năng (P2)

`GET /api/energy/summary?from_date=&to_date=` (JWT):

- Duyệt `control_logs` theo thời gian; mỗi khoảng giữa hai lần đổi trạng thái có công suất
  `Σ fan_power_w[i] (quạt bật) + AC(ac_temp)` với `AC(T) = ac_power_w × (1 − 0.06 × (T − 24))`.
- Năng lượng `kWh = Σ P × Δt`; so với cách dùng "chỉ bật AC 25°C" trong các khoảng có người và nóng.
- Trả: `kwh`, `kwh_baseline`, `saving_percent`, tỉ lệ thời gian theo `ai_mode`, số lần AC bật/tắt.

---

## 3. Frontend (Flutter)

### F1 — Hoàn tất chuyển 3 quạt (P0)

Theo `change.md` của backend: model `fan1/fan2/fan3`, Dashboard 3 nút bật/tắt độc lập, trang 4 và 6 hiển thị
trạng thái từng quạt (log cũ trước v4 hiển thị `--`).

### F2 — Trang Cài đặt thiết bị (P1, chỉ admin)

| Ô nhập | Kiểu | Gợi ý hiển thị |
|---|---|---|
| Công suất quạt 1/2/3 (W) | số, 5–300 | "Xem nhãn quạt. Quạt đứng ~35–60 W, quạt trần ~60–80 W" |
| Quạt 1/2/3 dùng được | công tắc | "Tắt nếu quạt hỏng hoặc phòng không có" |
| Phòng có điều hòa | công tắc | — |
| Công suất điều hòa (W) | số, 200–4000 | "**Mục Input / Công suất tiêu thụ** trên nhãn, KHÔNG phải BTU. 1 HP ~750–900 W, 1.5 HP ~1100–1300 W" |
| Nhiệt độ mong muốn (°C) | slider 23–29, bước 0.5 | "Thấp hơn = mát hơn nhưng tốn điện hơn. Khuyến nghị 26" |
| Nhiệt độ đặt điều hòa thấp nhất / cao nhất | số 16–32 | "Có quạt thì 27–28°C là hợp lý" |

- Nạp bằng `GET /api/device/config`, lưu bằng `PUT /api/device/config` (gửi trường đã đổi).
- Nhãn, khoảng, gợi ý lấy từ `ai_service/docs/device_config_schema.json` để hai bên khớp nhau.
- Người dùng không phải admin: chỉ xem. Lỗi `422` → hiện `detail`; `403` → "Chỉ admin được chỉnh".
- Nút "Khôi phục mặc định" (45/45/45 W, 900 W, 26°C, 20–28°C).

### F3 — Thẻ "Lý do AI" trên Dashboard (P1)

- Ẩn khi `mode = MANUAL` hoặc `ai_reason = null`.
- Tách tiền tố để hiện nhãn nguồn: `[Gemini]` → "AI (Gemini)", `[Luật dự phòng]` → "Tính toán dự phòng",
  `[Tự động]` → "Tự động (phòng trống)". Sau B5 thì đọc thẳng `ai_source`, `ai_mode`, `est_power_w`.
- Hiện công suất ước tính (`~774 W`) và chế độ (chỉ quạt / kết hợp / chỉ điều hòa) bằng icon.

### F4 — Lịch sử thao tác hiển thị AI (P2)

Trang 6: với `actor_type = AI`, hiện nhãn nguồn + chế độ + công suất; lọc theo `ai_mode` khi có B5.

### F5 — Màn hình Điện năng (P2)

Dựa trên B6: tổng kWh theo ngày/tuần, so với "chỉ dùng điều hòa", biểu đồ tỉ lệ thời gian theo chế độ.

---

## 4. Phía AI (để khớp các bước trên)

| Việc | Khi nào | Trạng thái |
|---|---|---|
| Đọc khối `config` từ `/api/ai/context`, áp dụng ngay | B4 xong | ✅ Đã làm sẵn |
| **A1**: gửi thêm `ai_source`, `ai_mode`, `est_power_w` trong `/api/ai/command` (bật bằng cờ trong `.env`) | B5 xong | ⏳ Làm khi backend hỗ trợ |
| Cập nhật `tests/mock_backend.py` theo API mới | Mỗi lần API đổi | ⏳ |

Tương thích: `ai_service` chạy được với backend **có hoặc chưa có** `config` (thiếu thì dùng mặc định/`.env`).

---

## 5. Kiểm thử chung trước demo

- [ ] B1: `GET /api/ai/context` → 200, có `fan1/fan2/fan3`.
- [ ] `backend/.env` có `AI_ENGINE_ENABLED=false`; thiết bị ở **AUTO**.
- [ ] `ai_service`: log `YOLO: thô=… → làm mượt=…` và `→ Đã gửi lệnh: quạt …`.
- [ ] LED trên mạch đổi theo `fan1/fan2/fan3` trong ~2 s; LCD hiện `F:110 AC:ON 28C`.
- [ ] Dashboard hiện thẻ lý do AI; chuyển MANUAL → AI dừng (lệnh AI trả 409).
- [ ] Trang 6 có bản ghi `actor_type = AI` kèm lý do.
- [ ] (P1) Đổi cấu hình trên app → AI áp dụng ở vòng kế tiếp.
- [ ] Không có mạch: chạy `backend/esp32_sim.py --people` để giả lập cảm biến + số người.
- [ ] Demo chuyển chế độ: `python ai_service/simulate.py` (mode override, không chạy `main.py` cùng lúc) —
      Flutter nên hiện rõ nhãn `[Mô phỏng]` trong thẻ lý do AI.
