"""
esp32_sim.py — Giả lập ESP32 gửi dữ liệu cảm biến, không cần mạch.
Chạy:  python esp32_sim.py                    # đẩy vào localhost:8006
       python esp32_sim.py --url https://smartroomiot.handq2212.site
       python esp32_sim.py --people           # kèm giả lập YOLO đẩy số người
       python esp32_sim.py --interval 5       # chu kỳ gửi (giây), mặc định 5

Nhấn Ctrl+C để dừng.
"""
import argparse
import random
import time

import requests

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://localhost:8006",
                    help="URL backend (mặc định localhost:8006)")
parser.add_argument("--interval", type=int, default=5, help="Chu kỳ gửi (giây)")
parser.add_argument("--people", action="store_true",
                    help="Cùng giả lập YOLO đẩy số người qua /api/people/count")
parser.add_argument("--people-key", default="aiot-yolo-n16",
                    help="X-API-Key cho /api/people/count")
args = parser.parse_args()

base = args.url.rstrip("/")

# Nhiệt độ phòng HN/SG: bắt đầu 29°C, dao động nhẹ mỗi lần gửi
temp = 29.0
hum = 68.0
people = 2

print(f"Giả lập ESP32 → {base} mỗi {args.interval}s. Ctrl+C để dừng.")
n = 0
while True:
    try:
        # Mô phỏng hơi thật: nhiệt độ trôi ±0.4°C, từ 26 đến 34
        temp = min(34.0, max(26.0, temp + random.uniform(-0.4, 0.4)))
        hum = min(90.0, max(50.0, hum + random.uniform(-1.0, 1.0)))

        r = requests.post(f"{base}/api/sensor",
                          json={"temperature": round(temp, 1),
                                "humidity": round(hum, 1)},
                          timeout=10)
        n += 1
        print(f"[{n:04d}] POST /api/sensor  T={temp:.1f}°C H={hum:.1f}%  → {r.status_code}")

        if args.people:
            # Số người thay đổi ngẫu nhiên 0-5, thỉnh thoảng có người ra/vào
            if random.random() < 0.3:
                people = max(0, min(5, people + random.choice([-1, 1])))
            rp = requests.post(f"{base}/api/people/count",
                               json={"count": people},
                               headers={"X-API-Key": args.people_key},
                               timeout=10)
            print(f"       POST /api/people/count  count={people} → {rp.status_code}")
    except requests.RequestException as e:
        print(f"Lỗi kết nối: {e} — thử lại sau {args.interval}s")

    time.sleep(args.interval)
