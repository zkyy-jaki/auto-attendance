import requests
import logging

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Modul untuk mengirim notifikasi ke Telegram menggunakan Bot API.
    Jika token atau chat_id tidak dikonfigurasi, pengiriman akan dilewati
    tanpa menyebabkan crash pada program utama.
    """

    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._is_configured = bool(bot_token and chat_id)

    def send_message(self, text: str) -> bool:
        """
        Mengirim pesan teks ke Telegram.

        Args:
            text: Teks pesan yang akan dikirim. Mendukung HTML formatting.

        Returns:
            True jika berhasil, False jika gagal atau tidak dikonfigurasi.
        """
        if not self._is_configured:
            logger.debug("Telegram tidak dikonfigurasi, notifikasi dilewati.")
            return False

        url = self.BASE_URL.format(token=self.bot_token)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        try:
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            return True
        except requests.exceptions.Timeout:
            logger.warning("Timeout saat mengirim notifikasi Telegram.")
        except requests.exceptions.HTTPError as e:
            logger.warning(f"HTTP Error dari Telegram API: {e}")
        except requests.exceptions.RequestException as e:
            logger.warning(f"Gagal mengirim notifikasi Telegram: {e}")

        return False
