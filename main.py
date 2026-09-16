import json
import logging
import os
import time
from datetime import datetime

from moodle_client import MoodleClient, _msg_startup
from notifier import TelegramNotifier

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt= "%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLL_INTERVAL_SECONDS = 300   # 5 menit
USERS_CONFIG_FILE     = "users.json"


# ---------------------------------------------------------------------------
# User loading
# ---------------------------------------------------------------------------

def load_users(filepath: str) -> list[dict]:
    """
    Memuat daftar konfigurasi user dari users.json.

    Jika program berjalan di GitHub Actions, nilai env_* keys akan digunakan
    untuk mengambil nilai dari GitHub Secrets.  Jika dijalankan secara lokal,
    nilai langsung diambil dari field di JSON.
    """
    if not os.path.exists(filepath):
        logger.error(f"File konfigurasi '{filepath}' tidak ditemukan.")
        return []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("users", [])
    except json.JSONDecodeError as e:
        logger.error(f"Gagal mem-parsing '{filepath}': {e}")
        return []


# ---------------------------------------------------------------------------
# Single-cycle runner (dipanggil oleh loop utama atau GitHub Actions)
# ---------------------------------------------------------------------------

def run_cycle(users: list[dict]) -> None:
    """
    Satu siklus pengecekan dan absensi untuk semua user.
    Dipanggil setiap POLL_INTERVAL_SECONDS dari loop utama,
    atau sekali ketika dijalankan dari GitHub Actions.
    """
    for user_conf in users:
        client = MoodleClient(user_conf)
        # Lewati user yang ditandai tidak aktif (active: false di users.json)
        if not user_conf.get("active", True):
            logger.info(f"[{client.name}] Dilewati (active: false).")
            continue
        try:
            lecture = client.get_current_lecture()

            if not lecture:
                logger.info(f"[{client.name}] Tidak ada jadwal aktif saat ini.")
                continue

            logger.info(f"[{client.name}] Jadwal aktif ditemukan: {lecture}")

            if client.login():
                client.mark_attendance(lecture)
            else:
                logger.warning(f"[{client.name}] Login gagal, absensi dilewati.")

        except Exception as e:
            # Safety net: pastikan error satu user tidak mematikan keseluruhan loop
            logger.exception(f"[{client.name}] Terjadi kesalahan yang tidak terduga: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    logger.info("=" * 60)
    logger.info("  Moodle Auto-Attendance V2 — Multi-User")
    logger.info("=" * 60)

    users = load_users(USERS_CONFIG_FILE)
    if not users:
        logger.error("Tidak ada user yang ditemukan. Program dihentikan.")
        return

    logger.info(f"Memuat {len(users)} user dari '{USERS_CONFIG_FILE}'.")

    # Kirim notifikasi startup ke setiap user aktif
    active_users = [u for u in users if u.get("active", True)]
    active_names = [u.get("name", "?") for u in active_users]
    startup_msg  = _msg_startup(active_names)
    for u_conf in active_users:
        bot_token = os.environ.get(u_conf.get("env_telegram_token", ""), "") \
                    or u_conf.get("telegram_bot_token", "")
        chat_id   = os.environ.get(u_conf.get("env_telegram_chat_id", ""), "") \
                    or u_conf.get("telegram_chat_id", "")
        TelegramNotifier(bot_token, chat_id).send_message(startup_msg)

    # Deteksi apakah dijalankan di GitHub Actions (CI=true di-set otomatis oleh GH Actions)
    is_ci = os.environ.get("CI", "false").lower() == "true"

    if is_ci:
        # Di GitHub Actions: jalankan hanya sekali, lalu exit
        logger.info("Mode GitHub Actions terdeteksi — menjalankan satu siklus saja.")
        run_cycle(users)
        logger.info("Siklus selesai. Program keluar.")
    else:
        # Lokal / server: loop terus setiap 5 menit
        logger.info(f"Mode lokal — polling setiap {POLL_INTERVAL_SECONDS // 60} menit.")
        try:
            while True:
                try:
                    run_cycle(users)
                except Exception as e:
                    logger.exception(f"Error pada loop utama: {e}")

                logger.info(
                    f"Menunggu {POLL_INTERVAL_SECONDS // 60} menit sebelum pengecekan berikutnya..."
                )
                time.sleep(POLL_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            # Tangkap Ctrl+C di sini agar tidak muncul traceback yang menakutkan
            logger.info("Program dihentikan oleh pengguna (Ctrl+C). Sampai jumpa!")


if __name__ == "__main__":
    main()
