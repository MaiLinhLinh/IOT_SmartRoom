"""
simulate.py — Chạy kịch bản giả lập để DEMO các chế độ quạt / điều hòa.

Ví dụ:
    python simulate.py --list                               # xem các kịch bản có sẵn
    python simulate.py --mode offline                       # tập dượt, không cần backend/ESP32
    python simulate.py                                      # DEMO: kịch bản "full", mode override, backend thật
    python simulate.py --scenario chi-dieu-hoa --speed 2    # một kịch bản, chạy nhanh gấp 2
    python simulate.py --with-counter --real-people         # nhiệt độ/độ ẩm giả lập + số người THẬT từ camera
    python simulate.py --mode backend                       # đẩy số giả lên backend (Dashboard hiện số giả)
    python simulate.py --scenario my_demo.json              # kịch bản tự viết

Trước khi demo với backend thật: backend đã áp bản vá AI v4, AI_ENGINE_ENABLED=false, thiết bị ở chế độ AUTO.
"""
import argparse
import logging
import sys
import threading

from backend_client import BackendClient
from config import settings
from decision_engine import DecisionEngine
from llm_client import GeminiClient
import events
import simulator

# Thời gian rút gọn cho demo (thực tế: AC bật tối thiểu 10 phút, phòng trống chờ 3 phút...)
DEMO_TIMING = {
    "decision_check_interval": 2,
    "llm_min_interval": 3,
    "llm_max_interval": 60,
    "ac_min_on_seconds": 20,
    "ac_min_off_seconds": 10,
    "empty_ac_grace_seconds": 10,
    "natural_temp_ttl_seconds": 300,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Giả lập kịch bản demo quạt / điều hòa")
    parser.add_argument("--scenario", default="full", help="Tên kịch bản có sẵn hoặc file .json (mặc định: full)")
    parser.add_argument("--list", action="store_true", help="Liệt kê kịch bản có sẵn")
    parser.add_argument("--mode", choices=["offline", "override", "backend"], default="override",
                        help="offline: không cần backend | override (mặc định): thay số AI đọc, gửi lệnh thật | "
                             "backend: đẩy số giả lên /api/sensor")
    parser.add_argument("--speed", type=float, default=1.0, help="Tốc độ chạy (2 = nhanh gấp đôi)")
    parser.add_argument("--real-timing", action="store_true",
                        help="Dùng thời gian thật (AC tối thiểu 10 phút...), không rút gọn cho demo")
    parser.add_argument("--with-counter", action="store_true", help="Chạy thêm YOLO đếm người từ camera thật")
    parser.add_argument("--real-people", action="store_true",
                        help="Bỏ số người trong kịch bản, dùng số người thật (YOLO)")
    parser.add_argument("--dry-run", action="store_true", help="Không gửi lệnh lên backend")
    parser.add_argument("--no-tag", action="store_true", help="Không gắn tiền tố [Mô phỏng] vào lý do")
    parser.add_argument("-v", "--verbose", action="store_true", help="In chi tiết log của AI")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S",
                        stream=sys.stdout)
    for noisy in ("urllib3", "ultralytics"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.list:
        for name, data in simulator.SCENARIOS.items():
            total = sum(s["duration"] for s in data["steps"])
            print(f"{name:<14} {len(data['steps'])} bước, ~{total}s  — {data['title']}")
        return 0

    name, title, steps = simulator.load_scenario(args.scenario)
    if args.real_people:
        for s in steps:
            s.people = None

    # ---- cấu hình ----
    if not args.real_timing:
        # thời gian rút gọn; chạy nhanh (--speed) thì rút gọn tương ứng để các bước vẫn đủ thời gian
        for key, value in DEMO_TIMING.items():
            setattr(settings, key, max(0.5, value / max(args.speed, 1.0)))
    dry_run = settings.dry_run or args.dry_run
    settings.dry_run = False  # dry-run do SimulatedBackend xử lý (giữ trạng thái ảo, không gửi lên backend)
    settings.simulation = name
    if args.mode == "offline":
        settings.decision_log_path = settings.decision_log_path or ""

    # ---- backend ----
    if args.mode == "offline":
        inner = simulator.InMemoryBackend()
        if args.with_counter or args.real_people:
            print("⚠ --with-counter / --real-people không dùng được ở mode offline.")
            events.alert("start", "error", "Tuỳ chọn không hợp lệ", "YOLO / số người thật cần backend.",
                         "Chọn chế độ khác Tập dượt, hoặc bỏ tick YOLO.")
            return 2
    else:
        inner = BackendClient(settings.backend_url, settings.people_api_key, settings.ai_api_key,
                              settings.request_timeout)
        try:
            ctx = inner.get_context()
        except Exception as exc:  # noqa: BLE001
            print(f"✗ Không đọc được {settings.backend_url}/api/ai/context: {exc}")
            msg = str(exc)
            title, hint = "Không kết nối được backend", "Kiểm tra BACKEND_URL và trạng thái backend."
            if "localhost" in settings.backend_url or "127.0.0.1" in settings.backend_url:
                hint = ("BACKEND_URL đang trỏ vào chính máy này nhưng không có backend. Sửa .env: "
                        "BACKEND_URL=http://<IP máy backend>:8006 — hoặc chọn chế độ Tập dượt.")
            elif "lỗi kết nối" in msg:
                hint = "Backend phải chạy với --host 0.0.0.0, đúng IP, mở cổng 8006, cùng mạng."
            elif "401" in msg:
                title, hint = "Sai API key", "PEOPLE_API_KEY / AI_API_KEY trong .env phải giống backend/.env."
            elif "HTTP 500" in msg:
                title, hint = "Backend báo lỗi 500", "Backend chưa cập nhật API AI v4 — pull bản mới của backend."
            short = (f"{settings.backend_url} không phản hồi" if "lỗi kết nối" in msg
                     else f"{settings.backend_url}: {msg[:160]}")
            events.alert("start", "error", title, short, hint)
            if "lỗi kết nối" in msg:
                print(f"  → Không có backend nào trả lời ở {settings.backend_url}.")
                if "localhost" in settings.backend_url or "127.0.0.1" in settings.backend_url:
                    print("  → BACKEND_URL đang trỏ vào CHÍNH MÁY NÀY, nhưng máy này không chạy backend.")
                    print("    Sửa ai_service/.env: BACKEND_URL=http://<IP máy chạy backend>:8006 (hoặc URL tunnel),")
                    print("    hoặc chọn chế độ 'Tập dượt (offline)' nếu chưa có backend.")
                else:
                    print("  → Kiểm tra: backend đang chạy với --host 0.0.0.0, đúng IP, tường lửa mở cổng 8006, cùng mạng.")
            elif "HTTP 500" in msg:
                print("  → Backend chưa áp bản vá AI v4 (ai_service/docs/backend_ai_v4.patch).")
            else:
                print("  Kiểm tra BACKEND_URL và trạng thái backend.")
            return 2
        if ctx.get("mode") != "AUTO":
            print("⚠ Thiết bị đang MANUAL → AI sẽ không điều khiển. Chuyển sang AUTO trên app/Swagger "
                  "(PATCH /api/device/mode).")
    sim = simulator.SimulatedBackend(inner, args.mode, tag_reason=not args.no_tag, dry_run=dry_run)

    llm = None
    if settings.gemini_api_key:
        llm = GeminiClient(settings.gemini_api_key, settings.gemini_models,
                           settings.gemini_timeout, settings.gemini_thinking_level)
    engine = DecisionEngine(settings, sim, llm)

    # ---- YOLO thật (tuỳ chọn) ----
    stop = threading.Event()
    if args.with_counter:
        from people_counter import run_counter_loop
        threading.Thread(target=run_counter_loop, args=(settings, inner, stop),
                         kwargs={"dry_run": dry_run}, daemon=True, name="counter").start()

    events.emit("scenario_start", mode=args.mode, speed=args.speed, dry_run=dry_run,
                llm=(", ".join(llm.models) if llm else None), **simulator.scenario_event(args.scenario, title, steps))
    print(f"KỊCH BẢN: {name} — {title}")
    print(f"Chế độ: {args.mode} | backend: {settings.backend_url if args.mode != 'offline' else 'giả lập'} | "
          f"LLM: {'Gemini ' + ','.join(llm.models) if llm else 'tắt (dùng planner)'} | "
          f"thời gian: {'thật' if args.real_timing else 'rút gọn cho demo'} | tốc độ x{args.speed:g}"
          f"{' | DRY-RUN (không gửi lệnh)' if dry_run else ''}")

    try:
        results = simulator.run_scenario(engine, sim, steps, speed=args.speed,
                                         tick=settings.decision_check_interval)
    except KeyboardInterrupt:
        print("\nĐã dừng.")
        return 1
    finally:
        stop.set()

    failed = simulator.print_summary(results)
    checked = [r for r in results if r.ok is not None]
    events.emit("summary", passed=sum(1 for r in checked if r.ok), checked=len(checked), commands=len(sim.sent))
    print(f"Số lệnh AI đã ra: {len(sim.sent)}" + (" (dry-run, không gửi)" if dry_run else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
