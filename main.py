import json
import logging
import os
import signal
import time
from datetime import datetime

# Muat .env saat development lokal (diabaikan otomatis jika tidak ada)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv tidak wajib di produksi jika env var sudah di-set langsung

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

POLL_INTERVAL_SECONDS = 900   # 15 menit — sesuai kebutuhan Render Background Worker
USERS_CONFIG_FILE     = "users.json"

# ---------------------------------------------------------------------------
# Graceful Shutdown (SIGTERM handler untuk Render)
# ---------------------------------------------------------------------------

_shutdown_requested = False

def _handle_sigterm(signum, frame):
    """
    Handler untuk sinyal SIGTERM yang dikirim Render saat mematikan worker.
    Menandai flag shutdown agar loop utama bisa keluar dengan bersih
    tanpa memotong siklus absensi yang sedang berjalan.
    """
    global _shutdown_requested
    logger.info("Sinyal SIGTERM diterima — menyelesaikan siklus saat ini lalu berhenti...")
    _shutdown_requested = True

signal.signal(signal.SIGTERM, _handle_sigterm)

# ---------------------------------------------------------------------------
# User loading
# ---------------------------------------------------------------------------

def load_users(filepath: str) -> list[dict]:
    """
    Memuat daftar konfigurasi user dari users.json.

    File users.json AMAN untuk di-commit ke Git karena hanya berisi
    nama environment variable (misal: "ZAKY_MOODLE_USERNAME"), bukan
    nilai aktualnya. Nilai aktual dibaca dari env var di runtime.

    Di Render: set env var via dashboard Render → Environment.
    Di lokal  : buat file .env berdasarkan .env.example.
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
# Single-cycle runner
# ---------------------------------------------------------------------------

def run_cycle(users: list[dict]) -> None:
    """
    Satu siklus pengecekan dan absensi untuk semua user.
    Dipanggil setiap POLL_INTERVAL_SECONDS dari loop utama.
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
    logger.info("  Moodle Auto-Attendance — Render Background Worker")
    logger.info("=" * 60)

    users = load_users(USERS_CONFIG_FILE)
    if not users:
        logger.error("Tidak ada user yang ditemukan. Program dihentikan.")
        return

    logger.info(f"Memuat {len(users)} user dari '{USERS_CONFIG_FILE}'.")

    # Validasi awal: pastikan env var kredensial tersedia
    active_users = [u for u in users if u.get("active", True)]
    missing_vars = []
    for u in active_users:
        for key in ("env_username", "env_password"):
            env_name = u.get(key, "")
            if env_name and not os.environ.get(env_name):
                missing_vars.append(f"  ⚠ '{env_name}' (untuk user '{u.get('name', '?')}')")
    if missing_vars:
        logger.warning(
            "Environment variable berikut belum di-set atau kosong:\n" +
            "\n".join(missing_vars) +
            "\n→ Set env var via Render Dashboard → Environment, atau buat file .env lokal."
        )

    # Kirim notifikasi startup ke setiap user aktif
    active_names = [u.get("name", "?") for u in active_users]
    startup_msg  = _msg_startup(active_names)
    for u_conf in active_users:
        bot_token = os.environ.get(u_conf.get("env_telegram_token", ""), "") \
                    or u_conf.get("telegram_bot_token", "")
        chat_id   = os.environ.get(u_conf.get("env_telegram_chat_id", ""), "") \
                    or u_conf.get("telegram_chat_id", "")
        TelegramNotifier(bot_token, chat_id).send_message(startup_msg)

    # -----------------------------------------------------------------------
    # Infinite loop — mode utama untuk Render Background Worker
    # Loop ini berjalan terus sampai menerima SIGTERM (Render shutdown)
    # atau KeyboardInterrupt (Ctrl+C di lokal).
    # -----------------------------------------------------------------------
    logger.info(
        f"Memulai loop polling — interval setiap {POLL_INTERVAL_SECONDS // 60} menit. "
        f"Kirim SIGTERM atau tekan Ctrl+C untuk menghentikan."
    )

    try:
        while not _shutdown_requested:
            cycle_start = datetime.now()
            logger.info(f"--- Siklus dimulai pada {cycle_start.strftime('%Y-%m-%d %H:%M:%S')} ---")

            try:
                run_cycle(users)
            except Exception as e:
                logger.exception(f"Error pada loop utama: {e}")

            if _shutdown_requested:
                break  # Keluar segera setelah siklus selesai jika SIGTERM diterima

            logger.info(
                f"Siklus selesai. Menunggu {POLL_INTERVAL_SECONDS // 60} menit "
                f"sebelum pengecekan berikutnya..."
            )
            # Sleep dalam potongan kecil agar SIGTERM langsung merespons
            for _ in range(POLL_INTERVAL_SECONDS):
                if _shutdown_requested:
                    break
                time.sleep(1)

    except KeyboardInterrupt:
        # Tangkap Ctrl+C di lokal agar tidak muncul traceback
        logger.info("Program dihentikan oleh pengguna (Ctrl+C). Sampai jumpa!")

    logger.info("Worker berhenti dengan bersih.")


if __name__ == "__main__":
    main()
