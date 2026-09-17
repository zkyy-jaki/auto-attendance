#  Moodle Auto-Attendance V2

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![GitHub Actions](https://img.shields.io/badge/Automated-GitHub%20Actions-2088FF.svg)
![Telegram](https://img.shields.io/badge/Notification-Telegram-2CA5E0.svg)

Bot otomatisasi berbasis Python untuk melakukan presensi secara otomatis pada platform Moodle (SPADA). Proyek ini dirancang dengan arsitektur modular dan diintegrasikan dengan **GitHub Actions** untuk eksekusi terjadwal secara gratis (24/7) tanpa memerlukan server lokal atau VPS.

---

## Fitur Utama
* **Otomatisasi Penuh (Zero-Cost):** Berjalan otomatis menggunakan *cron job* GitHub Actions yang telah dioptimalkan agar tidak menguras limit kuota gratis.
* **Sistem Multi-User:** Mendukung pembacaan data jadwal dan banyak pengguna melalui konfigurasi `users.json`.
* **Notifikasi Real-time:** Terintegrasi dengan Telegram Bot API untuk mengirimkan log keberhasilan atau kegagalan absensi langsung ke grup chat.
* **Smart Exception Handling:** Dilengkapi penanganan *error* untuk *bypass* verifikasi SSL kampus (SSLError) dan komparasi zona waktu (*offset-aware datetime*).

---
