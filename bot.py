
import requests
import time
from playwright.sync_api import sync_playwright

BOT_TOKEN = "8756937178:AAHrVGNMhetcZuqPsv3QstlsTr7qPoeslyA"
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

users = {}

# ---------------- TELEGRAM ----------------
def send_message(chat_id, text, keyboard=None):
    data = {"chat_id": chat_id, "text": text}
    if keyboard:
        data["reply_markup"] = {"keyboard": keyboard, "resize_keyboard": True}
    requests.post(f"{BASE_URL}/sendMessage", json=data)

def send_photo(chat_id, path):
    files = {"photo": open(path, "rb")}
    requests.post(f"{BASE_URL}/sendPhoto", data={"chat_id": chat_id}, files=files)

def send_document(chat_id, path):
    files = {"document": open(path, "rb")}
    requests.post(f"{BASE_URL}/sendDocument", data={"chat_id": chat_id}, files=files)

# ---------------- PLAYWRIGHT ----------------
def process_receipt(user):
    file_path = f"{user['chat_id']}.pdf"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://mpeuparjan.mp.gov.in/euparjanmp/WPMS2026/frm_Rabi_FarmerDetails.aspx")

        # Select district
        page.select_option("select", label=user["district"])

        # Enter code
        page.fill("input[type='text']", user["code"])

        # Fill captcha
        page.fill("input[type='text'] >> nth=1", user["captcha"])

        # Submit
        page.click("text=किसान सर्च करे")
        page.wait_for_timeout(3000)

        # Click print
        page.click("text=प्रिंट करे")
        page.wait_for_timeout(3000)

        # Force A4 one page
        page.add_style_tag(content="body { zoom: 0.6 }")

        page.pdf(path=file_path, format="A4", print_background=True)

        browser.close()

    return file_path

# ---------------- BOT LOGIC ----------------
def handle_message(msg):
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")

    if chat_id not in users:
        users[chat_id] = {"step": "start", "chat_id": chat_id}

    user = users[chat_id]

    # START
    if text == "/start":
        send_message(chat_id, "Select option:", [["📄 Get Receipt"]])
        user["step"] = "menu"

    # MENU
    elif user["step"] == "menu" and "Receipt" in text:
        districts = [
            ["धार"], ["इंदौर"], ["उज्जैन"],
            ["देवास"], ["खरगोन"], ["बड़वानी"]
        ]
        send_message(chat_id, "जिला चुनें:", districts)
        user["step"] = "district"

    # DISTRICT
    elif user["step"] == "district":
        user["district"] = text
        send_message(chat_id, "किसान कोड / मोबाइल / समग्र दर्ज करें:")
        user["step"] = "code"

    # CODE
    elif user["step"] == "code":
        user["code"] = text

        # get captcha image
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto("https://mpeuparjan.mp.gov.in/euparjanmp/WPMS2026/frm_Rabi_FarmerDetails.aspx")

            page.select_option("select", label=user["district"])

            page.screenshot(path=f"{chat_id}_captcha.png")
            browser.close()

        send_photo(chat_id, f"{chat_id}_captcha.png")
        send_message(chat_id, "Enter CAPTCHA:")
        user["step"] = "captcha"

    # CAPTCHA
    elif user["step"] == "captcha":
        user["captcha"] = text

        send_message(chat_id, "⏳ Processing...")

        try:
            pdf = process_receipt(user)
            send_document(chat_id, pdf)
        except Exception as e:
            send_message(chat_id, f"❌ Error: {e}")

        user["step"] = "start"

# ---------------- LONG POLLING ----------------
def run_bot():
    offset = 0
    while True:
        try:
            res = requests.get(f"{BASE_URL}/getUpdates", params={"offset": offset, "timeout": 30}).json()

            for update in res.get("result", []):
                offset = update["update_id"] + 1

                if "message" in update:
                    handle_message(update["message"])

        except Exception as e:
            print("Error:", e)

        time.sleep(1)

# ---------------- RUN ----------------
if __name__ == "__main__":
    run_bot()

