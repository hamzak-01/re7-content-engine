"""Keep the Streamlit app awake.

Streamlit Community Cloud puts an app to sleep after roughly 12 hours without a visitor and
only wakes it when someone clicks the "get this app back up" button. This script opens the
app in a headless browser, clicks that button if it is showing, and waits until the
dashboard has rendered. GitHub Actions runs it every four hours (see .github/workflows).
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

URL = os.environ.get("STREAMLIT_APP_URL", "https://re7-content-engine-exjyfdacpj7fn7rhzgtmzg.streamlit.app")
READY_TEXT = "Re7 Content Engine"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        time.sleep(5)
        button = page.get_by_text("get this app back up", exact=False)
        if button.count():
            print("app was asleep; waking it")
            button.first.click()
        else:
            print("no wake button; app appears awake")
        # the dashboard title lives inside an iframe on the hosted page, so search all frames
        deadline = time.time() + 180
        while time.time() < deadline:
            for frame in page.frames:
                try:
                    if READY_TEXT in frame.content():
                        print("dashboard rendered")
                        time.sleep(10)  # count as a real visit
                        browser.close()
                        return 0
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(5)
        print("timed out waiting for the dashboard")
        page.screenshot(path="keepalive_timeout.png")
        browser.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())
