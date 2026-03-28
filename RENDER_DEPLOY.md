# 🚀 Render Deployment Guide — Kisan Receipt Bot

## Why Docker is Required
Playwright needs Chromium + ~200MB of system libraries (fonts, audio, graphics).
Render's standard Python runtime does NOT include these.
The Microsoft Playwright Docker image has everything pre-installed.

---

## Step-by-Step Deployment

### 1. Push code to GitHub
Create a GitHub repo and push these files:
```
kisan_receipt_bot/
├── bot.py
├── scraper.py
├── requirements.txt
└── Dockerfile          ← This is the key file
```

### 2. Create a new Web Service on Render
- Go to https://render.com → New → **Web Service**
- Connect your GitHub repo
- Render will auto-detect the Dockerfile

### 3. Configure the service
| Setting | Value |
|---------|-------|
| **Name** | kisan-receipt-bot |
| **Environment** | Docker |
| **Instance Type** | **Starter** ($7/mo) minimum — Free tier will NOT work (not enough RAM for Chromium) |
| **Region** | Singapore (closest to India) |

### 4. Set Environment Variables
In Render dashboard → Environment → Add:

| Key | Value |
|-----|-------|
| `BOT_TOKEN` | Your Telegram bot token from @BotFather |
| `PYTHONUNBUFFERED` | `1` |

> ⚠️ Never put your bot token in the code or GitHub!

### 5. Deploy
Click **Deploy** — first build takes ~5-8 minutes (downloads Playwright image).
Subsequent deploys are faster.

---

## Important: Keep the Bot Alive on Render

Render **free tier sleeps after 15 min of inactivity**. For a bot, this means
it won't respond until it wakes up (30-60 sec delay on first message).

**Solution: Use Starter plan ($7/mo)** — always-on, no sleep.

Or add a keep-alive ping (free workaround):
- Use https://uptimerobot.com (free) to ping your Render URL every 10 min
- Add a health endpoint to bot.py (see below)

### Optional: Add health check endpoint to bot.py
Add this to `bot.py` before `main()`:

```python
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass  # suppress logs

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

# In main(), before app.run_polling():
threading.Thread(target=run_health_server, daemon=True).start()
```

---

## RAM Requirements
| Component | RAM needed |
|-----------|-----------|
| Python bot | ~50 MB |
| Playwright Chromium | ~300-400 MB |
| Per browser session | ~100 MB |
| **Total recommended** | **512 MB+** |

Render Starter = 512 MB RAM ✅
Render Free = 512 MB RAM ✅ (but sleeps)
Render Standard = 2 GB RAM ✅✅ (if multiple users simultaneously)

---

## File Structure on Render (inside Docker)
```
/app/
├── bot.py
├── scraper.py
├── requirements.txt
└── Dockerfile
```
Chromium is pre-installed at: `/ms-playwright/chromium-*/chrome-linux/chrome`

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Build fails: `playwright install` error | Using wrong base image — make sure Dockerfile uses `mcr.microsoft.com/playwright/python:v1.44.0-jammy` |
| Bot starts but Chromium crashes | Add `--single-process` to Playwright launch args in scraper.py |
| Out of memory | Upgrade to Standard instance (2 GB) |
| Bot sleeps / slow response | Use Starter plan or UptimeRobot ping |
| `BOT_TOKEN` not found | Set it in Render Environment Variables, not in code |

---

## If Chromium crashes on Render, add this to scraper.py launch args:
```python
self._browser = await self._playwright.chromium.launch(
    headless=True,
    args=[
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--single-process",          # ← add this for Render
        "--no-zygote",               # ← add this for Render
    ],
)
```
