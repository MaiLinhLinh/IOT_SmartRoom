"""
main.py — AI service cho AIoT Smart Room.

Chạy cả 2 chức năng song song (2 luồng):
    python main.py

Tuỳ chọn:
    python main.py --only counter            # chỉ YOLO đếm người
    python main.py --only llm                # chỉ Gemini ra quyết định
    python main.py --only counter --show     # mở cửa sổ xem khung hình + bounding box
    python main.py --source webcam:0         # test bằng webcam laptop
    python main.py --source anh_test.jpg --only counter --once --dry-run
    python main.py --only llm --once --dry-run   # gọi Gemini 1 lần, không gửi lệnh

Backend cần đặt AI_ENGINE_ENABLED=false để AI nội bộ không ghi đè quyết định.
"""
import argparse
import logging
import signal
import sys
import threading

from backend_client import BackendClient
from config import settings
from decision_engine import DecisionEngine
from llm_client import GeminiClient


def setup_logging(verbose: bool) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    for noisy in ("urllib3", "ultralytics", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> int:
    parser = argparse.ArgumentParser(description="AIoT Smart Room — YOLO đếm người + Gemini ra quyết định")
    parser.add_argument("--only", choices=["counter", "llm"], help="Chỉ chạy 1 chức năng")
    parser.add_argument("--source", help="Ghi đè CAMERA_SOURCE (esp32 | backend | webcam:0 | file | URL)")
    parser.add_argument("--show", action="store_true", help="Hiện cửa sổ khung hình YOLO (nhấn q để thoát)")
    parser.add_argument("--once", action="store_true", help="Chạy 1 vòng rồi thoát (để test)")
    parser.add_argument("--dry-run", action="store_true", help="Không gửi gì lên backend")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    log = logging.getLogger("main")
    if args.dry_run:
        settings.dry_run = True

    backend = BackendClient(settings.backend_url, settings.people_api_key, settings.ai_api_key,
                            settings.request_timeout)
    log.info("Backend: %s%s", settings.backend_url, " (DRY-RUN)" if settings.dry_run else "")

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())

    threads: list[threading.Thread] = []

    if args.only in (None, "counter"):
        from people_counter import run_counter_loop  # import trễ: chế độ --only llm không cần torch

        def counter_worker():
            try:
                run_counter_loop(settings, backend, stop, source_spec=args.source, show=args.show,
                                 once=args.once, dry_run=settings.dry_run)
            except Exception:
                logging.getLogger("counter").exception("Luồng đếm người dừng do lỗi")

        if args.only == "counter":
            counter_worker()  # chạy ở luồng chính để cửa sổ OpenCV (--show) hoạt động ổn định
            log.info("Đã dừng AI service")
            return 0
        if args.show:
            log.warning("--show nên dùng với --only counter (OpenCV hiển thị tốt nhất ở luồng chính)")
        threads.append(threading.Thread(target=counter_worker, name="counter", daemon=True))

    if args.only in (None, "llm"):
        llm = None
        if settings.gemini_api_key:
            llm = GeminiClient(settings.gemini_api_key, settings.gemini_models,
                               settings.gemini_timeout, settings.gemini_thinking_level)
        else:
            log.warning("Chưa có GEMINI_API_KEY → chỉ dùng luật ngưỡng dự phòng")
        engine = DecisionEngine(settings, backend, llm)
        threads.append(threading.Thread(target=engine.run_loop, args=(stop, args.once), name="decision",
                                        daemon=True))

    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(timeout=0.5)
    except KeyboardInterrupt:
        stop.set()
    log.info("Đã dừng AI service")
    return 0


if __name__ == "__main__":
    sys.exit(main())
