import csv
import logging
import os
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup
from lxml import html

from notifier import TelegramNotifier

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Notification message builders
# ---------------------------------------------------------------------------

def get_now_wib() -> datetime:
    """Mengembalikan waktu saat ini dalam zona waktu WIB (UTC+7)."""
    return datetime.now(timezone(timedelta(hours=7)))


def _ts() -> str:
    """Mengembalikan timestamp saat ini dalam format WIB yang mudah dibaca."""
    return get_now_wib().strftime("%d %b %Y, %H:%M:%S WIB")


def _msg_success(name: str, lecture_id: str, lecture_url: str, att_type: str) -> str:
    type_label = {"1": "Direct Link", "2": "Tombol Hadir", "3": "Password"}.get(att_type, "?")
    return (
        f"✅ <b>Absensi Berhasil!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Mahasiswa :</b> {name}\n"
        f"📚 <b>Mata Kuliah:</b> {lecture_id}\n"
        f"🔧 <b>Metode     :</b> {type_label}\n"
        f"🕐 <b>Waktu      :</b> {_ts()}\n"
        f"🔗 <a href=\'{lecture_url}\'>Buka Halaman Absensi</a>"
    )


def _msg_no_link(name: str, lecture_id: str) -> str:
    return (
        f"⏳ <b>Absensi Belum Tersedia</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Mahasiswa :</b> {name}\n"
        f"📚 <b>Mata Kuliah:</b> {lecture_id}\n"
        f"ℹ️ Link absensi belum dibuka oleh dosen.\n"
        f"🕐 <b>Dicek pada :</b> {_ts()}"
    )


def _msg_fail(name: str, lecture_id: str, reason: str) -> str:
    return (
        f"❌ <b>Absensi Gagal!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Mahasiswa :</b> {name}\n"
        f"📚 <b>Mata Kuliah:</b> {lecture_id}\n"
        f"⚠️ <b>Alasan    :</b> {reason}\n"
        f"🕐 <b>Waktu     :</b> {_ts()}\n"
        f"🔄 Bot akan mencoba lagi di siklus berikutnya."
    )


def _msg_login_fail(name: str) -> str:
    return (
        f"🔐 <b>Login Moodle Gagal!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Mahasiswa :</b> {name}\n"
        f"⚠️ Username atau password salah, atau server Moodle sedang down.\n"
        f"🕐 <b>Waktu     :</b> {_ts()}\n"
        f"🔧 Periksa konfigurasi <code>users.json</code>."
    )


def _msg_startup(names: list[str]) -> str:
    user_list = "\n".join(f"  • {n}" for n in names)
    return (
        f"🚀 <b>Auto-Attendance Bot Aktif</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>User terdaftar:</b>\n{user_list}\n"
        f"⏱️ Polling setiap <b>5 menit</b>\n"
        f"🕐 <b>Mulai pada:</b> {_ts()}"
    )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LectureMeta:
    """Representasi satu baris dari file MetaData.csv."""
    lecture_id: str
    link: str
    attendance_type: str        # "1" = direct link, "2" = present button, "3" = password
    password: str = ""


@dataclass
class ScheduleEntry:
    """Representasi satu baris dari file Schedule.csv."""
    day: str        # 3-char abbreviation: Mon, Tue, Wed, Thu, Fri, Sat, Sun
    time: str       # HH:MM
    lecture_id: str


# ---------------------------------------------------------------------------
# MoodleClient
# ---------------------------------------------------------------------------

class MoodleClient:
    """
    Abstraksi semua interaksi HTTP dengan Moodle untuk satu user.

    Semua kredensial dan konfigurasi diambil dari environment variables
    sehingga aman digunakan di GitHub Actions Secrets.

    Expected env vars (per user, prefix disesuaikan):
        MOODLE_USERNAME, MOODLE_PASSWORD, MOODLE_LOGIN_URL,
        TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
        SCHEDULE_CSV, METADATA_CSV
    """

    def __init__(self, user_config: dict):
        self.name          = user_config.get("name", "Unknown")
        self.username      = os.environ.get(user_config.get("env_username", ""), "") \
                             or user_config.get("username", "")
        self.password      = os.environ.get(user_config.get("env_password", ""), "") \
                             or user_config.get("password", "")
        self.login_url     = user_config.get("login_url", "")
        self.schedule_csv  = user_config.get("schedule_csv", "")
        self.metadata_csv  = user_config.get("metadata_csv", "")

        bot_token  = os.environ.get(user_config.get("env_telegram_token", ""), "") \
                     or user_config.get("telegram_bot_token", "")
        chat_id    = os.environ.get(user_config.get("env_telegram_chat_id", ""), "") \
                     or user_config.get("telegram_chat_id", "")

        self.notifier = TelegramNotifier(bot_token, chat_id)
        self.session  = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        })
        self._logged_in = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log(self, level: str, message: str, notify: bool = False):
        """Log ke console dan opsional kirim ke Telegram."""
        full_msg = f"[{self.name}] {message}"
        getattr(logger, level)(full_msg)
        if notify:
            self.notifier.send_message(f"<b>[{self.name}]</b>\n{message}")

    # ------------------------------------------------------------------
    # Schedule & Metadata parsing
    # ------------------------------------------------------------------

    def get_current_lecture(self) -> Optional[str]:
        """
        Membaca Schedule.csv dan mengembalikan Lecture_ID yang aktif saat ini
        (dalam window ±durasi 1 jam sejak jam mulai), atau None jika tidak ada.
        """
        if not os.path.exists(self.schedule_csv):
            self._log("error", f"File jadwal tidak ditemukan: {self.schedule_csv}", notify=True)
            return None

        now         = get_now_wib()
        current_day = now.strftime("%A")[:3]  # "Mon", "Tue", ...

        try:
            with open(self.schedule_csv, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("Day", "").strip() != current_day:
                        continue
                    try:
                        start = datetime.strptime(row["Time"].strip(), "%H:%M").replace(
                            year=now.year, month=now.month, day=now.day
                        )
                        end = start + timedelta(hours=1)
                        if start <= now < end:
                            return row["Lecture_ID"].strip()
                    except ValueError:
                        continue  # baris tidak valid, lewati
        except Exception as e:
            self._log("error", f"Gagal membaca jadwal: {e}")

        return None

    def get_lecture_meta(self, lecture_id: str) -> Optional[LectureMeta]:
        """Membaca MetaData.csv dan mengembalikan LectureMeta untuk lecture_id tertentu."""
        if not os.path.exists(self.metadata_csv):
            self._log("error", f"File metadata tidak ditemukan: {self.metadata_csv}", notify=True)
            return None

        try:
            with open(self.metadata_csv, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("Lecture_ID", "").strip() == lecture_id:
                        return LectureMeta(
                            lecture_id      = lecture_id,
                            link            = row["Lecture_Link"].strip(),
                            attendance_type = row.get("Attendance_Type", "2").strip(),
                            password        = row.get("Password", "").strip(),
                        )
        except Exception as e:
            self._log("error", f"Gagal membaca metadata: {e}")

        return None

    # ------------------------------------------------------------------
    # Moodle login
    # ------------------------------------------------------------------

    def login(self) -> bool:
        """
        Login ke Moodle. Mengekstrak logintoken dari halaman login terlebih dahulu.
        Mengembalikan True jika sukses.
        """
        if self._logged_in:
            return True

        self._log("info", "Mencoba login ke Moodle...")
        try:
            # 1. Ambil halaman login untuk mendapatkan CSRF token
            resp = self.session.get(self.login_url, timeout=20)
            resp.raise_for_status()

            tree  = html.fromstring(resp.text)
            tokens = tree.xpath("//input[@name='logintoken']/@value")
            logintoken = tokens[0] if tokens else ""
            if not logintoken:
                self._log("warning", "logintoken tidak ditemukan. Login mungkin gagal.")

            # 2. POST form login
            payload = {
                "username":   self.username,
                "password":   self.password,
                "logintoken": logintoken,
                "anchor":     "",
            }
            result = self.session.post(
                self.login_url,
                data    = payload,
                headers = {"Referer": self.login_url},
                timeout = 20,
            )
            result.raise_for_status()

            # 3. Validasi — jika masih di halaman login, berarti gagal
            if "login" in result.url or result.url.rstrip("/") == self.login_url.rstrip("/"):
                self._log("error", "Login gagal.")
                self.notifier.send_message(_msg_login_fail(self.name))
                return False

            self._log("info", "Login berhasil.")
            self._logged_in = True
            return True

        except requests.exceptions.Timeout:
            self._log("error", "Timeout saat mencoba login ke Moodle.")
            self.notifier.send_message(_msg_login_fail(self.name))
        except requests.exceptions.RequestException as e:
            self._log("error", f"Koneksi error saat login: {e}")
            self.notifier.send_message(_msg_login_fail(self.name))

        return False

    # ------------------------------------------------------------------
    # Attendance flow
    # ------------------------------------------------------------------

    def _find_open_attendance_url(self, lecture_url: str) -> Optional[str]:
        """
        Memindai halaman attendance Moodle dan mencari link sesi yang masih bisa diisi
        (biasanya berupa link 'Submit attendance').
        """
        try:
            resp = self.session.get(lecture_url, headers={"Referer": lecture_url}, timeout=20)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            self._log("error", f"Gagal mengakses halaman attendance: {e}")
            return None

        soup = BeautifulSoup(resp.content, "lxml")

        # Moodle menampilkan tabel rekap sesi, link aktif ada pada baris paling atas
        # dengan teks "Submit attendance" atau sejenisnya
        table = soup.find("table", class_=lambda c: c and "attendance" in c.lower()) \
                or soup.find("table")

        if not table:
            return None

        # Cari semua <a> yang mengandung 'sessid' atau 'action=add' di href-nya
        for a_tag in table.find_all("a", href=True):
            href = a_tag["href"]
            if ("sessid" in href or "action=add" in href) and "attendance" in href:
                return href

        return None

    def _extract_sesskey(self, soup: BeautifulSoup, url: str) -> str:
        """Mengekstrak sesskey dari halaman Moodle (dari logout link atau hidden input)."""
        # Cari di URL parameter
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if "sesskey" in params:
            return params["sesskey"][0]

        # Cari di hidden input
        input_tag = soup.find("input", attrs={"name": "sesskey"})
        if input_tag and input_tag.get("value"):
            return input_tag["value"]

        # Cari di logout link
        logout = soup.find("a", href=lambda h: h and "logout" in h and "sesskey=" in h)
        if logout:
            logout_params = urllib.parse.parse_qs(urllib.parse.urlparse(logout["href"]).query)
            if "sesskey" in logout_params:
                return logout_params["sesskey"][0]

        return ""

    # Kata kunci yang diterima sebagai opsi "hadir"
    _PRESENT_KEYWORDS  = frozenset({"present", "hadir", "attend", "p","Hadir"})
    # Kata kunci yang DIBLOKIR — jika opsi mengandung ini, TIDAK BOLEH dipilih
    _BLOCKED_KEYWORDS  = frozenset({"late", "absent", "excused", "izin", "sakit", "alfa",
                                    "terlambat", "tidak hadir", "dispensasi"})

    def _extract_present_status_value(self, soup: BeautifulSoup) -> Optional[str]:
        """
        Mencari value radio button 'Hadir/Present' secara ketat:
          - HARUS mengandung kata kunci positif (PRESENT_KEYWORDS)
          - TIDAK BOLEH mengandung kata kunci negatif (BLOCKED_KEYWORDS)

        Mengembalikan value string jika ditemukan, None jika tidak ada opsi aman.
        """
        # Kumpulkan semua pasangan (radio_input, label_text) di halaman
        candidates: list[tuple] = []

        # Strategi 1: cari via tag <label for="..."> yang menunjuk ke <input type="radio">
        for label in soup.find_all("label"):
            for_id = label.get("for")
            if not for_id:
                continue
            radio = soup.find("input", {"id": for_id, "type": "radio"})
            if radio and radio.get("value"):
                label_text = label.get_text(separator=" ", strip=True).lower()
                candidates.append((radio["value"], label_text))

        # Strategi 2: fallback — cari radio dengan name='status' dan ambil teks terdekatnya
        if not candidates:
            for radio in soup.find_all("input", {"type": "radio", "name": "status"}):
                if not radio.get("value"):
                    continue
                # Ambil teks dari parent atau sibling terdekat sebagai label
                parent_text = ""
                parent = radio.find_parent(["td", "div", "li", "span"])
                if parent:
                    parent_text = parent.get_text(separator=" ", strip=True).lower()
                candidates.append((radio["value"], parent_text))

        # Evaluasi setiap kandidat
        for value, label_text in candidates:
            # Cek apakah label mengandung kata yang DIBLOKIR
            is_blocked = any(kw in label_text for kw in self._BLOCKED_KEYWORDS)
            if is_blocked:
                self._log("debug", f"Radio diblokir (kata negatif ditemukan): '{label_text}'")
                continue

            # Cek apakah label mengandung kata POSITIF
            is_present = any(kw in label_text for kw in self._PRESENT_KEYWORDS)
            if is_present:
                self._log("info", f"Opsi 'Hadir' ditemukan: label='{label_text}', value='{value}'")
                return value

        # Tidak ada opsi yang lulus validasi
        self._log("warning", "Tidak ada radio button 'Present/Hadir' yang valid ditemukan.")
        return None

    def mark_attendance(self, lecture_id: str):
        """
        Orkestrasi utama proses absensi untuk satu mata kuliah.
        """
        meta = self.get_lecture_meta(lecture_id)
        if not meta:
            self._log("warning", f"Metadata untuk '{lecture_id}' tidak ditemukan.")
            self.notifier.send_message(
                _msg_fail(self.name, lecture_id, "Metadata tidak ditemukan di MetaData.csv")
            )
            return

        self._log("info", f"Memproses absensi untuk: {lecture_id} (Tipe {meta.attendance_type})")

        # --- Tipe 1: Direct Link ---
        if meta.attendance_type == "1":
            try:
                self.session.get(meta.link, headers={"Referer": meta.link},
                                 allow_redirects=True, timeout=20)
                self._log("info", f"Absen (direct link) berhasil: {lecture_id}")
                self.notifier.send_message(
                    _msg_success(self.name, lecture_id, meta.link, meta.attendance_type)
                )
            except requests.exceptions.RequestException as e:
                self._log("error", f"Gagal direct link absen [{lecture_id}]: {e}")
                self.notifier.send_message(
                    _msg_fail(self.name, lecture_id, f"Koneksi error: {e}")
                )
            return

        # --- Tipe 2 & 3: Form (Present Button / Password) ---
        open_url = self._find_open_attendance_url(meta.link)
        if not open_url:
            self._log("info", f"Belum ada sesi absensi aktif untuk '{lecture_id}'.")
            # Tidak perlu notif — ini kondisi normal saat dosen belum buka absen
            return

        try:
            att_page = self.session.get(open_url, headers={"Referer": meta.link},
                                        allow_redirects=True, timeout=20)
            att_page.raise_for_status()
        except requests.exceptions.RequestException as e:
            self._log("error", f"Gagal membuka halaman form absensi: {e}")
            self.notifier.send_message(
                _msg_fail(self.name, lecture_id, f"Gagal membuka halaman form: {e}")
            )
            return

        soup = BeautifulSoup(att_page.content, "lxml")

        # ── 1. Cari opsi 'Present' dengan validasi ketat (whitelist + blacklist) ──
        status = self._extract_present_status_value(soup)
        if not status:
            self._log("error", f"Opsi 'Hadir/Present' tidak ditemukan atau semua opsi terblokir untuk '{lecture_id}'.")
            self.notifier.send_message(
                _msg_fail(
                    self.name, lecture_id,
                    "Opsi 'Hadir/Present' tidak tersedia di form. "
                    "Semua opsi mengandung kata negatif (Absent/Late/Izin) atau form kosong."
                )
            )
            return

        # ── 2. Kumpulkan SEMUA hidden input dari form secara dinamis ──
        form = soup.find("form", id="attendancestudentform") \
               or soup.find("form", action=lambda a: a and "attendance" in a) \
               or soup.find("form")

        if not form:
            self._log("error", "Elemen <form> absensi tidak ditemukan di halaman.")
            self.notifier.send_message(
                _msg_fail(self.name, lecture_id, "Elemen form absensi tidak ditemukan di HTML halaman.")
            )
            return

        # Ambil action URL form (gunakan sebagai POST target jika tersedia)
        form_action = form.get("action", att_page.url)
        if form_action and not form_action.startswith("http"):
            from urllib.parse import urljoin
            form_action = urljoin(att_page.url, form_action)

        # Panen semua hidden input ke dalam payload dasar
        payload: dict = {}
        for hidden in form.find_all("input", {"type": "hidden"}):
            name = hidden.get("name")
            val  = hidden.get("value", "")
            if name:
                payload[name] = val

        # Tambahkan radio button 'status' yang sudah divalidasi
        payload["status"] = status

        # Tambahkan password absensi jika tipe 3
        if meta.attendance_type == "3" and meta.password:
            payload["studentpassword"] = meta.password

        # Cari nama dan value tombol submit di dalam form
        submit_btn = form.find("input", {"type": "submit"}) \
                     or form.find("button", {"type": "submit"})
        if submit_btn:
            btn_name  = submit_btn.get("name")
            btn_value = submit_btn.get("value", "Save changes")
            if btn_name:
                payload[btn_name] = btn_value
        else:
            # Fallback ke nama default Moodle
            payload["submitbutton"] = "Save changes"

        self._log("debug", f"Payload yang akan dikirim: {list(payload.keys())}")

        # ── 3. POST form ──
        try:
            result = self.session.post(
                form_action,
                data            = payload,
                headers         = {"Referer": att_page.url},
                allow_redirects = True,
                timeout         = 20,
            )
            result.raise_for_status()
        except requests.exceptions.RequestException as e:
            self._log("error", f"Gagal melakukan POST absensi: {e}")
            self.notifier.send_message(
                _msg_fail(self.name, lecture_id, f"Koneksi error saat submit form: {e}")
            )
            return

        # ── 4. Validasi respons POST ──
        result_soup = BeautifulSoup(result.content, "lxml")
        page_text   = result_soup.get_text(separator=" ", strip=True).lower()

        # Indikator SUKSES: Moodle biasanya menampilkan salah satu kata ini
        SUCCESS_SIGNALS = (
            "self-recorded", "self recorded",
            "your attendance", "attendance saved",
            "successfully", "berhasil",
            "hadir",  # muncul di halaman konfirmasi Moodle Indonesia
        )
        # Indikator GAGAL tambahan: form masih bisa diisi
        form_still_active = bool(result_soup.find("input", {"type": "radio", "name": "status"}))
        has_success_text  = any(sig in page_text for sig in SUCCESS_SIGNALS)

        if has_success_text or not form_still_active:
            self._log("info", f"Absen berhasil untuk: {lecture_id}")
            self.notifier.send_message(
                _msg_success(self.name, lecture_id, meta.link, meta.attendance_type)
            )
        else:
            self._log("warning", f"Absen tidak terkonfirmasi untuk: {lecture_id}")
            self.notifier.send_message(
                _msg_fail(
                    self.name, lecture_id,
                    "Form mungkin sudah tersubmit tetapi halaman respons tidak mengandung "
                    "konfirmasi kehadiran. Mohon cek manual di SPADA."
                )
            )
