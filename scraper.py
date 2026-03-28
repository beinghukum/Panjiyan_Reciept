"""
scraper.py — Browser automation for mpeuparjan.mp.gov.in
Uses Playwright (async) + Chrome DevTools Protocol (CDP) for exact print output.

Page flow:
  1. frm_Rabi_FarmerDetails.aspx  — district select + kisan code + captcha
  2. Same page after ASP.NET postback — shows farmer info
     + "आवेदन पर्ची प्रिंट करने के लिए क्लिक करे" link
  3. PrintRegForm.aspx — receipt page → PDF via CDP Page.printToPDF
"""

import asyncio
import base64
import logging
import os
from pathlib import Path
from typing import Dict, Optional

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

    async def _ensure_browser(self):
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,          # must be True on Render
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--no-zygote",
                        "--single-process",  # required on Render containers
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
        """Select district from the <select> dropdown. Real ID: ddlDistrict"""
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

        # Check page text for errors
        body_text = await page.inner_text("body")
        logger.info("Body after search (first 400): %r", body_text[:400])

        captcha_err = ["गलत", "wrong captcha", "invalid captcha", "captcha incorrect"]
        if any(w in body_text.lower() for w in captcha_err):
            raise CaptchaError("CAPTCHA गलत है")

        # Find receipt link
        receipt_link = await self._find_receipt_link(page)
        if receipt_link is None:
            await self._debug_dump(page, "03x_no_receipt_found")
            raise Exception(
                "आवेदन पर्ची लिंक नहीं मिला।\n\n"
                "संभावित कारण:\n"
                "• किसान कोड / मोबाइल नं. गलत\n"
                "• इस सीज़न में पंजीयन नहीं\n"
                "• CAPTCHA गलत था\n\n"
                "/start से दोबारा कोशिश करें।"
            )

        # Click receipt link → get PrintRegForm.aspx page
        receipt_page = await self._click_receipt_link(sess, receipt_link)
        await self._debug_dump(receipt_page, "04_receipt_page")

        # Generate PDF using CDP
        pdf_bytes = await self._generate_pdf(receipt_page)
        try:
            await receipt_page.close()
        except Exception:
            pass
        return pdf_bytes

    async def _fill_captcha_input(self, page: Page, captcha_text: str):
        """Fill the CAPTCHA text box — last visible text input on page."""
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
            await last.fill(captcha_text)
        elif visible:
            await visible[0].fill(captcha_text)
        else:
            logger.error("No visible text inputs found for CAPTCHA!")

    async def _click_search_button(self, page: Page):
        """Click 'किसान सर्च करे' — skip reset/captcha buttons."""
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
        """Find 'आवेदन पर्ची प्रिंट करने के लिए क्लिक करे' link."""
        receipt_keywords = [
            "आवेदन पर्ची प्रिंट करने के लिए क्लिक करे",
            "आवेदन पर्ची",
            "पर्ची प्रिंट",
            "पर्ची",
            "PrintRegForm",
        ]

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

        # ASP.NET LinkButton fallback
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
        Click receipt link and return the loaded PrintRegForm.aspx page.
        NEVER use page.goto() here — it loses the ASP.NET session.
        Click naturally so cookies stay intact.
        """
        page = sess.page
        target = (await element.get_attribute("target") or "").strip()
        href   = (await element.get_attribute("href")   or "").strip()
        logger.info("Receipt link: target=%r href=%r", target, href)

        if target == "_blank":
            # Opens in a new tab
            async with sess.context.expect_page(timeout=15_000) as new_info:
                await element.click()
            new_page = await new_info.value
            await new_page.wait_for_load_state("networkidle", timeout=25_000)
            await self._wait_for_content(new_page)
            logger.info("Receipt page (new tab): %s", new_page.url)
            return new_page

        elif "PrintRegForm" in href or href.endswith(".aspx"):
            # Same-tab navigation
            try:
                async with page.expect_navigation(timeout=15_000):
                    await element.click()
            except Exception:
                await page.wait_for_timeout(2000)
            await page.wait_for_load_state("networkidle", timeout=25_000)
            await self._wait_for_content(page)
            logger.info("Receipt page (same tab): %s", page.url)
            return page

        else:
            # Unknown — try new tab first, fall back to same tab
            try:
                async with sess.context.expect_page(timeout=8_000) as new_info:
                    await element.click()
                new_page = await new_info.value
                await new_page.wait_for_load_state("networkidle", timeout=20_000)
                await self._wait_for_content(new_page)
                logger.info("Receipt page (new tab fallback): %s", new_page.url)
                return new_page
            except Exception:
                try:
                    async with page.expect_navigation(timeout=8_000):
                        await element.click()
                except Exception:
                    await page.wait_for_timeout(2000)
                await page.wait_for_load_state("networkidle", timeout=15_000)
                await self._wait_for_content(page)
                logger.info("Receipt page (same tab fallback): %s", page.url)
                return page

    async def _wait_for_content(self, page: Page, timeout_ms: int = 10_000):
        """Wait until page body has meaningful content (not blank)."""
        import time
        start = time.time()
        while (time.time() - start) * 1000 < timeout_ms:
            try:
                body = (await page.inner_text("body")).strip()
                if len(body) > 100:
                    logger.info("Page content ready (%d chars)", len(body))
                    return
            except Exception:
                pass
            await page.wait_for_timeout(300)
        logger.warning("Content wait timed out — proceeding anyway")

    # ── PDF Generation via CDP ─────────────────────────────────────────────────

    async def _generate_pdf(self, page: Page) -> bytes:
        """
        Generate PDF using Chrome DevTools Protocol Page.printToPDF.
        This gives the exact same output as clicking "Print" in Chrome.

        Why CDP instead of page.pdf():
        - page.pdf() ignores CSS @media print rules on some ASP.NET pages
        - CDP printToPDF is the actual Chrome print engine — same as Ctrl+P
        - Matches the hemkunwar.pdf reference exactly

        Scale 0.82 on A4 fits all farmer details + khasra table on one page.
        Farmers with 10+ khasra rows may need 2 pages — that is acceptable.
        """
        # Wait for Hindi fonts, logo, and table data to fully render
        await page.wait_for_timeout(2000)

        # Verify page has real content before generating PDF
        body = (await page.inner_text("body")).strip()
        logger.info("Receipt body preview: %r", body[:200])
        if len(body) < 50:
            raise Exception(
                "Receipt page is blank — session expired.\n"
                "/start से दोबारा कोशिश करें।"
            )

        # Inject minimal CSS:
        # - Hide top nav links (पीछे जाये / प्रिंट करे) from PDF
        # - Prevent table horizontal overflow
        await page.add_style_tag(content="""
            /* Hide top navigation links in PDF output */
            body > center:first-child,
            body > div:first-child > a,
            body > p:first-child { display: none !important; }

            /* Prevent horizontal overflow in tables */
            table {
                width: 100% !important;
                table-layout: fixed !important;
                word-break: break-word !important;
                border-collapse: collapse !important;
            }
            td, th {
                word-break: break-word !important;
                overflow-wrap: break-word !important;
            }
            img { max-width: 100% !important; }
        """)

        # Switch to print media so @media print CSS rules apply
        await page.emulate_media(media="print")
        await page.wait_for_timeout(500)  # let print styles settle

        # Use CDP Page.printToPDF — exact Chrome print engine
        cdp = await page.context.new_cdp_session(page)
        result = await cdp.send("Page.printToPDF", {
            "printBackground": True,    # keep green receipt background
            "paperWidth":  8.27,        # A4 width in inches
            "paperHeight": 11.69,       # A4 height in inches
            "marginTop":    0.4,        # ~10mm
            "marginBottom": 0.4,
            "marginLeft":   0.31,       # ~8mm
            "marginRight":  0.31,
            "scale": 0.82,             # tuned to fit receipt on 1 page
            "preferCSSPageSize": False,
        })
        await cdp.detach()

        pdf_bytes = base64.b64decode(result["data"])
        if not pdf_bytes:
            raise Exception("CDP PDF generation returned empty data.")

        logger.info("CDP PDF generated: %d bytes", len(pdf_bytes))
        return pdf_bytes
