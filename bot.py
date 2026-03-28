from playwright.sync_api import sync_playwright
import requests
import time

BOT_TOKEN = "8756937178:AAHrVGNMhetcZuqPsv3QstlsTr7qPoeslyA"

def send_document(chat_id, file_path):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    files = {"document": open(file_path, "rb")}
    requests.post(url, data={"chat_id": chat_id}, files=files)

def generate_pdf(print_url):
    file_path = "receipt.pdf"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(print_url)
        page.wait_for_timeout(3000)

        # 🔥 force 1-page fit
        page.add_style_tag(content="body { zoom: 0.6 }")

        page.pdf(
            path=file_path,
            format="A4",
            print_background=True
        )

        browser.close()

    return file_path
