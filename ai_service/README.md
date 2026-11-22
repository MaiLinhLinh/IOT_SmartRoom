# AI Service — YOLO đếm người + Gemini điều khiển 3 quạt & điều hòa (backend v4)

Service Python chạy **riêng** với backend, chỉ giao tiếp qua API (`backend/api.md`):

```
ESP32-CAM ──JPEG──► [1] people_counter.py: YOLO → trung vị 5 khung → POST /api/people/count ─► BACKEND
                                                                                                  │
                    [2] decision_engine.py (mỗi 5s)                                               │
                        GET /api/ai/context ◄──────────────────────────────────────────────────────┤ ◄── ESP32 (DHT11)
                        rules.plan(): tính 8 phương án (0–3 quạt × AC bật/tắt) → rẻ nhất mà đủ mát │
                        Gemini đọc bảng phương án + bối cảnh → chọn & viết lý do                   │
                        ràng buộc vận hành (máy nén AC, phòng trống, chống bật/tắt liên tục)       │
                        POST /api/ai/command {fan1, fan2, fan3, ac_on, ac_temp, ai_reason} ───────►│ → ESP32 poll 2s → LED/LCD
```

## Cấu trúc

| File | Vai trò |
|---|---|
| `main.py` | Chạy 2 chức năng song song, tham số dòng lệnh |
| `people_counter.py` | **Chức năng 1** — lấy ảnh, YOLO đếm người, lọc trung vị, gửi backend |
| `rules.py` | **Bộ lập kế hoạch tiện nghi – năng lượng**: mô hình nhiệt độ cảm nhận, hiệu ứng quạt, công suất; chọn tập quạt + AC |
| `simulate.py`, `simulator.py` | **Giả lập kịch bản demo** (nhiệt độ/độ ẩm/số người thay đổi theo kịch bản) |
| `scenarios/` | Kịch bản tự tạo dạng JSON (có file ví dụ) |
| `device_profile.py` | **Hồ sơ thiết bị & sở thích** (công suất từng quạt, quạt/AC nào có, nhiệt độ mong muốn): mặc định → `.env` → backend |
| `decision_engine.py` | **Chức năng 2** — khi nào quyết định, hỏi Gemini, kiểm tra kết quả, ràng buộc vận hành, gửi lệnh |
| `prompts.py` | System prompt (chiến lược) + prompt dữ liệu kèm bảng phương án |
| `llm_client.py` | Gọi Gemini REST, ép JSON theo schema, đổi model khi lỗi/hết quota |
| `backend_client.py` | Gọi API backend (contract v4) |
| `docs/device_config_schema.json` | Mô tả các trường hồ sơ thiết bị (kiểu, mặc định, khoảng, nhãn, gợi ý) cho backend/Flutter làm form nhập |
| `docs/backend_ai_v4.patch` | Bản vá cho backend: `/api/ai/context`, `/api/ai/command`, `ai_engine.py` sang `fan1/fan2/fan3` |
| `tests/` | Unit test, backend giả v4 + Gemini giả, kịch bản end-to-end |

## Cài đặt & chạy

```powershell
cd IOT_SmartRoom\ai_service
pip install -r requirements.txt
copy .env.example .env      # điền BACKEND_URL, GEMINI_API_KEY, CAM_IP (+ API key nếu backend có đặt)
python main.py              # cả 2 chức năng
python main.py --only counter --show          # chỉ đếm người, xem khung hình
python main.py --only llm --once --dry-run    # ra 1 quyết định, không gửi
```

**Điều kiện phía backend**

1. `backend/.env` có `AI_ENGINE_ENABLED=false`.
2. Backend v4 phải sửa phần AI sang 3 quạt (hiện `/api/ai/context` trả 500, `/api/ai/command` lỗi).
   Bản vá sẵn: `git apply ai_service/docs/backend_ai_v4.patch` (đã chạy thử với backend thật trên SQLite).
3. Thiết bị phải ở chế độ **AUTO** (mặc định là MANUAL).

---

## Chiến lược: khi nào chỉ quạt, chỉ điều hòa, hay kết hợp?

### Ý tưởng

Bài toán tối ưu nhỏ có ràng buộc, giải bằng cách thử hết 8 phương án (0–3 quạt × AC bật/tắt,
mỗi phương án có AC thì chọn nhiệt độ đặt **cao nhất** vẫn đủ mát):

> **chọn** (số quạt *n*, AC bật/tắt, nhiệt độ đặt *Tset*)
> **sao cho** nhiệt độ cảm nhận ≤ `COMFORT_MAX_TEMP` (26°C)
> **và** công suất ước tính nhỏ nhất.

### Mô hình (đơn giản, giải thích được, mọi hệ số chỉnh trong `.env`)

| Thành phần | Công thức / giá trị mặc định | Cơ sở |
|---|---|---|
| Nhiệt độ cảm nhận (không AC) | `T + phạt_ẩm(RH) + phạt_đông(N) − hiệu_ứng_quạt(n, RH)` | |
| Phạt độ ẩm | `0.075 × (RH − 60)` khi RH > 60%, tối đa +1.9°C | Độ ẩm cao làm mồ hôi khó bay hơi, thấy oi hơn |
| Phạt đông người | `0.25°C × (N − 2)`, tối đa +1.5°C | Mỗi người toả ~70–100 W nhiệt |
| Hiệu ứng làm mát của quạt | 1 quạt 1.5°C, 2 quạt 2.5°C, 3 quạt 3.0°C; ×0.75 khi RH ≥ 70%, ×0.5 khi RH ≥ 80% | ASHRAE 55 cho phép bù tới ~3°C bằng tốc độ gió; gió kém hiệu quả khi ẩm |
| Khi bật AC | `Tset + phạt_đông − hiệu_ứng_quạt` (AC hút ẩm nên bỏ phạt ẩm) | |
| Công suất quạt | 45 W/quạt (`FAN_POWER_W`) | Quạt đứng/trần phổ biến 40–75 W |
| Công suất AC | 900 W ở 24°C, giảm 6%/°C đặt cao hơn (`AC_POWER_W`, `AC_SAVING_PER_DEGREE`) | Máy ~1 HP; các khuyến nghị tiết kiệm điện thường nêu 3–10%/°C |

### Ba vùng hoạt động (kết quả của mô hình)

| Vùng | Khi nào | Lý do tiết kiệm |
|---|---|---|
| **Chỉ quạt** | Phần "thiếu tiện nghi" ≤ hiệu ứng tối đa của quạt. Ví dụ 2 người: RH 60% → trên 26°C đến 29°C (1→3 quạt); RH 72% → tới ~27.3°C | Quạt ~45 W, chỉ ~5% điện của AC; dùng **ít quạt nhất** đủ mát |
| **Kết hợp** | Quạt không đủ: nóng hơn (> 29°C khi RH 60%, > ~27.3°C khi RH 72%) hoặc đông người | Mỗi quạt cho phép nâng Tset ~1–1.5°C (≈ 55–80 W điện AC) > 45 W điện quạt → AC 27–28°C + 1–3 quạt rẻ hơn AC 25°C một mình |
| **Chỉ điều hòa** | Rất ẩm (RH ≥ 80%) và quạt không đủ, ví dụ nồm ẩm 26–28°C | Gió quạt chỉ còn ½ hiệu quả, không bù được điện quạt; AC vừa làm mát vừa hút ẩm. Khi AC hút ẩm xuống < 80% → tự chuyển sang **kết hợp** và nâng Tset |

Bảng tham khảo (2 người, phòng đang tắt hết; công suất ở trạng thái ổn định):

| Nhiệt độ | Độ ẩm | Quyết định | Công suất ước tính | "Chỉ AC 25°C" |
|---|---|---|---|---|
| 25.5°C | 60% | tắt hết | 0 W | 0 W |
| 27.0°C | 60% | 1 quạt | 45 W | 846 W |
| 28.5°C | 60% | 2 quạt | 90 W | 846 W |
| 29.0°C | 60% | 3 quạt | 135 W | 846 W |
| 30.0°C | 60% | 2 quạt + AC 28°C (ổn định) | 774 W | 846 W |
| 28.0°C | 72% | 1 quạt + AC 27°C | 783 W | 846 W |
| 26.0–27.0°C | 82% | chỉ AC 26°C | 792 W | 846 W |

### Hồ sơ thiết bị — thông số người dùng nhập

Mọi thông số **phụ thuộc phòng/thiết bị** được gom vào `device_profile.py`, có sẵn giá trị mặc định nên chạy được
ngay. Thứ tự ưu tiên (lớp sau ghi đè lớp trước, từng trường):

```
mặc định trong code → .env của ai_service → GET /api/devices/info (backend v5) → /api/ai/context "config"
```

- **Backend v5 (MaiLinh) đã có `GET/PUT /api/devices/info`**: người dùng nhập hãng + công suất `fan1/fan2/fan3/ac`
  trên app. ai_service đọc lại mỗi `DEVICE_INFO_REFRESH_SECONDS` (30 s) và chỉ lấy thiết bị đã được nhập
  (`power_is_default = false`); thiết bị chưa nhập giữ giá trị `.env`/mặc định. Không cần sửa ai_service.
- Các trường backend **chưa có** (quạt/AC nào dùng được, nhiệt độ mong muốn, khoảng nhiệt độ đặt) vẫn lấy từ `.env`;
  khi backend thêm, chỉ cần trả trong khối `config` của `/api/ai/context`.

| Trường | Mặc định | Khoảng hợp lệ | Ý nghĩa |
|---|---|---|---|
| `fan_power_w` | `[45, 45, 45]` W | 5–500 W mỗi quạt | Công suất điện từng quạt (1 số = cả 3 quạt) |
| `fans_available` | `[true, true, true]` | — | Quạt nào dùng được (quạt hỏng → không bao giờ bật) |
| `ac_available` | `true` | — | Phòng có điều hòa không (không → chỉ dùng quạt) |
| `ac_power_w` | `900` W | 200–5000 W | Công suất **điện** của điều hòa (mục Input trên nhãn, không phải BTU) |
| `comfort_max_temp` | `26.0` °C | 23–29 °C | Nhiệt độ cảm nhận tối đa mong muốn |
| `ac_set_min` / `ac_set_max` | `20` / `28` °C | 16–32 °C | Khoảng nhiệt độ đặt điều hòa cho phép |

Mở rộng sau này (các trường backend chưa có):
```json
GET /api/ai/context → { ..., "config": { "fans_available": [true, true, false], "comfort_max_temp": 26.5 } }
```
- Chỉ cần gửi các trường người dùng đã nhập; trường thiếu dùng giá trị `.env`/mặc định.
- Giá trị sai kiểu hoặc ngoài khoảng bị bỏ qua (ghi cảnh báo), không làm dừng service.
- Đổi cấu hình → ai_service áp dụng ở lần đọc kế tiếp (≤ 30 s, không cần khởi động lại) và quyết định lại.
- Mô tả đầy đủ cho form nhập: `docs/device_config_schema.json` (hoặc chạy `python device_profile.py`).

Planner tận dụng hồ sơ này: chọn **quạt cụ thể** tốn ít điện nhất (rồi ít bật/tắt nhất), bỏ qua quạt hỏng;
phòng không có AC → chỉ dùng quạt (không đủ mát thì bật hết quạt khả dụng); không có quạt → chỉ dùng AC.
Công suất AC ảnh hưởng trực tiếp tới vùng "kết hợp" / "chỉ AC": AC càng tốn điện, kết hợp quạt càng có lợi
(ví dụ 30°C, 60%: AC 2000 W → kết hợp quạt; AC inverter ~300 W → chỉ AC là rẻ nhất).

### Ràng buộc vận hành (để chạy thật ổn định)

- **Biên trễ 0.5°C** (`HYSTERESIS`): phương án làm phòng ấm hơn hiện tại phải mát hơn ngưỡng 0.5°C mới được chọn
  → không bật/tắt liên tục khi nhiệt độ dao động (DHT11 sai số ±2°C, ±5%).
- **Nhiệt độ "tự nhiên"**: khi AC chạy, nhiệt độ/độ ẩm đo được đã bị AC kéo xuống. Hệ thống nhớ nhiệt độ & độ ẩm
  lúc trước khi bật AC để đánh giá "tắt AC, chỉ dùng quạt có đủ không"; không biết thì chỉ tắt AC khi phòng mát hơn
  Tset ≥ 1°C (`AC_OFF_MARGIN`). Tránh vòng lặp AC làm mát → tắt AC → nóng lại → bật AC.
- **Bảo vệ máy nén**: AC bật ít nhất 10 phút mới tắt, tắt ít nhất 5 phút mới bật lại
  (`AC_MIN_ON_SECONDS`, `AC_MIN_OFF_SECONDS`); trong lúc chờ bật lại thì dùng 3 quạt.
- **Làm mát nhanh**: vừa bật AC mà phòng nóng hơn Tset ≥ 2°C → bật hết quạt khả dụng cho mát ngay (trừ khi rất ẩm).
- **Phòng trống**: tắt quạt ngay; AC giữ thêm 3 phút ở 28°C rồi mới tắt (`EMPTY_AC_GRACE_SECONDS`) vì người có thể
  quay lại, khởi động lại máy nén tốn hơn. Không gọi Gemini khi phòng trống (tiết kiệm quota).
- **Không gọi Gemini khi không cần**: nếu chỉ là đánh giá định kỳ / nhiệt độ, độ ẩm đổi nhẹ mà planner xác nhận
  trạng thái hiện tại vẫn tối ưu → giữ nguyên, không gọi Gemini, không gửi lệnh (tiết kiệm quota, giảm độ trễ).

### Vai trò của Gemini

Planner tính sẵn bảng 8 phương án (cảm nhận, công suất, đạt tiện nghi hay không) và phương án đề xuất.
Gemini đọc bảng cùng bối cảnh (số người, xu hướng, lý do lần trước, ràng buộc máy nén), chọn cấu hình cuối và viết
lý do tiếng Việt cho Dashboard. Gemini được phép chọn khác planner nhưng **không được** chọn phương án làm phòng nóng
hơn ngưỡng > 1°C khi còn phương án đạt (comfort guard). Gemini lỗi → dùng phương án planner (`[Luật dự phòng]`).

Mỗi quyết định ghi vào `logs/decisions.jsonl`: phương án planner, quyết định cuối, công suất ước tính và công suất
nếu "chỉ bật AC 25°C" → dùng để thống kê mức tiết kiệm trong báo cáo.

### Giới hạn

- Các hệ số là **ước lượng** (không đo công suất thật); công suất thiết bị nên lấy từ nhãn (hồ sơ thiết bị),
  `FAN_COOLING_EFFECT` chỉnh theo cảm nhận thực tế của nhóm.
- Không có cảm biến nhiệt độ ngoài trời, nên chưa dự báo được tải lạnh; không biết vị trí người so với từng quạt
  (có thể mở rộng: dùng toạ độ bounding box YOLO để chọn quạt gần người).

---

## Demo bằng kịch bản giả lập

Khi demo trực tiếp, nhiệt độ/độ ẩm trong phòng gần như không đổi nên không thấy được hệ thống chuyển chế độ.
`simulate.py` cho nhiệt độ, độ ẩm, số người **thay đổi theo kịch bản**, AI ra quyết định như thật và (tùy chế độ)
gửi lệnh thật xuống backend → LED/LCD trên mạch và Dashboard đổi theo.

```powershell
python simulate.py --list                          # xem kịch bản
python simulate.py --mode offline                  # tập dượt trên máy, không cần backend/mạch (~4.5 phút)
python simulate.py                                 # DEMO thật: kịch bản "full", mode override
python simulate.py --scenario chi-dieu-hoa         # chỉ một chế độ
python simulate.py --speed 2                       # nhanh gấp đôi
python simulate.py --with-counter --real-people    # nhiệt độ/độ ẩm giả + số người THẬT từ camera
python simulate.py --dry-run                       # chạy với backend thật nhưng không gửi lệnh
python simulate.py --scenario scenarios/vi_du_tu_tao.json
```

### Ba cách nối với hệ thống (`--mode`)

| Mode | AI đọc dữ liệu | Lệnh | Dashboard hiện nhiệt độ | Dùng khi |
|---|---|---|---|---|
| `offline` | backend giả trong bộ nhớ | không ra ngoài | — | Tập dượt, lấy kết quả cho báo cáo |
| `override` (mặc định) | context thật, **thay** nhiệt độ/độ ẩm/số người bằng số giả lập | **gửi thật** | số thật (DHT11) | Demo: LED/LCD + lý do AI đổi theo kịch bản, không đụng dữ liệu cảm biến |
| `backend` | đẩy số giả lên `/api/sensor`, `/api/people/count` rồi đọc lại | gửi thật | **số giả lập** | Muốn Dashboard hiện đúng số giả lập — phải rút dây DATA của DHT11 (ESP32 đọc lỗi sẽ không gửi), nếu không số thật/giả xen kẽ |

Lý do AI luôn có tiền tố **`[Mô phỏng]`** (thấy trên Dashboard và trang Lịch sử thao tác); mỗi dòng
`logs/decisions.jsonl` có trường `"simulation"` = tên kịch bản → không lẫn với dữ liệu thật.

### Kịch bản `full` (~4.5 phút)

| # | Bước | Giả lập | Kết quả mong đợi |
|---|---|---|---|
| 1 | Phòng mát | 25°C, 60%, 2 người | tắt hết |
| 2 | Hơi ấm | 27°C, 60% | chỉ quạt (1 quạt) |
| 3 | Ấm hơn | 28.5°C, 60% | chỉ quạt (2 quạt) |
| 4 | Nóng dần | 29 → 31°C, 62% | kết hợp: AC 28°C + quạt (làm mát nhanh: bật hết quạt) |
| 5 | Điều hòa đã kéo nhiệt xuống | 28.4°C, 60% | kết hợp ổn định: bớt quạt |
| 6 | Đông người | 6 người | kết hợp: thêm quạt |
| 7 | Mọi người ra về | 0 người | tắt quạt ngay, giữ AC 28°C ~10 s rồi tắt hết |
| 8 | Có người, trời nồm ẩm | 27°C, 85% | chỉ điều hòa |
| 9 | Điều hòa đã hút ẩm | 27°C, 68% | kết hợp |

Kịch bản ngắn: `chi-quat`, `ket-hop`, `chi-dieu-hoa`, `phong-trong`. Cuối mỗi bước in ✓/✗ so với kết quả mong đợi,
cuối kịch bản in bảng tổng kết. Đã chạy thử: 5 kịch bản dựng sẵn đều đạt 100% (planner, Gemini giả, backend thật trên SQLite).

**Thời gian rút gọn cho demo** (mặc định; `--real-timing` để dùng thời gian thật): AC bật tối thiểu 20 s (thật: 10 phút),
tắt tối thiểu 10 s (thật: 5 phút), phòng trống chờ 10 s (thật: 3 phút), kiểm tra mỗi 2 s, 2 lần gọi Gemini cách nhau ≥ 3 s.

### Chuẩn bị trước buổi demo

1. Backend đã áp bản vá AI v4, `AI_ENGINE_ENABLED=false`, thiết bị ở **AUTO** (simulate.py cảnh báo nếu đang MANUAL).
2. **Không chạy `main.py` cùng lúc** với `simulate.py` (hai bộ quyết định sẽ tranh nhau). Cần đếm người thật thì dùng
   `simulate.py --with-counter`.
3. Chạy tập dượt `--mode offline` trước; kịch bản `full` gọi Gemini khoảng 10 lần (vừa gói miễn phí).
4. LCD trên mạch hiện nhiệt độ **thật** từ DHT11 (ở mode `override`); chế độ AUTO, trạng thái quạt/AC và lý do AI
   trên Dashboard thay đổi theo kịch bản.

### Tự tạo kịch bản (JSON)

```json
{
  "title": "Mô tả",
  "steps": [
    {"name": "Nắng gắt", "duration": 40, "temperature": [28.5, 31.5], "humidity": 58, "people": 4,
     "note": "Câu giải thích hiện cho người xem", "expect": ["kết hợp"]}
  ]
}
```
`temperature`/`humidity`: một số, hoặc `[từ, đến]` để tăng/giảm dần trong bước. `people`: bỏ trống = dùng số người thật
(YOLO). `expect`: `tắt hết` | `chỉ quạt` | `kết hợp` | `chỉ điều hòa`. Xem `scenarios/vi_du_tu_tao.json`.

## Test

```powershell
python tests/test_logic.py      # 33 unit test (planner 3 vùng, hồ sơ thiết bị, trễ, máy nén, phòng trống, Gemini...)

# End-to-end với backend giả v4 + Gemini giả (không cần SQL Server):
set MOCK_SNAPSHOT=duong_dan_anh_co_nguoi.jpg
uvicorn tests.mock_backend:app --port 8006
python tests/e2e_scenario.py    # chỉ quạt → kết hợp → chỉ AC → comfort guard → hồ sơ mới từ backend → phòng trống → MANUAL
```

## Lưu ý

- Thời gian `people_updated_at` / `sensor_timestamp` là giờ local của backend: máy AI và backend cùng múi giờ.
- Tên model Gemini thay đổi theo thời gian; lỗi `HTTP 404 không có model` thì sửa `GEMINI_MODELS`
  (danh sách: <https://ai.google.dev/gemini-api/docs/models>).
