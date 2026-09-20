"""
AliExpress daily coin collector - headless (Docker-ready) version.

Same flow as the original Selenium script:
  1. Open the coin page
  2. Log in (human-like typing) if the session is not still valid
  3. Set the ship-to country to Korea (best coin rewards)
  4. Click the daily "Collect" button
  5. If anything fails, restart the whole cycle (up to MAX_ATTEMPTS)

Designed to run unattended in Docker on a small always-on machine.
"""

import json
import os
import random
import sys
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv
from playwright.sync_api import (
    sync_playwright,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

# === FORCE UTF-8 OUTPUT ===
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# === Configuration (everything can be overridden via env / .env) ===
load_dotenv()

ALIEXPRESS_EMAIL = os.getenv("ALIEXPRESS_EMAIL")
ALIEXPRESS_PASSWORD = os.getenv("ALIEXPRESS_PASSWORD")
COIN_URL = os.getenv("COIN_URL", "https://s.click.aliexpress.com/e/_DB2kEjh")
HEADLESS = os.getenv("HEADLESS", "true").strip().lower() not in ("0", "false", "no")
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_STATE_PATH = os.getenv(
    "STORAGE_STATE_PATH", os.path.join(BASE_DIR, "data", "storage_state.json")
)
DEBUG_DIR = os.getenv("DEBUG_DIR", os.path.join(BASE_DIR, "data", "debug"))

# Where the "same time every 24h" anchor is persisted (survives restarts)
ANCHOR_PATH = os.path.join(
    os.path.dirname(STORAGE_STATE_PATH) or ".", "schedule.json"
)

# Basic stealth: hide the most common automation signals
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""

# Container-friendly browser flags (also fine for local runs)
BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-blink-features=AutomationControlled",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def random_sleep(min_s=1.0, max_s=3.0):
    """Sleep for a random amount of time to mimic human behavior."""
    time.sleep(random.uniform(min_s, max_s))


def screenshot(page, name):
    """Save a screenshot for debugging (best effort)."""
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        path = os.path.join(DEBUG_DIR, f"{datetime.now():%Y%m%d-%H%M%S}-{name}.png")
        page.screenshot(path=path)
        print(f"[debug] screenshot saved: {path}")
    except Exception as e:
        print(f"[debug] could not save screenshot: {e}")


def find_first_visible(page, selectors, timeout_each_ms=10000):
    """Return the first locator (in the given order) that becomes visible, else None."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout_each_ms)
            print(f"[ok] element found: {sel}")
            return loc
        except PlaywrightTimeoutError:
            continue
        except PlaywrightError as e:
            print(f"[warn] selector failed: {sel} ({e})")
            continue
    return None


def human_click(element):
    """Click at a random position inside the element (more human-like)."""
    try:
        box = element.bounding_box()
        if box and box["width"] > 4 and box["height"] > 4:
            element.click(
                position={
                    "x": random.uniform(0.25, 0.75) * box["width"],
                    "y": random.uniform(0.25, 0.75) * box["height"],
                }
            )
            return
    except Exception:
        pass
    element.click()


def type_like_human(page, locator, text):
    """Type text with human-like timing and occasional mistakes that get corrected."""
    for char in text:
        # Randomly decide if we make a typo (1% chance)
        if random.random() < 0.01:
            page.keyboard.type(random.choice("qwertyuiopasdfghjklzxcvbnm"))
            random_sleep(0.1, 0.3)
            page.keyboard.press("Backspace")
            random_sleep(0.2, 0.5)

        # Type the correct character
        page.keyboard.type(char)

        # Random pause between keystrokes
        random_sleep(0.05, 0.15)

        # Occasionally pause longer as if thinking
        if random.random() < 0.05:
            random_sleep(0.5, 1.2)


def is_login_form_visible(page, timeout_ms=15000):
    """True if the login email field shows up (i.e. we are NOT logged in)."""
    try:
        page.locator("input.cosmos-input[label='Email']").first.wait_for(
            state="visible", timeout=timeout_ms
        )
        return True
    except (PlaywrightTimeoutError, PlaywrightError):
        return False


def detect_captcha(page):
    """Best-effort detection of AliExpress/Alibaba anti-bot challenges."""
    try:
        return page.evaluate(
            """() => {
                const sels = ['[id*="nc_"]', '[class*="nc_"]', '[class*="captcha"]',
                              '[class*="baxia"]', 'iframe[src*="punish"]'];
                for (const s of sels) { if (document.querySelector(s)) return true; }
                return false;
            }"""
        )
    except Exception:
        return False


def save_session(context):
    """Persist cookies so the next run can skip the login step."""
    try:
        d = os.path.dirname(STORAGE_STATE_PATH)
        if d:
            os.makedirs(d, exist_ok=True)
        context.storage_state(path=STORAGE_STATE_PATH)
        print(f"Session saved to {STORAGE_STATE_PATH}")
    except Exception as e:
        print(f"Could not save session: {e}")


# ---------------------------------------------------------------------------
# Flow steps
# ---------------------------------------------------------------------------

def login(page):
    """Perform the login process with human-like behavior."""
    try:
        print("Starting login process...")

        # Email field
        email_input = page.locator("input.cosmos-input[label='Email']").first
        email_input.wait_for(state="visible", timeout=15000)
        print("Found email input field")
        email_input.scroll_into_view_if_needed()
        random_sleep(1, 2)
        email_input.click()
        random_sleep(0.5, 1.5)

        # Type email with human-like behavior
        print("Entering email address...")
        type_like_human(page, email_input, ALIEXPRESS_EMAIL)
        random_sleep(1, 2)

        # Continue button
        continue_button = page.locator(
            "xpath=//button[contains(@class, 'cosmos-btn-primary') and .//span[text()='Continue']]"
        ).first
        continue_button.wait_for(state="visible", timeout=15000)
        print("Found continue button")
        continue_button.scroll_into_view_if_needed()
        random_sleep(0.5, 1)
        human_click(continue_button)
        print("Clicked continue button")
        random_sleep(2, 3)

        # Password field
        password_input = page.locator("#fm-login-password").first
        password_input.wait_for(state="visible", timeout=15000)
        print("Found password field")
        password_input.scroll_into_view_if_needed()
        random_sleep(0.5, 1)
        password_input.click()
        random_sleep(0.5, 1)

        # Type password with human-like behavior
        print("Entering password...")
        type_like_human(page, password_input, ALIEXPRESS_PASSWORD)
        random_sleep(1, 2)

        # Sign in button
        sign_in_button = page.locator(
            "xpath=//button[contains(@class, 'cosmos-btn-primary') and .//span[text()='Sign in']]"
        ).first
        sign_in_button.wait_for(state="visible", timeout=15000)
        print("Found sign in button")
        sign_in_button.scroll_into_view_if_needed()
        random_sleep(0.5, 1)
        human_click(sign_in_button)
        print("Clicked sign in button")

        # Wait for the redirect back to the coin page (login complete)
        try:
            page.wait_for_url(lambda url: "s.click.aliexpress.com" in url, timeout=45000)
            print("Login successful (back on the coin page)")
            return True
        except PlaywrightTimeoutError:
            random_sleep(5, 7)

        if detect_captcha(page):
            print("WARNING: captcha / anti-bot challenge detected, login not completed")
            screenshot(page, "login-captcha")
            return False

        if is_login_form_visible(page, timeout_ms=6000):
            print("Login probably failed: login form is still visible")
            screenshot(page, "login-failed")
            return False

        print("Login successful")
        return True

    except Exception as e:
        print(f"Login failed: {e}")
        screenshot(page, "login-error")
        return False


def change_country_to_korea(page):
    """Change the country to Korea using the ship-to dropdown."""
    try:
        # STEP 1: open the ship-to dropdown
        print("STEP 1: looking for the ship-to dropdown...")
        ship_to = find_first_visible(
            page,
            [
                "xpath=//div[contains(@class, 'ship-to--menuItem--')]",
                "xpath=//div[contains(@class, 'ship-to--text--')]/b[contains(text(), 'USD')]",
                "xpath=//div[contains(@class, 'es--wrap--')]/div/div[contains(@class, 'ship-to--menuItem--')]",
            ],
            timeout_each_ms=10000,
        )
        if ship_to is None:
            print("Could not find the ship-to dropdown")
            screenshot(page, "ship-to-not-found")
            return False
        ship_to.scroll_into_view_if_needed()
        random_sleep(1)
        human_click(ship_to)
        print("Clicked ship-to dropdown")
        random_sleep(2, 3)

        # STEP 2: open the country selector
        print("STEP 2: looking for the country selector...")
        country_selector = find_first_visible(
            page,
            [
                "xpath=//div[contains(@class, 'select--text--1b85oDo')]",
                "xpath=//div[contains(@class, 'select--text--')]",
            ],
            timeout_each_ms=15000,
        )
        if country_selector is None:
            print("Could not find the country selector")
            screenshot(page, "country-selector-not-found")
            return False
        country_selector.scroll_into_view_if_needed()
        random_sleep(0.5, 1)
        human_click(country_selector)
        print("Clicked country selector")
        random_sleep(1.5, 2.5)

        # STEP 3: search for Korea
        search_input = find_first_visible(
            page,
            [
                "xpath=//div[contains(@class, 'select--search--20Pss08')]/input",
                "xpath=//div[contains(@class, 'select--search--')]/input",
            ],
            timeout_each_ms=15000,
        )
        if search_input is None:
            print("Could not find the country search input")
            screenshot(page, "country-search-not-found")
            return False
        search_input.scroll_into_view_if_needed()
        search_input.click()
        random_sleep(0.5, 1)

        print("STEP 3: typing 'Korea' in the country search...")
        type_like_human(page, search_input, "Korea")
        random_sleep(1, 2)

        # Check if any results were found, if not, try with Korean
        has_results = page.evaluate(
            """() => Array.from(document.querySelectorAll('div'))
                .some(el => el.textContent.includes('Korea') &&
                    (el.className.includes('item') || el.className.includes('option')))"""
        )
        if not has_results:
            print("No results with 'Korea', trying with Korean '대한민국'...")
            search_input.fill("")
            random_sleep(0.5, 1)
            type_like_human(page, search_input, "대한민국")
            random_sleep(1, 2)

        # STEP 4: click the Korea option
        print("STEP 4: looking for the Korea option in the dropdown...")
        korea_option = find_first_visible(
            page,
            [
                "xpath=//div[@class='select--item--32FADYB' and (contains(., 'Korea') or contains(., '대한민국'))]",
                "xpath=//div[contains(@class, 'select--item') and .//span[(contains(text(), 'Korea') or contains(text(), '대한민국'))]]",
            ],
            timeout_each_ms=8000,
        )
        if korea_option is None:
            print("Korea option not found by selector, trying JavaScript fallback...")
            handle = page.evaluate_handle(
                """() => Array.from(document.querySelectorAll('div'))
                    .find(el => (el.textContent.includes('Korea') || el.textContent.includes('대한민국')) &&
                                (el.className.includes('item') || el.className.includes('option')))"""
            )
            korea_option = handle.as_element() if handle else None
            if korea_option is None:
                print("Could not find the Korea option")
                screenshot(page, "korea-option-not-found")
                return False
            print("Found Korea option using JavaScript")

        korea_option.scroll_into_view_if_needed()
        random_sleep(0.5, 1)
        human_click(korea_option)
        print("Selected Korea")
        random_sleep(1.5, 2.5)

        # STEP 5: click Save
        save_button = find_first_visible(
            page,
            [
                "xpath=//div[contains(@class, 'es--saveBtn--w8EuBuy')]",
                "xpath=//div[contains(@class, 'es--saveBtn--')]",
            ],
            timeout_each_ms=15000,
        )
        if save_button is None:
            print("Could not find the save button")
            screenshot(page, "save-button-not-found")
            return False
        save_button.scroll_into_view_if_needed()
        random_sleep(0.5, 1)
        human_click(save_button)
        print("Clicked save button")
        random_sleep(3, 5)
        print("STEP 6: country saved (Korea). Continuing to the coin collection page...")
        return True

    except Exception as e:
        print(f"Country change failed: {e}")
        screenshot(page, "country-change-failed")
        return False


def verify_korea_selected(page, timeout_ms=10000):
    """Verify that Korea is currently selected as the ship-to country."""
    try:
        ship_to_element = page.locator("xpath=//div[contains(@class, 'ship-to--text--')]").first
        ship_to_element.wait_for(state="visible", timeout=timeout_ms)
        ship_to_text = ship_to_element.inner_text()
        print(f"Current ship-to text: {ship_to_text}")

        if "KO/" in ship_to_text:
            print("Found 'KO/' in ship-to text - Korea is definitely selected")
            return True
        if "Korea" in ship_to_text or "한국" in ship_to_text or "대한민국" in ship_to_text:
            print("Korea is selected as the country")
            return True
        print("Korea is NOT selected as the country")
        return False
    except Exception as e:
        print(f"Could not verify Korea selection: {e}")
        return False


def find_and_click_collect_button(page):
    """Find and click the coin collect button with multiple approaches."""
    print("STEP 7: looking for the Collect button...")

    collect_button_selectors = [
        "xpath=//div[contains(@class, 'checkin-button')]",
        "xpath=//div[contains(text(), 'Collect') and contains(@class, 'button')]",
        "xpath=//div[contains(text(), '출석체크') and contains(@class, 'button')]",
        "xpath=//div[contains(text(), '적립하기') and contains(@class, 'button')]",
        "xpath=//div[contains(text(), '체크인') and contains(@class, 'button')]",
        "xpath=//button[contains(@class, 'check-in') or contains(@class, 'checkin')]",
        "xpath=//div[contains(@class, 'coin') and contains(@class, 'collect')]",
    ]

    for selector in collect_button_selectors:
        try:
            button = page.locator(selector).first
            button.wait_for(state="visible", timeout=8000)
            print(f"Found the Collect button using selector: {selector}")
            button.scroll_into_view_if_needed()
            random_sleep(1, 2)
            human_click(button)
            print("Clicked collect button")
            random_sleep(5, 7)
            screenshot(page, "after-collect-click")
            return True
        except PlaywrightTimeoutError:
            continue
        except Exception as e:
            print(f"Couldn't find or click collect button with selector {selector}: {e}")
            continue

    # Fallback: look for any clickable element that might be the collect button
    try:
        print("Trying fallback approach - looking for any element that might be the collect button")
        handle = page.evaluate_handle(
            """() => Array.from(document.querySelectorAll('div, button, a'))
                .find(el => {
                    const text = (el.textContent || '').toLowerCase();
                    return (text.includes('collect') || text.includes('check') ||
                            text.includes('출석') || text.includes('적립') || text.includes('체크')) &&
                           ((el.className && el.className.toString().includes('button')) ||
                            el.tagName === 'BUTTON' || el.style.cursor === 'pointer');
                })"""
        )
        button = handle.as_element() if handle else None
        if button:
            print("Found a potential collect button using JavaScript")
            button.scroll_into_view_if_needed()
            random_sleep(1, 2)
            human_click(button)
            print("Clicked potential collect button")
            random_sleep(5, 7)
            screenshot(page, "after-fallback-click")
            return True
    except Exception as e:
        print(f"Fallback approach failed: {e}")

    print("Could not find any collect button despite multiple attempts")
    screenshot(page, "collect-not-found")
    print("*** WILL RESTART FROM STEP 1 (COUNTRY SELECTION) ***")
    return False


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_collection(p) -> bool:
    """Run one full collection cycle. Returns True if coins were collected."""
    browser = p.chromium.launch(headless=HEADLESS, args=BROWSER_ARGS)

    context_kwargs = {
        "viewport": {"width": 1366, "height": 768},
        "locale": "en-US",
    }
    if os.path.exists(STORAGE_STATE_PATH):
        print(f"Loading saved session from {STORAGE_STATE_PATH}")
        context_kwargs["storage_state"] = STORAGE_STATE_PATH
    context = browser.new_context(**context_kwargs)
    context.add_init_script(STEALTH_JS)

    page = context.new_page()
    page.set_default_timeout(30000)

    try:
        print(f"Opening coin page: {COIN_URL}")
        page.goto(COIN_URL, wait_until="domcontentloaded", timeout=60000)
        random_sleep(2, 4)

        # STEP A: login if needed
        if is_login_form_visible(page):
            if login(page):
                save_session(context)
            else:
                print("Login process failed, attempting to continue anyway...")
        else:
            print("Already logged in (saved session still valid)")

        # STEP B: main collection loop - restarts from Step 1 when needed
        for attempt in range(1, MAX_ATTEMPTS + 1):
            print(f"--- Collection attempt {attempt}/{MAX_ATTEMPTS} ---")

            if change_country_to_korea(page):
                # After saving country, the page reloads with Korean interface
                random_sleep(5, 7)

                # Navigate to the coin page
                print("Going to coin page after country change...")
                page.goto(COIN_URL, wait_until="domcontentloaded", timeout=60000)
                random_sleep(5, 7)

                verify_korea_selected(page)

                # STEP 7: look for the collect button
                if find_and_click_collect_button(page):
                    print("SUCCESS: coins collected!")
                    save_session(context)
                    return True
                print(f"Failed to find collect button on attempt {attempt}, restarting from Step 1")
            else:
                print(f"Country change failed on attempt {attempt}")

                # If we're on the last attempt, try the coin page anyway
                if attempt >= MAX_ATTEMPTS:
                    print("Maximum attempts reached. Trying coin page directly as last resort...")
                    page.goto(COIN_URL, wait_until="domcontentloaded", timeout=60000)
                    random_sleep(5, 7)
                    if find_and_click_collect_button(page):
                        print("SUCCESS: coins collected!")
                        save_session(context)
                        return True

        print("Maximum attempts reached without successful coin collection.")
        save_session(context)
        return False
    finally:
        context.close()
        browser.close()


def load_anchor():
    """Return (hour, minute) of the last collection run, or None.

    Persisted in ANCHOR_PATH so the schedule survives container restarts.
    """
    try:
        with open(ANCHOR_PATH) as f:
            data = json.load(f)
        return int(data["hour"]), int(data["minute"])
    except Exception:
        return None


def save_anchor(dt):
    """Persist the time of the collection that just started (the new anchor)."""
    try:
        d = os.path.dirname(ANCHOR_PATH)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(ANCHOR_PATH, "w") as f:
            json.dump(
                {"hour": dt.hour, "minute": dt.minute, "last_run": dt.isoformat(timespec="seconds")},
                f,
            )
        print(f"Schedule anchor saved: next collection ~24h from now, at {dt:%H:%M}")
    except Exception as e:
        print(f"Could not save schedule anchor: {e}")


def schedule_loop():
    """Stay alive: collect on start, then every 24h at the same time the
    previous collection was taken.

    The anchor (HH:MM of the previous run) is persisted in ANCHOR_PATH, so a
    container restart keeps the same daily time.
    """
    anchor = load_anchor()
    if anchor is None:
        print("No previous collection time found: running now, then daily at this time.")
    else:
        print(f"Previous collection time: {anchor[0]:02d}:{anchor[1]:02d} -> will repeat daily at this time.")

    jitter_seconds = int(os.getenv("JITTER_MINUTES", "0")) * 60

    while True:
        now = datetime.now()
        if anchor is None:
            target = now  # first start: run immediately
        else:
            target = now.replace(hour=anchor[0], minute=anchor[1], second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            if jitter_seconds > 0:
                target += timedelta(seconds=random.uniform(0, jitter_seconds))

        delay = max(0.0, (target - datetime.now()).total_seconds())
        print(f"Next collection in {delay / 3600:.2f} h (at {target:%Y-%m-%d %H:%M:%S})")
        if delay > 5:
            time.sleep(delay)

        run_started = datetime.now()
        print("=== Starting scheduled collection run ===")
        try:
            with sync_playwright() as p:
                run_collection(p)
        except Exception as e:
            print(f"Collection run failed: {e}")
        print("=== Scheduled run finished ===")

        # Anchor to this run's start time: next collection = same time + 24h
        anchor = (run_started.hour, run_started.minute)
        save_anchor(run_started)


def main():
    if not ALIEXPRESS_EMAIL or not ALIEXPRESS_PASSWORD:
        print("Error: Environment variables for ALIEXPRESS_EMAIL and ALIEXPRESS_PASSWORD must be set.")
        print("Please create a .env file (see .env.example) or set them in your environment.")
        sys.exit(1)

    run_on_schedule = os.getenv("RUN_ON_SCHEDULE", "0").strip().lower() in ("1", "true", "yes")

    try:
        if run_on_schedule:
            schedule_loop()
        else:
            with sync_playwright() as p:
                run_collection(p)
    except KeyboardInterrupt:
        print("Interrupted by user.")
        sys.exit(130)


if __name__ == "__main__":
    main()
