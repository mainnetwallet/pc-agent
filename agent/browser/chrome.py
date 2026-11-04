"""Browser automation using Playwright (Chrome/Edge/Firefox).

Maintains a dedicated persistent agent browser profile separate from the user's
default profile. Respects CAPTCHA/MFA/authentication boundaries (does not bypass
them).
"""

import os
from typing import Any, Dict, List, Optional

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

AGENT_PROFILE_DIR = os.path.join(os.path.expanduser("~"), "PC Agent Browser Profile")


class BrowserControl:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def start(self, browser_name: str = "chrome", profile: str = "agent") -> None:
        self._playwright = sync_playwright().start()
        os.makedirs(AGENT_PROFILE_DIR, exist_ok=True)
        user_data_dir = os.path.join(AGENT_PROFILE_DIR, profile)

        if browser_name.lower() == "chrome":
            channel = "chrome"
        elif browser_name.lower() == "edge":
            channel = "msedge"
        elif browser_name.lower() == "firefox":
            channel = None
        else:
            channel = "chrome"

        if browser_name.lower() == "firefox":
            self._browser = self._playwright.firefox.launch_persistent_context(
                user_data_dir=user_data_dir, headless=self.headless,
            )
            # launch_persistent_context returns a BrowserContext directly
            self._context = self._browser
            self._browser = self._context.browser
        else:
            self._browser = self._playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir, channel=channel, headless=self.headless,
            )
            self._context = self._browser

        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()

    def _ensure(self) -> None:
        if self._context is None:
            self.start()

    def new_page(self) -> Page:
        self._ensure()
        self._page = self._context.new_page()
        return self._page

    def navigate(self, url: str, wait_until: str = "load") -> Dict[str, Any]:
        self._ensure()
        page = self._page or self._context.new_page()
        self._page = page
        try:
            response = page.goto(url, wait_until=wait_until, timeout=30000)
            return {
                "ok": True,
                "url": page.url,
                "title": page.title(),
                "status": response.status if response else None,
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "url": url}

    def search_google(self, query: str) -> Dict[str, Any]:
        res = self.navigate("https://www.google.com")
        if not res["ok"]:
            return res
        page = self._page
        try:
            page.fill('input[name="q"]', query)
            page.press('input[name="q"]', "Enter")
            page.wait_for_load_state("networkidle", timeout=15000)
            return {"ok": True, "url": page.url, "title": page.title()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def click_selector(self, selector: str, timeout: int = 10000) -> Dict[str, Any]:
        self._ensure()
        page = self._page
        try:
            page.click(selector, timeout=timeout)
            return {"ok": True, "selector": selector}
        except Exception as e:
            return {"ok": False, "error": str(e), "selector": selector}

    def fill_selector(self, selector: str, value: str, timeout: int = 10000) -> Dict[str, Any]:
        self._ensure()
        page = self._page
        try:
            page.fill(selector, value, timeout=timeout)
            return {"ok": True, "selector": selector}
        except Exception as e:
            return {"ok": False, "error": str(e), "selector": selector}

    def press_selector(self, selector: str, key: str) -> Dict[str, Any]:
        self._ensure()
        page = self._page
        try:
            page.press(selector, key)
            return {"ok": True, "selector": selector, "key": key}
        except Exception as e:
            return {"ok": False, "error": str(e), "selector": selector}

    def get_content(self) -> str:
        self._ensure()
        return self._page.content() if self._page else ""

    def get_url(self) -> str:
        self._ensure()
        return self._page.url if self._page else ""

    def get_title(self) -> str:
        self._ensure()
        return self._page.title() if self._page else ""

    def screenshot(self, path: Optional[str] = None, full_page: bool = False) -> Optional[str]:
        self._ensure()
        page = self._page
        if page is None:
            return None
        if path is None:
            from config import SCREENSHOTS_DIR
            os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
            path = os.path.join(SCREENSHOTS_DIR, f"browser_{int(__import__('time').time()*1000)}.png")
        page.screenshot(path=path, full_page=full_page)
        return path

    def evaluate(self, script: str) -> Any:
        self._ensure()
        return self._page.evaluate(script) if self._page else None

    def list_tabs(self) -> List[Dict[str, Any]]:
        self._ensure()
        out = []
        for p in self._context.pages:
            out.append({"url": p.url, "title": p.title()})
        return out

    def switch_tab(self, index: int) -> bool:
        self._ensure()
        pages = self._context.pages
        if 0 <= index < len(pages):
            self._page = pages[index]
            pages[index].bring_to_front()
            return True
        return False

    def close(self) -> None:
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser and self._browser is not self._context:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass


# Singleton instance
browser = BrowserControl()


def get_browser() -> BrowserControl:
    return browser
