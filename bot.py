
import requests
import time
import threading
from flask import Flask
from playwright.sync_api import sync_playwright

BOT_TOKEN = "8756937178:AAHrVGNMhetcZuqPsv3QstlsTr7qPoeslyA"
BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

users = {}

# ---------------- FLASK ----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

def run_web():
    app.run(host="0.0.0.0", port=10000)

# ---------------- TELEGRAM ----------------
def send_msg(chat_id, text):
    requests.post(f"{BASE}/sendMessage", json={
        "chat_id": chat_id,
        "text": text
    })

def send_keyboard(chat_id, text, buttons):
    requests.post(f"{BASE}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {
            "keyboard": buttons,
            "resize_keyboard": True,
            "one_time_keyboard": True
        }
    })

def send_photo(chat_id, path):
    requests.post(f"{BASE}/sendPhoto",
                  data={"chat_id": chat_id},
                  files={"photo": open(path, "rb")})

def send_doc(chat_id, path):
    requests.post(f"{BASE}/sendDocument",
                  data={"chat_id": chat_id},
                  files={"document": open(path, "rb")})

# ---------------- FETCH DISTRICTS ----------------
def get_districts():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://mpeuparjan.mp.gov.in/euparjanmp/WPMS2026/frm_Rabi_FarmerDetails.aspx")

        options = page.locator("#ContentPlaceHolder1_ddlDistrict option").all_text_contents()

        browser.close()

    return [o.strip() for o in options if o.strip() and "चुनें" not in o]

# ---------------- CAPTCHA ----------------
def get_captcha(user):
    file = f"{user['chat_id']}_captcha.png"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://mpeuparjan.mp.gov.in/euparjanmp/WPMS2026/frm_Rabi_FarmerDetails.aspx")
        page.select_option("#ContentPlaceHolder1_ddlDistrict", label=user["district"])

        page.locator("#ContentPlaceHolder1_imgCaptcha").screenshot(path=file)

        browser.close()

    return file

# ---------------- PDF ----------------
def generate_pdf(user):
    file = f"{user['chat_id']}.pdf"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://mpeuparjan.mp.gov.in/euparjanmp/WPMS2026/frm_Rabi_FarmerDetails.aspx")

        page.select_option("#ContentPlaceHolder1_ddlDistrict", label=user["district"])

        inputs = page.locator("input[type='text']")
        inputs.nth(0).fill(user["code"])
        inputs.nth(1).fill(user["captcha"])

        page.click("text=किसान सर्च करे")
        page.wait_for_timeout(4000)

        page.click("text=प्रिंट करे")
        page.wait_for_timeout(3000)

        page.add_style_tag(content="body { zoom:0.6 }")

        page.pdf(path=file, format="A4", print_background=True)

        browser.close()

    return file

# ---------------- BOT LOGIC ----------------
def handle(msg):
    chat = msg["chat"]["id"]
    text = msg.get("text", "").strip()

    if chat not in users:
        users[chat] = {"chat_id": chat, "step": "start"}

    user = users[chat]

    # START
    if text == "/start":
        send_keyboard(chat, "Select option:", [
            ["📄 Get Receipt"]
        ])
        user["step"] = "menu"

    # MENU BUTTON
    elif text == "📄 Get Receipt":
        districts = get_districts()

        keyboard = []
        row = []

        for d in districts:
            row.append(d)
            if len(row) == 2:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)

        send_keyboard(chat, "जिला चुनें:", keyboard)
        user["step"] = "district"

    # DISTRICT SELECT
    elif user["step"] == "district":
        user["district"] = text
        send_msg(chat, "Enter किसान कोड / मोबाइल / समग्र:")
        user["step"] = "code"

    # CODE INPUT
    elif user["step"] == "code":
        user["code"] = text

        captcha = get_captcha(user)
        send_photo(chat, captcha)

        send_msg(chat, "Enter CAPTCHA:")
        user["step"] = "captcha"

    # CAPTCHA INPUT
    elif user["step"] == "captcha":
        user["captcha"] = text

        send_msg(chat, "⏳ Processing...")

        try:
            pdf = generate_pdf(user)
            send_doc(chat, pdf)
        except Exception as e:
            send_msg(chat, f"❌ Error: {str(e)}")

        user["step"] = "start"

# ---------------- RUN BOT ----------------
def run_bot():
    offset = 0
    while True:
        try:
            res = requests.get(f"{BASE}/getUpdates",
                               params={"offset": offset, "timeout": 30}).json()

            for upd in res.get("result", []):
                offset = upd["update_id"] + 1

                if "message" in upd:
                    handle(upd["message"])

        except Exception as e:
            print("Error:", e)

        time.sleep(1)

# ---------------- MAIN ----------------
if __name__ == "__main__":
    threading.Thread(target=run_web).start()
    run_bot()
