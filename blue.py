import re
import os
import time
import logging
import traceback
import pandas as pd

from datetime import datetime, date
from dateutil import parser

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import undetected_chromedriver as uc


# ============================================================
# CONFIG
# ============================================================
RUN_HEADLESS = False
OUTPUT_FILE = "output.csv"

PAGES = [
    ("https://www.blumenthalarts.org/events-tickets/category/broadway-at-blumenthal", "Musical"),
    ("https://www.blumenthalarts.org/events-tickets/category/theater", "Play")
]


# ============================================================
# LOGGING
# ============================================================
if not os.path.exists("log"):
    os.makedirs("log")

logging.basicConfig(
    filename="log/scrape.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


def log(msg, level="info"):
    print(f"[LOG] {msg}")

    if level == "error":
        logging.error(msg)
    elif level == "warning":
        logging.warning(msg)
    else:
        logging.info(msg)


# ============================================================
# BROWSER
# ============================================================
def setup_browser():
    log("🚀 Starting browser...")
    options = uc.ChromeOptions()

    if RUN_HEADLESS:
        options.add_argument("--headless=new")

    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = uc.Chrome(options=options, version_main=147)
    log("✅ Browser ready")
    return driver


# ============================================================
# SAFE GET
# ============================================================
def safe_get(driver, url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            log(f"🌍 Loading page ({attempt}/{retries}): {url}")
            driver.get(url)
            log("✅ Page loaded")
            return True
        except Exception as e:
            log(f"❌ Load failed: {e}", "error")
            time.sleep(2)
    return False


def detect_captcha(driver):

    try:

        body_text = driver.find_element(By.TAG_NAME, "body").text.lower()

        indicators = [
            "prove you are human",
            "verify you are human",
            "captcha",
            "checking your browser",
            "cloudflare",
            "attention required"
        ]

        if any(x in body_text for x in indicators):
            return True

        frames = driver.find_elements(
            By.CSS_SELECTOR,
            "iframe[src*='captcha'], "
            "iframe[src*='recaptcha'], "
            "iframe[src*='hcaptcha']"
        )

        if frames:
            return True

        return False

    except Exception:
        return False

def wait_for_captcha_to_clear(driver, timeout=300):
    log("⏳ Waiting for CAPTCHA to be solved...")
    start = time.time()

    while time.time() - start < timeout:
        if not detect_captcha(driver):
            log("✅ CAPTCHA cleared")
            return True
        time.sleep(2)

    log("❌ CAPTCHA timeout", "error")
    return False



# ============================================================
# SCROLL
# ============================================================
def scroll_to_load_all(driver):
    log("⬇️ Scrolling page...")
    last_height = driver.execute_script("return document.body.scrollHeight")

    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)

        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

    log("✅ Finished scrolling")


# ============================================================
# DATE PARSER
# ============================================================
def parse_date(text):

    try:
        dt = parser.parse(text, dayfirst=True, fuzzy=True)
        if dt.date() < date.today():
            dt = dt.replace(year=dt.year + 1)
        return dt
    except Exception:
        return None

# ============================================================
# CLEAN CURRENCY TEXT
# ============================================================
def detect_currency(text):
    if not text: return None
    if "£" in text: return "GBP"
    elif "$" in text: return "USD"
    elif "€" in text: return "EUR"
    return None


#============================================================
# BUSINESS LOGIC & DATA EXTRACTION FUNCTIONS
# ============================================================
def get_theatre_details(theatre_name: str) -> dict:
    """
    Returns structured localization data dynamically mapped to specific Blumenthal Arts venues.
    """
    normalized_name = theatre_name.lower().strip() if theatre_name else ""

    # Definitive Blumenthal Arts physical venues mapping registry
    theatre_map = {
        "belk theater": {"address": "130 N Tryon St", "city": "Charlotte", "country": "USA"},
        "booth playhouse": {"address": "130 N Tryon St", "city": "Charlotte", "country": "USA"},
        "knight theater": {"address": "550 South Tryon Street", "city": "Charlotte", "country": "USA"},
        "stage door theater": {"address": "130 N Tryon St", "city": "Charlotte", "country": "USA"}
    }

    # Fallback substring evaluation matcher to protect against appended corporate strings
    for key, data in theatre_map.items():
        if key in normalized_name:
            return data
    # Universal location context backup
    return {"address": "130 N Tryon St", "city": "Charlotte", "country": "USA"}

# ============================================================
# EVENTS LIST EXTRACTION
# ============================================================
def extract_events(driver, category):
    log(f"🎭 Extracting events for category: {category}")

    cards = driver.find_elements(By.CSS_SELECTOR, "div.eventItem, div.event-list-item, article.event")
    if not cards:
         cards = driver.find_elements(By.CSS_SELECTOR, ".event")

    log(f"📦 Found {len(cards)} event cards")

    events = []
    seen_titles = set()
    for i, card in enumerate(cards, start=1):
        try:
            title_el = card.find_element(By.CSS_SELECTOR, "h3.title a, h2.title a, .event-title a, a.event-link")
            title = title_el.get_attribute("textContent").strip()
            venue_url = title_el.get_attribute("href")

            try:
              venue = card.find_element(By.CSS_SELECTOR, "div.event_venue, .venue, .eventVenue").get_attribute("textContent").strip()
            except:
              venue = "Blumenthal Performing Arts"

            log(f" ➤ [{i}/{len(cards)}] {title}")

            # Skip duplicate titles
            if title.lower() in seen_titles:
                log(f"🔁 Duplicate title skipped: {title}")
                continue
            seen_titles.add(title.lower())

            if not venue_url or not venue_url.startswith("http"):
                continue

            events.append({
                "title": title,
                "venue_url": venue_url,
                "category": category,
                "venue": venue
            })
        except Exception as e:
            log(f"⚠️ Event list item parse error at block index {i}: {e}", "warning")

    log(f"✅ Total base events extracted: {len(events)}")
    return events

# ============================================================
# PERFORMANCE DATES HELPER FUNCTIONS
# ============================================================
def _parse_performance_datetime(block, current_year):
    """Helper to parse date and time from a performance block element."""
    try:
        month = block.find_element(By.CSS_SELECTOR, ".m-date__month, .month").text.strip()
        day = block.find_element(By.CSS_SELECTOR, ".m-date__day, .day").text.strip()
        time_text = block.find_element(By.CSS_SELECTOR, "span.time.cell, .time").text.strip()
        date_string = f"{month} {day} {current_year} {time_text}"
        return parse_date(date_string)
    except:
        # Alternative layout raw text fallback
        raw_text = block.text.strip()
        return parse_date(raw_text)


# ============================================================
# PERFORMANCE DATES EXTRACTION
# ============================================================
def extract_events_performance_dates(driver):
    """Extract event performance dates, times, and booking link with year and venue info."""

    log("🎭 Extracting individual performances and site metadata...")
    performances = []

    try:
        # 1. Extract Global Metadata (Year and Venue URL)
        try:
            year = driver.find_element(By.CSS_SELECTOR, ".event_heading .m-date__year").text.replace(",", "").strip()
        except:
            year = str(datetime.now().year) # Fallback to current year

        try:
            venue_url = driver.find_element(By.CSS_SELECTOR, ".event_venue a").get_attribute("href")
        except:
            venue_url = None

        blocks = driver.find_elements(By.CSS_SELECTOR, "div.event_showings li.listItem, .performance-list li, .showing-item")
        if not blocks:
            blocks = driver.find_elements(By.CSS_SELECTOR, ".performances .item")

        log(f"📦 Found {len(blocks)} performance rows")
        current_year = datetime.now().year

        for idx, block in enumerate(blocks, start=1):
            try:
                # 1. Parse datetime
                parsed_dt = _parse_performance_datetime(block, current_year)
                date_ymd = parsed_dt.strftime("%Y-%m-%d")
                time_hm = parsed_dt.strftime("%H:%M")

                # FIX: Corrected button selector matching target structure
                get_ticket_btn = block.find_element(
                    By.CSS_SELECTOR, "a.tickets, a.button").get_attribute("href")

                performances.append({
                    "date": date_ymd,
                    "time": time_hm,
                    "get_ticket_btn": get_ticket_btn
                })

            except Exception as e:
                log(f"⚠️ Single performance parse failed on block index {idx}: {e}", "warning")

    except Exception as e:
        log(f"❌ Structural performance extraction level error: {e}", "warning")

    return performances

# ============================================================
# SVG SEATMAP SCRAPER
# ============================================================
def extract_all_seats(driver):
  """Extracts seats and pricing from the currently open SVG modal."""

  log("💺 Extracting seats from all seat map sections...")

  all_seats = {}
  click_count = 0
  section_click_count = 0
  currency = None

  while True:

    try:
      # ------------------------------------------------
      # WAIT FOR SEAT MAP
      # ------------------------------------------------
      WebDriverWait(driver, 10).until(EC.presence_of_element_located(
          (By.CSS_SELECTOR, "circle[data-seat-row], g#screenMap polygon.picker")))
      time.sleep(2)

      # =================================================
      # 1. HANDLE SVG SECTION SELECTION (NEW ADDITION)
      # =================================================
      sections = driver.find_elements(By.CSS_SELECTOR, "g#screenMap polygon.picker")
      if sections:
        log(f"🧭 Found {len(sections)} seat sections")

        for sec in sections:
            aria = sec.get_attribute("aria-label") or ""

            if sec.is_displayed():
                driver.execute_script("""
                var element = arguments[0];
                var evt = document.createEvent("MouseEvents");
                evt.initMouseEvent("click", true, true, window, 0, 0, 0, 0, 0, false, false, false, false, 0, null);
                element.dispatchEvent(evt);
                """, sec)
                section_click_count += 1

                log(f"🎭 Clicked section " f"({section_click_count}): {aria}")
                time.sleep(2)
                break
                                                                
      # Collect currently visible seats
      seats = driver.find_elements(By.CSS_SELECTOR, "circle[data-seat-row][data-seat-seat]")
      log(f"📦 Found {len(seats)} seats")

      # Extract seats
      for seat in seats:
        row_name = seat.get_attribute("data-seat-row")
        seat_no = seat.get_attribute("data-seat-seat")
        section = seat.get_attribute("data-seat-section")
        zone = seat.get_attribute("data-sectiondescription")
        aria = (seat.get_attribute("aria-label") or "")

        if not currency:
            currency = detect_currency(aria)

        match = re.search(r"\$([\d]+(?:\.\d+)?)", aria)
        if not match:
            continue
            
        price = float(match.group(1))

        if price is None:
            continue

        seat_id = f"{section} {row_name}{seat_no}".strip()
        # deduplicate
        all_seats[seat_id] = {
            "seat": seat_id,
            "ticket_price": price
        }

      # -----------------------------------
      # CLICK NEXT SECTION ARROW
      # -----------------------------------
      try:
        seatmap_arrow = driver.find_element(By.CSS_SELECTOR, "div.map-container button.bottom-arrow")
        if not seatmap_arrow.is_displayed():
          break

        driver.execute_script("arguments[0].click();", seatmap_arrow)
        click_count += 1

        log(f"⬇️ Clicked seat map arrow " f"({click_count})")
        time.sleep(2)

      except Exception as e:
        log("✅ Reached final seat map section")
        break

    except Exception as e:
        log(f"⚠️ Seat extraction failed: {e}", "warning")
        break

  seat_list = list(all_seats.values())
  capacity= len(seat_list)
  log(f"🎟 Total unique seats extracted: " f"{capacity}")

  return seat_list, currency, capacity

# ============================================================
# SEAT PRICING
# ============================================================
def _extract_seat_pricing_metrics(driver, performances):
    """Extract seat pricing from SVG seat map"""

    log("💺 Extracting seat pricing...")
    seat_pricing = {}

    # Save the original event details window handle
    main_window = driver.current_window_handle

    for perf in performances:
        try:
            start = time.time()

            # -----------------------------------
            # OPEN GET TICKETS PAGE
            # -----------------------------------
            # Clicking the button or loading the link triggers a new tab
            driver.get(perf["get_ticket_btn"])
            time.sleep(1.5)  # Give the browser a moment to register the new handle

            # -----------------------------------
            # GET TICKETS OPENS NEW TAB
            # -----------------------------------
            if len(driver.window_handles) > 1:

                new_tab = [
                    h for h in driver.window_handles
                    if h != main_window][0]

                driver.switch_to.window(new_tab)

            # ------------------------------------------------
            # CAPTCHA CHECK
            # ------------------------------------------------
            if detect_captcha(driver):
                log("⚠️ CAPTCHA DETECTED")
                input(
                    "\nSolve the captcha manually "
                    "then press ENTER..."
                )
                time.sleep(1.5)

            log("✅ Page loaded")

            # -----------------------------------
            # WAIT FOR BUY PAGE
            # -----------------------------------
            WebDriverWait(driver, 10).until(EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "div.result-box-item")))

            rows = driver.find_elements(By.CSS_SELECTOR, "div.result-box-item")
            target_row = None

            for row in rows:
                try:
                    availability = row.find_element(By.CSS_SELECTOR, ".availability-text").text.strip()
                    if "Sold Out" in availability:
                        continue

                    target_row = row
                    break

                except:
                    continue

            if not target_row:
                log("⚠️ No available performance found")
                continue

            # -----------------------------------
            # CLICK BUY
            # -----------------------------------
            buy_button  = target_row.find_element(By.CSS_SELECTOR, "a.btn.btn-primary, #popupDivOpen")
            driver.execute_script("arguments[0].click();", buy_button )

            # ------------------------------------------------
            # WAIT FOR SEAT MAP
            # ------------------------------------------------
            seat_list, currency, capacity = extract_all_seats(driver)

            perf_key = f"{perf['date']} {perf['time']}"
            seat_pricing[perf_key] = seat_list
            perf["capacity"] = capacity
            perf["currency"] = currency


            log(
                f"✅ Seats: {capacity} | "
                f"Time: {round(time.time()-start,2)}s"
            )

            # -----------------------------------
            # CLOSE BUY TAB
            # -----------------------------------
            if driver.current_window_handle != main_window:
                driver.close()
                driver.switch_to.window(main_window)

        except Exception as e:
            log(f"⚠️ seat extraction error: {e}", "warning")

            try:
                if driver.current_window_handle != main_window:
                    driver.close()
                    driver.switch_to.window(main_window)
            except:
                pass

            continue

    return seat_pricing

# ============================================================
# MAIN APPLICATION FLOW
# ============================================================
def scrape_shows():
    log("🚀 SCRAPER STARTED")

    driver = setup_browser()
    all_rows = []

    for page_idx, (url, category) in enumerate(PAGES, start=1):
        log(f"\n🌍 CATEGORY CORRELATION {page_idx}/{len(PAGES)} → {category}")

        if not safe_get(driver, url):
            continue

        scroll_to_load_all(driver)
        events = extract_events(driver, category)

        for i, e in enumerate(events[2:4], start=1):
            log(f"\n🎭 EVENT SPECIFIC EXTRACTION {i}/{len(events)} → {e['title']}")

            if not safe_get(driver, e["venue_url"]):
                continue


            scroll_to_load_all(driver)
            performances = extract_events_performance_dates(driver)
            seat_pricing = _extract_seat_pricing_metrics(driver, performances)
            capacity = max([p.get("capacity", 0) for p in performances], default=0)

            # Find the first performance that successfully extracted a currency string, fallback to None
            currency = next((p.get("currency") for p in performances if p.get("currency")), None)

            if performances:
                sorted_dates = sorted([p["date"] for p in performances])
                open_date = sorted_dates[0]
                close_date = sorted_dates[-1]
            else:
                open_date = None
                close_date = None

            formatted_performances = repr([
                {"date": p["date"], "time": p["time"]} for p in performances
            ])
            formatted_seat_pricing = repr(seat_pricing) if seat_pricing else "{}"

            theatre_details = get_theatre_details(e["venue"])

            row = {
                "title": e["title"],
                "venue_url": e["venue_url"],
                "category": e["category"],
                "venue": e["venue"] if e["venue"] else "Blumenthal Performing Arts",
                "address": theatre_details["address"],
                "city": theatre_details["city"],
                "country": theatre_details["country"],
                "open_date": open_date,
                "close_date": close_date,
                "booking_start_date": open_date,
                "booking_end_date": close_date,
                "upcoming_performances": formatted_performances,
                "capacity": capacity,
                "currency": currency,
                "is_limited_run": "True" if close_date else "False",
                "seat_pricing": formatted_seat_pricing,
                "scrape_datetime": datetime.now().strftime("%Y-%m-%d %H:%M")
            }

            all_rows.append(row)
            log(f"✅ Extracted Row Record Saved: {e['title']}")

    # ============================================================
    # BUILD CSV IN STRICT CANONICAL ORDER
    # ============================================================
    canonical_columns = [
        "title", "venue_url", "category", "venue", "address", "city", "country",
        "open_date", "close_date", "booking_start_date", "booking_end_date",
        "upcoming_performances", "capacity", "currency", "is_limited_run",
        "seat_pricing", "scrape_datetime"
    ]

    if all_rows:
        df = pd.DataFrame(all_rows)
        df = df.reindex(columns=canonical_columns)
    else:
        df = pd.DataFrame(columns=canonical_columns)

    df.to_csv(OUTPUT_FILE, index=False)
    log("🎉 SCRAPING PROCESS SUCCESSFULLY COMPLETED")

    driver.quit()


if __name__ == "__main__":
    scrape_shows()