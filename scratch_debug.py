import os
import urllib3
import logging
import json
from moodle_client import MoodleClient
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)

def main():
    with open("d:/Auto-Attendance-Moodle/users.json", "r") as f:
        users = json.load(f)["users"]
    
    user_conf = users[0]
    os.environ["ZAKY_MOODLE_USERNAME"] = "123240107"
    os.environ["ZAKY_MOODLE_PASSWORD"] = "Zakymubarok123_"
    
    client = MoodleClient(user_conf)
    if client.login():
        # Step 1: Ambil halaman daftar sesi
        url = "https://spada.upnyk.ac.id/mod/attendance/view.php?id=809875"
        resp = client.session.get(url)
        soup = BeautifulSoup(resp.content, "lxml")

        # Step 2: Cari link Submit attendance
        submit_link = None
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if "sessid" in href and "attendance" in href:
                submit_link = href
                print("Submit attendance link:", submit_link)
                break

        if not submit_link:
            print("Tidak ada submit link ditemukan!")
            return

        # Step 3: Buka halaman FORM absensi (yang ada radio buttonnya)
        form_resp = client.session.get(submit_link)
        with open("d:/Auto-Attendance-Moodle/scratch_form.html", "w", encoding="utf-8") as f:
            f.write(form_resp.text)
        print("HTML form halaman disimpan ke scratch_form.html")

        # Step 4: Parse radio buttons
        form_soup = BeautifulSoup(form_resp.content, "lxml")
        radios = form_soup.find_all("input", {"type": "radio"})
        print(f"\nJumlah radio button ditemukan: {len(radios)}")
        for r in radios:
            print(f"  name={r.get('name')}, value={r.get('value')}, id={r.get('id')}")
        
        labels = form_soup.find_all("label")
        print(f"\nJumlah label ditemukan: {len(labels)}")
        for l in labels:
            print(f"  for={l.get('for')}, text={l.get_text(strip=True)}")

if __name__ == "__main__":
    main()
