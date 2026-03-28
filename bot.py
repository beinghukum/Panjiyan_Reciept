import requests
import time
from playwright.sync_api import sync_playwright

BOT_TOKEN = "8756937178:AAHrVGNMhetcZuqPsv3QstlsTr7qPoeslyA"
BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

users = {}

# -------- TELEGRAM --------
def send_msg(chat_id, text, kb=None):
    data = {"chat_id": chat_id, "text": text}
    if kb:
        data["reply_markup"] = {"keyboard": kb, "resize_keyboard": True}
    requests.post(f"{BASE}/sendMessage", json=data)

def send_photo(chat_id, path):
    requests.post(f"{BASE}/sendPhoto",
                  data={"chat_id": chat_id},
                  files={"photo": open(path, "rb")})

def send_doc(chat_id, path):
    requests.post(f"{BASE}/sendDocument",
                  data={"chat_id": chat_id},
                  files={"document": open(path, "rb")})

# -------- CAPTCHA FETCH --------
def get_captcha(user):
    file = f"{user['chat_id']}_captcha.png"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://mpeuparjan.mp.gov.in/euparjanmp/WPMS2026/frm_Rabi_FarmerDetails.aspx")
        page.select_option("select", label=user["district"])

        # correct captcha selector
        page.locator("img").first.screenshot(path=file)

        browser.close()

    return file

# -------- PDF GENERATION --------
def generate_pdf(user):
    file = f"{user['chat_id']}.pdf"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://mpeuparjan.mp.gov.in/euparjanmp/WPMS2026/frm_Rabi_FarmerDetails.aspx")

        page.select_option("select", label=user["district"])

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

# -------- BOT LOGIC --------
def handle(msg):
    chat = msg["chat"]["id"]
    text = msg.get("text", "")

    if chat not in users:
        users[chat] = {"step": "start", "chat_id": chat}

    u = users[chat]

    if text == "/start":
        send_msg(chat, "Select option:", [["📄 Receipt"]])
        u["step"] = "menu"

    elif u["step"] == "menu":
        send_msg(chat, "जिला चुनें:", [["धार"], ["इंदौर"], ["उज्जैन"]])
        u["step"] = "district"

    elif u["step"] == "district":
        u["district"] = text
        send_msg(chat, "Enter किसान कोड / मोबाइल / समग्र:")
        u["step"] = "code"

    elif u["step"] == "code":
        u["code"] = text

        captcha_path = get_captcha(u)
        send_photo(chat, captcha_path)

        send_msg(chat, "Enter CAPTCHA:")
        u["step"] = "captcha"

    elif u["step"] == "captcha":
        u["captcha"] = text

        send_msg(chat, "⏳ Processing...")

        try:
            pdf = generate_pdf(u)
            send_doc(chat, pdf)
        except Exception as e:
            send_msg(chat, f"❌ Error: {str(e)}")

        u["step"] = "start"

# -------- LONG POLLING --------
def run():
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
            print("ERR:", e)

        time.sleep(1)

if __name__ == "__main__":
    run()
