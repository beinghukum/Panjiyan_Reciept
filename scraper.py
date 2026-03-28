"""
scraper.py — Browser automation for mpeuparjan.mp.gov.in
Exact selectors derived from live page inspection (screenshots).

Page flow:
  1. frm_Rabi_FarmerDetails.aspx  — district select + kisan code + captcha
  2. Same page after ASP.NET postback — shows farmer info
     + "आवेदन पर्ची प्रिंट करने के लिए क्लिक करे" link
  3. PrintRegForm.aspx — receipt with "प्रिंट करे"
"""
import pyautogui
import time
import asyncio
import logging
import os
from pathlib import Path
from typing import Dict, Optional
import pyautogui
import time

from playwright.async_api import (
    async_playwright, Browser, BrowserContext, Page, Playwright, ElementHandle
)

logger = logging.getLogger(__name__)

TARGET_URL = (
    "https://mpeuparjan.mp.gov.in/euparjanmp/WPMS2026/frm_Rabi_FarmerDetails.aspx"
)

# Set DEBUG_HTML_DUMP=1 to save HTML + screenshots for selector debugging
DEBUG = os.environ.get("DEBUG_HTML_DUMP", "0") == "1"


class UserSession:
    def __init__(self, context: BrowserContext, page: Page):
        self.context = context
        self.page = page
        self.district: str = ""
        self.kisan_code: str = ""


class CaptchaError(Exception):
    pass


class KisanScraper:

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._sessions: Dict[int, UserSession] = {}
        self._lock = asyncio.Lock()

    # ── Browser lifecycle ──────────────────────────────────────────────────────


    async def _generate_pdf(self, page: Page) -> bytes:
        """
        EXACT Chrome-like print output
        """

        # Step 1: Trigger print page
        await page.evaluate("__doPostBack('btnPrint','')")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)

        # 🔥 Step 2: IMPORTANT → Apply PRINT MODE
        await page.emulate_media(media="print")

        # 🔥 Step 3: Remove green background via CSS (same as Chrome)
        await page.add_style_tag(content="""
            body {
                background: white !important;
            }

            * {
                background-color: transparent !important;
            }

            table {
                width: 100% !important;
                border-collapse: collapse !important;
            }
        """)

        # Step 4: Use Chrome print engine
        client = await page.context.new_cdp_session(page)

        pdf = await client.send("Page.printToPDF", {
            "printBackground": False,   # 🔥 KEY (removes green)
            "paperWidth": 8.27,
            "paperHeight": 11.69,
            "marginTop": 0.4,
            "marginBottom": 0.4,
            "marginLeft": 0.4,
            "marginRight": 0.4,
            "scale": 0.99,              # match Chrome custom scale
            "preferCSSPageSize": True
        })

        import base64
        return base64.b64decode(pdf['data'])

    async def _ensure_browser(self):
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=False,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )
                logger.info("Browser launched.")

    async def _get_or_create_session(self, user_id: int) -> UserSession:
        await self._ensure_browser()
        if user_id not in self._sessions:
            context = await self._browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="hi-IN",
                extra_http_headers={"Accept-Language": "hi-IN,hi;q=0.9,en;q=0.8"},
            )
            page = await context.new_page()
            self._sessions[user_id] = UserSession(context, page)
            logger.info("New session for user %s", user_id)
        return self._sessions[user_id]

    async def close_session(self, user_id: int):
        if user_id in self._sessions:
            sess = self._sessions.pop(user_id)
            try:
                await sess.context.close()
            except Exception:
                pass
            logger.info("Session closed for user %s", user_id)

    # ── Debug helpers ──────────────────────────────────────────────────────────

    async def _debug_dump(self, page: Page, tag: str):
        if not DEBUG:
            return
        d = Path("debug_dumps")
        d.mkdir(exist_ok=True)
        (d / f"{tag}.html").write_text(await page.content(), encoding="utf-8")
        await page.screenshot(path=str(d / f"{tag}.png"), full_page=True)
        logger.info("DEBUG dump saved: %s", tag)

    # ── Step 1: Load page → fill form → return CAPTCHA image ──────────────────

    async def load_form_and_get_captcha(
        self, user_id: int, district: str, kisan_code: str
    ) -> bytes:
        sess = await self._get_or_create_session(user_id)
        page = sess.page
        sess.district = district
        sess.kisan_code = kisan_code

        logger.info("Loading page for user %s", user_id)
        await page.goto(TARGET_URL, wait_until="networkidle", timeout=30_000)
        await self._debug_dump(page, "01_loaded")

        await self._select_district(page, district)
        await self._fill_kisan_code(page, kisan_code)

        await self._debug_dump(page, "02_form_filled")
        return await self._capture_captcha(page)

    async def _select_district(self, page: Page, district: str):
        """Select district from the <select> dropdown."""
        selects = await page.query_selector_all("select")
        logger.info("Found %d <select> elements", len(selects))
        for sel_el in selects:
            options = await sel_el.query_selector_all("option")
            for opt in options:
                text = (await opt.inner_text()).strip()
                if district.strip() == text or district.strip() in text:
                    val = await opt.get_attribute("value")
                    sel_id = await sel_el.get_attribute("id") or "?"
                    logger.info("Selecting district=%r in select#%s val=%r", district, sel_id, val)
                    await sel_el.select_option(value=val)
                    await page.wait_for_timeout(400)
                    return
        # Fallback: select by label
        for sel_el in selects:
            try:
                await sel_el.select_option(label=district)
                logger.info("District selected by label fallback")
                return
            except Exception:
                pass
        logger.warning("District %r not found in any select", district)

    async def _fill_kisan_code(self, page: Page, kisan_code: str):
        """Fill the kisan code input. Real ID confirmed: txt_SearchID"""
        # Try known real IDs first (from log: input#txt_SearchID)
        known_ids = ["txt_SearchID", "txtSearchID", "txtKisanCode", "txtMobile"]
        for kid in known_ids:
            el = await page.query_selector(f"#{kid}")
            if el and await el.is_visible():
                logger.info("Filling kisan code in input#%s", kid)
                await page.fill(f"#{kid}", kisan_code)
                return
        # Fallback: first visible text input
        inputs = await page.query_selector_all("input[type='text']")
        for inp in inputs:
            if await inp.is_visible():
                iid = await inp.get_attribute("id") or "?"
                logger.info("Filling kisan code in input#%s (fallback)", iid)
                await inp.fill(kisan_code)
                return
        logger.warning("No visible text input found for kisan code")

    # ── CAPTCHA capture ────────────────────────────────────────────────────────

    async def _capture_captcha(self, page: Page) -> bytes:
        """Capture the CAPTCHA image from the page."""
        patterns = [
            "img[src*='CaptchaImage']",
            "img[src*='Captcha']",
            "img[src*='captcha']",
            "img[src*='ValidateCode']",
            "img[id*='aptcha']",
            "img[id*='Captcha']",
        ]
        captcha_el = None
        for p in patterns:
            el = await page.query_selector(p)
            if el:
                src = await el.get_attribute("src") or ""
                logger.info("CAPTCHA img found: selector=%s src=%s", p, src)
                captcha_el = el
                break

        if not captcha_el:
            # Scan all images for captcha-like src
            for img in await page.query_selector_all("img"):
                src = (await img.get_attribute("src") or "").lower()
                if any(k in src for k in ["captcha", "code", "validate", "verify"]):
                    captcha_el = img
                    logger.info("CAPTCHA found by src scan: %s", src)
                    break

        if not captcha_el:
            logger.warning("CAPTCHA img not found, sending viewport screenshot")
            return await page.screenshot(type="png")

        box = await captcha_el.bounding_box()
        if box:
            return await page.screenshot(
                type="png",
                clip={
                    "x": max(0, box["x"] - 6),
                    "y": max(0, box["y"] - 6),
                    "width": box["width"] + 12,
                    "height": box["height"] + 12,
                },
            )
        return await captcha_el.screenshot(type="png")

    # ── Refresh CAPTCHA ────────────────────────────────────────────────────────

    async def refresh_captcha(self, user_id: int) -> bytes:
        """Click "Captcha बदले" button and re-capture."""
        sess = self._sessions[user_id]
        page = sess.page
        refresh_sels = [
            "input[value*='Captcha बदले']",
            "input[value*='बदले']",
            "input[value*='Refresh']",
            "button:has-text('बदले')",
            "button:has-text('Refresh')",
        ]
        for sel in refresh_sels:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                logger.info("Clicking CAPTCHA refresh: %s", sel)
                await el.click()
                await page.wait_for_timeout(1200)
                break
        return await self._capture_captcha(page)

    # ── Step 2: Submit → find receipt link → return PDF ───────────────────────

    async def submit_and_get_receipt_pdf(
        self, user_id: int, captcha_text: str
    ) -> bytes:
        sess = self._sessions[user_id]
        page = sess.page

        # Fill CAPTCHA
        await self._fill_captcha_input(page, captcha_text)

        # Click "किसान सर्च करे"
        await self._click_search_button(page)

        # ASP.NET postback — wait for DOM to settle on same page
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)

        await self._debug_dump(page, "03_after_search")

        # Check page text for errors / farmer data
        body_text = await page.inner_text("body")
        logger.info("Body after search (first 400 chars): %r", body_text[:400])

        # CAPTCHA error detection
        captcha_err = ["गलत", "wrong captcha", "invalid captcha", "captcha incorrect"]
        if any(w in body_text.lower() for w in captcha_err):
            raise CaptchaError("CAPTCHA गलत है")

        # Find "आवेदन पर्ची प्रिंट करने के लिए क्लिक करे"
        receipt_link = await self._find_receipt_link(page)
        if receipt_link is None:
            await self._debug_dump(page, "03x_no_receipt_found")
            # Build a helpful error with what we actually see
            preview = body_text[:300].strip()
            raise Exception(
                "आवेदन पर्ची लिंक नहीं मिला।\n\n"
                "संभावित कारण:\n"
                "• किसान कोड / मोबाइल नं. गलत\n"
                "• इस सीज़न में पंजीयन नहीं\n"
                "• CAPTCHA गलत था\n\n"
                "/start से दोबारा कोशिश करें।"
            )

        # Click receipt link (may open new tab or navigate same tab)
        receipt_page = await self._click_receipt_link(sess, receipt_link)
        await self._debug_dump(receipt_page, "04_receipt_page")

        # Generate PDF
        pdf_bytes = await self._generate_pdf(receipt_page)
        try:
            await receipt_page.close()
        except Exception:
            pass
        return pdf_bytes

    async def _fill_captcha_input(self, page: Page, captcha_text: str):
        """
        Fill the CAPTCHA text box.
        From Image 1: it's the text input right next to the CAPTCHA image.
        Strategy: find input whose ID/name contains captcha-like words,
        else use the LAST visible text input (kisan code is first, captcha is last).
        """
        patterns = [
            "input[id*='aptcha']",
            "input[id*='Captcha']",
            "input[id*='captcha']",
            "input[id*='CaptchaCode']",
            "input[id*='txtCaptcha']",
            "input[name*='aptcha']",
            "input[name*='Captcha']",
        ]
        for p in patterns:
            el = await page.query_selector(p)
            if el and await el.is_visible():
                iid = await el.get_attribute("id") or "?"
                logger.info("Filling CAPTCHA in input#%s (%s)", iid, p)
                await page.fill(p, captcha_text)
                return

        # Fallback: last visible text input = captcha box
        inputs = await page.query_selector_all("input[type='text']")
        visible = [i for i in inputs if await i.is_visible()]
        if len(visible) >= 2:
            last = visible[-1]
            iid = await last.get_attribute("id") or "?"
            logger.info("CAPTCHA fallback → last visible input#%s", iid)
            await last.click(click_count=3)
            await last.fill(captcha_text)
        elif visible:
            await visible[0].click(click_count=3)
            await visible[0].fill(captcha_text)
        else:
            logger.error("No visible text inputs found for CAPTCHA!")

    async def _click_search_button(self, page: Page):
        """
        Click "किसान सर्च करे" (the green button from Image 1).
        It's rendered as <input type="button" value="किसान सर्च करे">
        Avoid "नया" (reset) and "Captcha बदले" buttons.
        """
        skip_values = ["नया", "captcha बदले", "बदले", "reset", "clear"]
        search_values = ["किसान सर्च", "सर्च करे", "search", "खोज"]

        all_btns = await page.query_selector_all(
            "input[type='button'], input[type='submit'], button"
        )
        logger.info("Total buttons: %d", len(all_btns))

        for btn in all_btns:
            val = (await btn.get_attribute("value") or "").strip()
            bid = await btn.get_attribute("id") or "?"
            logger.info("  btn id=%r value=%r", bid, val)

            val_lower = val.lower()
            if any(s in val_lower for s in skip_values):
                continue
            if any(s in val or s in val_lower for s in search_values):
                logger.info("Clicking search button: id=%r value=%r", bid, val)
                await btn.click()
                return

        # Fallback: first non-skip visible button
        for btn in all_btns:
            if not await btn.is_visible():
                continue
            val = (await btn.get_attribute("value") or "").lower()
            if any(s in val for s in skip_values):
                continue
            bid = await btn.get_attribute("id") or "?"
            logger.info("Search fallback: clicking btn id=%r", bid)
            await btn.click()
            return

        logger.error("Search button not found!")

    async def _find_receipt_link(self, page: Page) -> Optional[ElementHandle]:
        """
        Find "आवेदन पर्ची प्रिंट करने के लिए क्लिक करे" link.
        From Image 1: it's a plain blue <a> hyperlink below the form.
        """
        receipt_keywords = [
            "आवेदन पर्ची प्रिंट करने के लिए क्लिक करे",
            "आवेदन पर्ची",
            "पर्ची प्रिंट",
            "पर्ची",
            "PrintRegForm",
        ]

        # Check all <a> tags
        links = await page.query_selector_all("a")
        logger.info("Scanning %d <a> tags for receipt link", len(links))
        for lnk in links:
            try:
                txt = (await lnk.inner_text()).strip()
                href = (await lnk.get_attribute("href") or "")
                if txt:
                    logger.info("  <a> text=%r href=%r", txt[:80], href[:60])
                if any(kw in txt or kw in href for kw in receipt_keywords):
                    logger.info("Receipt <a> found: text=%r href=%r", txt, href)
                    return lnk
            except Exception:
                pass

        # Check input buttons too (ASP.NET LinkButton)
        btns = await page.query_selector_all(
            "input[type='button'], input[type='submit'], button"
        )
        for btn in btns:
            val = (await btn.get_attribute("value") or "").strip()
            if any(kw in val for kw in receipt_keywords):
                logger.info("Receipt <input> found: value=%r", val)
                return btn

        logger.warning("Receipt link not found. Total links: %d", len(links))
        return None

    async def _click_receipt_link(self, sess: UserSession, element: ElementHandle) -> Page:
        """
        Click the receipt link and return the resulting page.
        Handles both new-tab (target=_blank) and same-tab navigation.
        """
        page = sess.page
        target = (await element.get_attribute("target") or "").strip()
        href = (await element.get_attribute("href") or "").strip()
        logger.info("Receipt link: target=%r href=%r", target, href)

        if target == "_blank":
            # Opens in new tab
            async with sess.context.expect_page(timeout=15_000) as new_info:
                await element.click()
            new_page = await new_info.value
            await new_page.wait_for_load_state("networkidle", timeout=20_000)
            logger.info("Receipt page (new tab): %s", new_page.url)
            return new_page

        elif "PrintRegForm" in href or href.endswith(".aspx"):
            # Relative/absolute URL — navigate in same tab
            try:
                async with page.expect_navigation(timeout=15_000):
                    await element.click()
            except Exception:
                await page.wait_for_timeout(2000)
            await page.wait_for_load_state("networkidle", timeout=20_000)
            logger.info("Receipt page (same tab): %s", page.url)
            return page

        else:
            # __doPostBack or unknown — try new tab first, fallback same tab
            try:
                async with sess.context.expect_page(timeout=8_000) as new_info:
                    await element.click()
                new_page = await new_info.value
                await new_page.wait_for_load_state("networkidle", timeout=20_000)
                logger.info("Receipt page (new tab fallback): %s", new_page.url)
                return new_page
            except Exception:
                try:
                    async with page.expect_navigation(timeout=8_000):
                        await element.click()
                except Exception:
                    await page.wait_for_timeout(2000)
                await page.wait_for_load_state("networkidle", timeout=15_000)
                logger.info("Receipt page (same tab fallback): %s", page.url)
                return page

    