
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

    driver = uc.Chrome(options=options, version_main=148)
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
# SOLVE CAPTCHA
# ============================================================
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
            event_url = title_el.get_attribute("href")

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

            if not event_url or not event_url.startswith("http"):
                continue

            events.append({
                "title": title,
                "event_url": event_url,
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
def _parse_performance_datetime(block, year):
    """Helper to parse date and time from a performance block element."""
    try:
        month = block.find_element(By.CSS_SELECTOR, ".m-date__month, .month").text.strip()
        day = block.find_element(By.CSS_SELECTOR, ".m-date__day, .day").text.strip()
        time_text = block.find_element(By.CSS_SELECTOR, "span.time.cell, .time").text.strip()
        date_string = f"{month} {day} {year} {time_text}"
        return parse_date(date_string)
    except:
        # Alternative layout raw text fallback
        raw_text = block.text.strip()
        return parse_date(raw_text)


# ============================================================
# PERFORMANCE DATES EXTRACTION
# ============================================================
from selenium.webdriver.common.by import By
from datetime import datetime

def extract_events_performance_dates(driver):
    """Extract event performance dates, times, and booking link with year and venue info."""
    log("🎭 Extracting individual performances and site metadata...")
    performances = []

    # Save the original event details window handle
    main_window = driver.current_window_handle

    try:
        # 1. Extract Global Metadata (Year and Venue URL)
        # Using CSS Selectors based on your provided HTML
        try:
            page_get_ticket_btn = block.find_element(By.CSS_SELECTOR, "div.buttons a").get_attribute("href")
            time.sleep(1.5)
        except:
            log(f" This show is not on sale at the moment")

        #-----------------------------------
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

        date_blocks = driver.find_elements(By.CSS_SELECTOR, "div.result-box-item")
        
        for row in date_blocks:
            try:
                # Get row date/time
                dt_text = row.find_element(By.CSS_SELECTOR, ".start-date").text.strip()
                row_dt = parser.parse(dt_text)

                row_date = row_dt.strftime("%Y-%m-%d")
                row_time = row_dt.strftime("%H:%M")

                # skip sold out
                availability = row.find_element(By.CSS_SELECTOR, ".availability-text").text.strip()
                sold_out = "sold_out" if sold_out in availability
                buy_button  = row.find_element(By.CSS_SELECTOR, "a.btn.btn-primary, #popupDivOpen")
                
                #if "Sold Out" in availability:
                    #continue

                performances.append({
                    "date": row_date,
                    "time": row_time,
                    "sold_out": sold_out if "Sold Out" else None,
                    "buy_link": buy_button if availability != "sold_out" else None
                })

                # -----------------------------------
                # CLOSE BUY TAB
                # -----------------------------------
                if driver.current_window_handle != main_window:
                    driver.close()
                    driver.switch_to.window(main_window)
            
            except Exception as e:
                log(f"⚠️ Single performance parse failed on block index {idx}: {e}", "warning")

    except Exception as e:
        log(f"❌ Structural performance extraction error: {e}", "warning")

        try:
            if driver.current_window_handle != main_window:
                driver.close()
                driver.switch_to.window(main_window)
        except:
            pass

            continue

    return performances
    

# ============================================================
# SVG SEATMAP SCRAPER
# ============================================================
def extract_all_seats(driver):
    """Extracts seats and pricing from the currently open SVG modal without looping infinitely."""

    log("💺 Extracting seats from all seat map sections...")

    all_seats = {}
    seen_snapshots = set()  #  Track unique seat layouts to prevent loops
    click_count = 0
    section_click_count = 0
    currency = None

    while True:
        try:
            # ------------------------------------------------
            # WAIT FOR SEAT MAP TO SETTLE
            # ------------------------------------------------
            WebDriverWait(driver, 10).until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "circle[data-seat-row], g#screenMap polygon.picker")))
            time.sleep(2)

            # =================================================
            # 1. HANDLE SVG SECTION SELECTION 
            # =================================================
            sections = driver.find_elements(By.CSS_SELECTOR, "g#screenMap polygon.picker")
            if sections:
                log(f"🧭 Found {len(sections)} seat sections")

                for sec in sections:
                    aria = sec.get_attribute("aria-label") or ""

                    if sec.is_displayed():
                        # Click the section to switch views
                        driver.execute_script("""
                        var element = arguments[0];
                        var evt = document.createEvent("MouseEvents");
                        evt.initMouseEvent("click", true, true, window, 0, 0, 0, 0, 0, false, false, false, false, 0, null);
                        element.dispatchEvent(evt);
                        """, sec)
                        section_click_count += 1

                        log(f"🎭 Clicked section ({section_click_count}): {aria}")
                        time.sleep(2)  # Give the DOM 2 seconds to load the new seats
                        break  # Break out of the sections loop to parse the newly loaded elements
                                                                        
            # =================================================
            # 2. COLLECT AND VALIDATE FRESH SEATS (Correct Sequence Placement)
            # =================================================
            seats = driver.find_elements(By.CSS_SELECTOR, "circle[data-seat-row][data-seat-seat]")
            
            # Create a unique fingerprint string of current rows and seat numbers
            seat_fingerprint = "|".join(sorted([
                (s.get_attribute("data-seat-row") or "") + (s.get_attribute("data-seat-seat") or "") 
                for s in seats
            ]))

            #  INFINITE LOOP PROTECTION: Stop if this view has already been scraped
            if seat_fingerprint in seen_snapshots:
                log("🔄 Duplicate state detected. Reached the end of sections.")
                break
                
            seen_snapshots.add(seat_fingerprint)
            log(f"📦 Found {len(seats)} unique seats in this section")

            # =================================================
            # 3. EXTRACT SEAT DATA
            # =================================================
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

                seat_id = f"{section} {row_name}{seat_no}".strip()
                # Deduplicate records by seat ID
                all_seats[seat_id] = {
                    "seat": seat_id,
                    "ticket_price": price
                }

            # -----------------------------------
            # 4. CLICK NEXT SECTION ARROW
            # -----------------------------------
            try:
                seatmap_arrow = driver.find_element(By.CSS_SELECTOR, "div.map-container button.bottom-arrow")
                
                # Enhanced exit condition: Stop if hidden OR explicitly disabled via CSS class
                if not seatmap_arrow.is_displayed() or "disabled" in (seatmap_arrow.get_attribute("class") or ""):
                    log("✅ Arrow button is hidden or disabled. Map processing complete.")
                    break

                driver.execute_script("arguments[0].click();", seatmap_arrow)
                click_count += 1

                log(f"⬇️ Clicked seat map arrow ({click_count})")
                time.sleep(2)  # Wait for page slide transition

            except Exception as e:
                log("✅ Reached final seat map section (Arrow element missing)")
                break

        except Exception as e:
            log(f"⚠️ Seat extraction failed: {e}", "warning")
            break

    seat_list = list(all_seats.values())
    capacity = len(seat_list)
    log(f"🎟 Total unique seats extracted: {capacity}")

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
        perf_key = f"{perf['date']} {perf['time']}"

        try:
            start = time.time()
            
            try:
                # Click the buy button 
                driver.get(perf["buy_link"])
                time.sleep(1.5)  # Give the browser a moment to register the new handle
            except:
                log(f" This show is not on sale at the moment for {perf_key}")
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
        except Exception as e:
            log(f"⚠️ seat extraction error: {e}", "warning")
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

        for i, e in enumerate(events[-6:], start=1):
            log(f"\n🎭 EVENT SPECIFIC EXTRACTION {i}/{len(events)} → {e['title']}")

            if not safe_get(driver, e["event_url"]):
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
                "venue_url": e["event_url"],
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
           
