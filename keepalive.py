"""Keep the Streamlit app awake.

Streamlit Community Cloud puts an app to sleep after roughly 12 hours without a visitor and
only wakes it when someone clicks the "get this app back up" button. This script opens the
app in a headless browser, clicks that button whenever it appears, and waits until the
dashboard has rendered. GitHub Actions runs it every four hours (see .github/workflows).
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

URL = os.environ.get("STREAMLIT_APP_URL", "https://re7-content-engine-exjyfdacpj7fn7rhzgtmzg.streamlit.app")
READY_TEXT = "Re7 Content Engine"
WAKE_TEXT = "get this app back up"
TOTAL_SECONDS = 300


def visible_text(page) -> str:
    out = []
    for frame in page.frames:
        try:
            out.append(frame.evaluate("() => document.body ? document.body.innerText : ''"))
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(out)


def try_wake(page) -> bool:
    """Click the wake-up button in any frame if it is showing. Returns True if clicked."""
    for frame in page.frames:
        try:
            btn = frame.get_by_text(WAKE_TEXT, exact=False)
            if btn.count():
                btn.first.click(timeout=5000)
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(URL, wait_until="domcontentloaded", timeout=90_000)
        deadline = time.time() + TOTAL_SECONDS
        clicked = 0
        last_reload = time.time()
        while time.time() < deadline:
            text = visible_text(page)
            if READY_TEXT in text:
                print(f"dashboard rendered after {int(TOTAL_SECONDS - (deadline - time.time()))}s; wake clicks: {clicked}")
                time.sleep(15)  # count as a real visit
                browser.close()
                return 0
            if try_wake(page):
                clicked += 1
                print("clicked the wake-up button")
                time.sleep(20)
                continue
            # A page that shows neither the dashboard nor the button may be mid-boot; reload now and then.
            if time.time() - last_reload > 60:
                try:
                    page.reload(wait_until="domcontentloaded", timeout=90_000)
                except Exception:  # noqa: BLE001
                    pass
                last_reload = time.time()
            time.sleep(5)
        snippet = visible_text(page).strip().replace("\n", " | ")[:600]
        print(f"timed out after {TOTAL_SECONDS}s; wake clicks: {clicked}; page title: {page.title()!r}")
        print(f"visible text: {snippet!r}")
        page.screenshot(path="keepalive_timeout.png", full_page=True)
        browser.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())
