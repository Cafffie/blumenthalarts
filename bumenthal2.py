"""Blumenthal Arts extractor implementation using the framework.

Listing:   SeleniumBase UC Mode (scrapes target category pages)
Detail:    SeleniumBase UC Mode (navigates to individual show info)
Seat map:  SVG Map extraction inside individual show ticket pages.
"""
import json
import re
import sys
from datetime import date, datetime

import pandas as pd
from dateutil import parser
from selenium.webdriver.common.by import By

from utils.base_extractor import BaseExtractor
from utils.logger import setup_logger
from utils.scraping_helpers import (
    accept_cookies,
    get_currency_from_price,
    get_scrape_datetime,
    human_delay,
    standardize_category,
)

from .blumenthal_arts_config import (
    DEFAULT_THEATRE_DETAILS,
    MAX_RETRIES,
    PAGES,
    RETRY_DELAY
    QUEUE_COOKIES,
    RUN_HEADLESS,
    THEATRE_DETAILS_MAP,
)

logger = setup_logger(__name__, log_to_file=False)


class BlumenthalArtsExtractor(BaseExtractor):
    """Extractor for Blumenthal Arts website using SeleniumBase."""

    def __init__(self, local_test=False, show_count=2, **kwargs):
        super().__init__(
            site_id="blumenthal_arts",
            log_to_file=False,
            log_to_terminal=True,
            local_test=local_test,
            show_count=show_count,
            **kwargs,
        )
        self.all_data = []

    # ------------------------------------------------------------------ #
    # BaseExtractor Lifecycle Interface                                  #
    # ------------------------------------------------------------------ #

    def extract(self) -> bytes:
        """Core extraction loop utilizing SeleniumBase SB context manager.

        Phase 1: collect all event listings from category pages (single browser session).
        Phase 2: extract detail + seat pricing per show (fresh browser per show so a
                 ChromeDriver crash in one show cannot propagate to subsequent shows).
        """
        self.all_data = []

        from seleniumbase import SB

        self.custom_logger.info(" Starting SeleniumBase Browser Context...")

        # ------------------------------------------------------------------
        # Phase 1 — collect event listings from all category pages
        # ------------------------------------------------------------------
        all_events = []
        with SB(uc=True, headless=RUN_HEADLESS, rtf=True) as sb:
            for page_idx, (url, category) in enumerate(PAGES, start=1):
                self.custom_logger.info(
                    f"\n CATEGORY CORRELATION {page_idx}/{len(PAGES)} → {category}"
                )
                sb.sleep(2)
                if not self._safe_get(sb, url):
                    continue

                self._scroll_to_load_all(sb)
                events = self._extract_events(sb, category)

                if self.local_test and self.show_count:
                    events = events[: self.show_count]
                    self.custom_logger.info(
                        f"Capped category items to {self.show_count} for test run."
                    )

                all_events.extend(events)

        # ------------------------------------------------------------------
        # Deduplicate events by venue_url before running Phase 2
        # ------------------------------------------------------------------
        seen_urls = set()
        unique_events = []
        for e in all_events:
            if e["venue_url"] not in seen_urls:
                seen_urls.add(e["venue_url"])
                unique_events.append(e)
        all_events = unique_events

        # ------------------------------------------------------------------
        # Phase 2 — per-show detail extraction (one fresh browser per show)
        # ------------------------------------------------------------------
        total = len(all_events)
        for i, e in enumerate(all_events, start=1):
            self.custom_logger.info(
                f"\n EVENT SPECIFIC EXTRACTION {i}/{total} → {e['title']}"
            )
            try:
                with SB(uc=True, headless=RUN_HEADLESS, rtf=True) as sb:
                    if not self._safe_get(sb, e["venue_url"]):
                        continue

                    self._scroll_to_load_all(sb)
                    performances = self._extract_events_performance_dates(sb)

                    seat_pricing = self._extract_seat_pricing_metrics(sb, performances)

                    if seat_pricing:
                        capacity = (
                            max(
                                [p.get("capacity", 0) for p in performances],
                                default=None,
                            )
                            
                        )
                        currency = next(
                            (
                                p.get("currency")
                                for p in performances
                                if p.get("currency")
                            ),
                            "USD",
                        )
                    else:
                        capacity = None
                        currency = None

                    if performances:
                        sorted_dates = sorted([p["date"] for p in performances])
                        open_date = sorted_dates[0]
                        close_date = sorted_dates[-1]
                    else:
                        open_date = datetime.now().strftime("%Y-%m-%d")
                        close_date = datetime.now().strftime("%Y-%m-%d")

                    formatted_performances = [
                        {"date": p["date"], "time": p["time"]} for p in performances
                    ]

                    theatre_details = self._get_theatre_details(e["venue"])

                    row = {
                        "title": e["title"],
                        "venue_url": e["venue_url"],
                        "category": standardize_category(e["category"]),
                        "venue": e["venue"]
                        if e["venue"]
                        else "Blumenthal Performing Arts",
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
                        "seat_pricing": seat_pricing,
                        "scrape_datetime": get_scrape_datetime(),
                    }

                    self.all_data.append(row)
                    self.log_record(row)
                    self.custom_logger.info(
                        f" Extracted Row Record Saved: {e['title']}"
                    )
            except Exception as exc:
                self.custom_logger.error(
                    "Show extraction failed for %s: %s", e["title"], exc
                )

        return json.dumps(self.all_data, default=str).encode("utf-8")

    def _parse(self, _raw: bytes) -> pd.DataFrame:
        """Converts raw structured dictionary values into standard pandas DataFrame."""
        df = pd.DataFrame(self.all_data)
        if "capacity" in df.columns:
            df["capacity"] = pd.to_numeric(df["capacity"], errors="coerce")
        self.custom_logger.info(
            "Parsing completed. Generated collection rows: %s", len(df)
        )
        return df

    # ------------------------------------------------------------------ #
    # Browser Safe Navigation Helpers                                    #
    # ------------------------------------------------------------------ #

    def _scroll_to_load_all(self, sb):
        """Standard automated scrolling interface inside the framework canvas."""
        last_height = sb.execute_script("return document.body.scrollHeight")
        while True:
            sb.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            human_delay(1.5, 2.5)
            new_height = sb.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

    def _solve_captcha(self, sb) -> None:
        """Use SeleniumBase UC helper when a bot protection page is present."""
        try:
            self.custom_logger.info("Attempting Cloudflare captcha solver...")
            sb.uc_gui_click_captcha()
            human_delay(2.0, 4.0)
        except Exception as e:
            self.custom_logger.warning("Captcha handler attempt failed: %s", e)

    def _is_bot_protection(self, sb) -> bool:
        url = sb.get_current_url().lower()
        source = sb.get_page_source().lower()
        # Only flag genuine challenge pages, not pages that merely use Cloudflare as CDN
        return (
            "captcha" in url
            or "challenge" in url
            or "cf-spinner" in source
            or "checking your browser" in source
            or "enable javascript and cookies" in source
            or "distil_identify_cookie" in source
        )

    def _safe_get(self, sb, url, wait=10) -> bool:
        """Safely navigates using Undetected-Chromatography reconnect checks."""
        try:
            self.custom_logger.info("Loading URL: %s", url)
            sb.uc_open_with_reconnect(url, reconnect_time=wait if wait > 4 else 4)

            if self._is_bot_protection(sb):
                self.custom_logger.warning("Bot protection detected. Solving...")
                self._solve_captcha(sb)
                sb.uc_open_with_reconnect(url, reconnect_time=wait if wait > 4 else 4)
                if self._is_bot_protection(sb):
                    self.custom_logger.error(
                        "Bot protection still present after captcha handler"
                    )
                    return False

            return True
        except Exception as e:
            self.custom_logger.error(f"Failed to load page: {url} | Exception: {e}")
            return False

    # ------------------------------------------------------------------ #
    # Parsers & Localizers                                               #
    # ------------------------------------------------------------------ #

    def _parse_date(self, text: str) -> date | None:
        try:
            dt = parser.parse(text, dayfirst=True, fuzzy=True)
            if dt.date() < date.today():
                dt = dt.replace(year=dt.year + 1)
            return dt
        except Exception:
            return None

    def _safe_navigate_to_ticket_page(self, sb, url):
        """Standardized navigation with cookies and banners."""
        # 1. Initial load
        sb.uc_open_with_reconnect(url, reconnect_time=6)

        # 2. Proactive Cookie Injection
        for cookie in QUEUE_COOKIES:
            try:
                # Ensure the cookie domain matches the current URL
                if cookie["domain"] in sb.get_current_url():
                    sb.add_cookie(cookie)
            except Exception as e:
                self.custom_logger.debug(f"Failed to add cookie {cookie['name']}: {e}")

        # 3. Refresh to apply cookies and trigger any JS handlers
        sb.refresh()
        human_delay(1.5, 2.5)

        # 4. Clear UI blockers
        accept_cookies(sb.driver, logger=self.custom_logger)

        # 5. Final check for bot protection
        if self._is_bot_protection(sb):
            self.custom_logger.warning(
                "Bot protection detected on ticket page. Solving..."
            )
            self._solve_captcha(sb)
            sb.refresh()
            human_delay(2.0, 3.0)

    def _get_theatre_details(self, theatre_name: str) -> dict:
        normalized_name = theatre_name.lower().strip() if theatre_name else ""

        for key, data in THEATRE_DETAILS_MAP.items():
            if key in normalized_name:
                return data
        return DEFAULT_THEATRE_DETAILS

    # ============================================================
    # EVENTS LIST EXTRACTION
    # ============================================================

    def _extract_events(self, sb, category: str) -> list:
        self.custom_logger.info(f" Extracting events for category: {category}")

        cards = sb.find_elements("div.eventItem, div.event-list-item, article.event")
        if not cards:
            cards = sb.find_elements(".event")
        self.custom_logger.info(f" Found {len(cards)} event cards")

        events = []
        seen_titles = set()
        for i, card in enumerate(cards, start=1):
            try:
                title_el = card.find_element(
                    By.CSS_SELECTOR,
                    "h3.title a, h2.title a, .event-title a, a.event-link",
                )
                title = title_el.get_attribute("textContent").strip()
                venue_url = title_el.get_attribute("href")

                try:
                    venue = (
                        card.find_element(
                            By.CSS_SELECTOR, "div.event_venue, .venue, .eventVenue"
                        )
                        .get_attribute("textContent")
                        .strip()
                    )
                except Exception:
                    venue = "Blumenthal Performing Arts"

                self.custom_logger.info(f"  [{i}/{len(cards)}] {title}")

                if title.lower() in seen_titles:
                    continue
                seen_titles.add(title.lower())

                if not venue_url or not venue_url.startswith("http"):
                    continue

                events.append(
                    {
                        "title": title,
                        "venue_url": venue_url,
                        "category": category,
                        "venue": venue,
                    }
                )
            except Exception as e:
                self.custom_logger.debug(
                    f"⚠️ Event list item parse error at block index {i}: {e}"
                )

        self.custom_logger.debug(f" Total base events extracted: {len(events)}")
        return events

    # ============================================================
    # PERFORMANCE DATES EXTRACTION
    # ============================================================
    def _extract_events_performance_dates(self, sb) -> list:
        performances = []
        try:

            blocks = sb.find_elements("div.event_showings li.listItem")
            self.custom_logger.info(f" Found {len(blocks)} performance rows ")

            for idx, block in enumerate(blocks, start=1):
                try:
                    try:
                        month = (
                            block.find_element(
                                By.CSS_SELECTOR, ".m-date__month, .month"
                            )
                            .get_attribute("textContent")
                            .strip()
                        )
                        day = (
                            block.find_element(By.CSS_SELECTOR, ".m-date__day, .day")
                            .get_attribute("textContent")
                            .strip()
                        )
                        time_text = (
                            block.find_element(By.CSS_SELECTOR, "span.time.cell, .time")
                            .get_attribute("textContent")
                            .strip()
                        )
                        year = str(datetime.now().year)
                        date_string = f"{month} {day} {year} {time_text}"
                        parsed_dt = self._parse_date(date_string)
                    except Exception:
                        continue

                    if not parsed_dt:
                        continue

                    date_ymd = parsed_dt.strftime("%Y-%m-%d")
                    time_hm = parsed_dt.strftime("%H:%M")
                    get_ticket_btn = block.find_element(
                        By.CSS_SELECTOR, "a.tickets"
                    ).get_attribute("href")

                    performances.append(
                        {
                            "date": date_ymd,
                            "time": time_hm,
                            "get_ticket_btn": get_ticket_btn,
                            "year": year,
                        }
                    )
                except Exception as e:
                    self.custom_logger.debug(
                        f"Single performance parse failed on index {idx}: {e}"
                    )
        except Exception as e:
            self.custom_logger.warning(f"Structural performance extraction error: {e}")
        return performances

    # ============================================================
    # SVG SEATMAP SCRAPER
    # ============================================================
    def _extract_all_seats(self, sb) -> tuple:
        """Extracts seats and pricing from the currently open SVG modal without looping infinitely."""

        self.custom_logger.info(" \nExtracting seats from all seat map sections...")

        all_seats = {}
        seen_snapshots = set()
        click_count = 0
        section_click_count = 0
        currency = None

        while True:
            try:
                # ------------------------------------------------
                # WAIT FOR SEAT MAP TO SETTLE
                # ------------------------------------------------
                sb.wait_for_element_present(
                    "circle[data-seat-row], g#screenMap polygon.picker", timeout=10
                )
                human_delay(1.0, 2.0)
        
                # =================================================
                # 1. HANDLE SVG SECTION SELECTION
                # =================================================
                tier_sections = sb.find_elements("g#screenMap polygon.picker")
                if tier_sections:
                    self.custom_logger.info(f" Found {len(tier_sections)} tier seat sections")

                    for sec in tier_sections:
                        aria = sec.get_attribute("aria-label") or ""

                        if sec.is_displayed():
                            # Click the section to switch views
                            sb.execute_script(
                                """
                            var element = arguments[0];
                            var evt = document.createEvent("MouseEvents");
                            evt.initMouseEvent("click", true, true, window, 0, 0, 0, 0, 0, false, false, false, false, 0, null);
                            element.dispatchEvent(evt);
                            """,
                                sec,
                            )
                            section_click_count += 1

                            self.custom_logger.info(" Clicked the tier section")                   
                            human_delay(1.0, 1.5)
                            break  # Break out of the sections loop to parse the newly loaded elements

                # =================================================
                # 2. COLLECT AND VALIDATE FRESH SEATS (Correct Sequence Placement)
                # =================================================
                seats = sb.find_elements("circle[data-seat-row][data-seat-seat]")
                # Create a unique fingerprint string of current rows and seat numbers
                seat_fingerprint = "|".join(
                    sorted(
                        [
                            (s.get_attribute("data-seat-row") or "")
                            + (s.get_attribute("data-seat-seat") or "")
                            for s in seats
                        ]
                    )
                )

                #  INFINITE LOOP PROTECTION: Stop if this view has already been scraped
                if seat_fingerprint in seen_snapshots:
                    self.custom_logger.info(
                        " Duplicate state detected. Reached the end of sections."
                    )
                    break

                seen_snapshots.add(seat_fingerprint)
                self.custom_logger.info(
                    f" Found {len(seats)} unique seats in this section"
                )

                # =================================================
                # 3. EXTRACT SEAT DATA
                # =================================================
                for seat in seats:
                    try:
                        # 'A' = Available, 'S' = Sold, 'U' = Unavailable/Hold
                        status = seat.get_attribute("data-status")
                        if status == "A":
                            row_name = seat.get_attribute("data-seat-row")
                            seat_no = seat.get_attribute("data-seat-seat")
                            section = seat.get_attribute("data-seat-section")
                            aria = seat.get_attribute("aria-label") or ""

                            section_name = section.split()[0].strip()

                            if not currency:
                                currency = get_currency_from_price(aria)

                            match = re.search(r"\$([\d]+(?:\.\d+)?)", aria)
                            if not match:
                                continue

                            price = float(match.group(1))

                            seat_id = f"{section} {row_name}{seat_no}".strip()
                            # Deduplicate records by seat ID
                            all_seats[seat_id] = {
                                "seat": seat_id,
                                "ticket_price": price,
                            }

                    except Exception as e:
                        self.custom_logger.debug(
                            f" couldn't find available seats in this section : {e}",
                            "warning",
                        )

                # -----------------------------------
                # 4. CLICK NEXT SECTION ARROW
                # -----------------------------------
                try:
                    seatmap_arrow = sb.find_element(
                        "div.map-container button.bottom-arrow"
                    )

                    # Enhanced exit condition: Stop if hidden OR explicitly disabled via CSS class
                    if not seatmap_arrow.is_displayed() or "disabled" in (
                        seatmap_arrow.get_attribute("class") or ""
                    ):
                        self.custom_logger.info(
                            " Arrow button is hidden or disabled. Map processing complete."
                        )
                        break

                    sb.execute_script("arguments[0].click();", seatmap_arrow)
                    click_count += 1

                    self.custom_logger.info(f" Finished extracting seats from section : {section_name}")
                    self.custom_logger.info(f" Clicked seat map arrow ({click_count})")
                    human_delay(1.5, 2.0)

                except Exception as e:
                    self.custom_logger.debug(
                        f" Reached final seat map section (Arrow element missing): {e}"
                    )
                    break

            except Exception as e:
                self.custom_logger.debug(f"Seat canvas extraction subloop failure: {e}")
                break

        seat_list = list(all_seats.values())
        capacity = len(all_seats)
        return seat_list, currency, capacity

    def _extract_seat_pricing_metrics(self, sb, performances) -> dict:
        seat_pricing = {}
        has_seat_map = False

        # Save the original event details window handle
        main_window = sb.driver.current_window_handle

        for i, perf in enumerate(performances, 1):
            perf_key = f"{perf['date']} {perf['time']}"

            self.custom_logger.info(
                f"  [{i}/{len(performances)}] Seats for {perf['date']} {perf['time']}"
            )

            try:
                # -----------------------------------
                # OPEN GET TICKETS PAGE
                # -----------------------------------
                # Clicking the button or loading the link triggers a new tab
                self._safe_navigate_to_ticket_page(sb, perf["get_ticket_btn"])

                # Refresh main_window after reconnect — UC mode may assign a new handle
                try:
                    main_window = sb.driver.current_window_handle
                except Exception:
                    pass

                # -----------------------------------
                # GET TICKETS OPENS NEW TAB
                # -----------------------------------
                if len(sb.driver.window_handles) > 1:
                    new_tab = [h for h in sb.driver.window_handles if h != main_window][
                        0
                    ]
                    sb.driver.switch_to.window(new_tab)

                # -----------------------------------
                # WAIT FOR BUY PAGE AND MATCH DATES
                # -----------------------------------
                target_row = None
                is_sold_out = False

                while True:
                    sb.wait_for_element_present("div.result-box-item", timeout=12)
                    rows = sb.find_elements("div.result-box-item")

                    for row in rows:
                        try:
                            availability = (
                                row.find_element(By.CSS_SELECTOR, ".availability-text")
                                .get_attribute("textContent")
                                .strip()
                            )
                            dt_text = (
                                row.find_element(By.CSS_SELECTOR, ".start-date")
                                .get_attribute("textContent")
                                .strip()
                            )
                            row_dt = parser.parse(dt_text)

                            row_date = row_dt.strftime("%Y-%m-%d")
                            row_time = row_dt.strftime("%H:%M")

                            # Match current performance
                            if row_date == perf["date"] and row_time == perf["time"]:
                                # skip sold out
                                if "Sold Out" in availability:
                                    self.custom_logger.info(
                                        f"Performance {perf_key} is sold out."
                                    )
                                    is_sold_out = True
                                else:
                                    target_row = row
                                    self.custom_logger.info(
                                        f" Matched performance: "
                                        f"{row_date} {row_time}"
                                    )
                                break

                        except Exception as e:
                            self.custom_logger.warning(
                                f" Row match failed: {e}", "warning"
                            )
                            continue

                    # EXIT LOGIC
                    if is_sold_out:
                        seat_pricing[perf_key] = []  # Mark as empty
                        break  # Break the while-loop to move to next perf

                    # If we successfully matched a performance, break out of pagination loop
                    if target_row:
                        break

                    # If we checked all rows on this page and found nothing, handle the Next arrow
                    try:
                        next_btn = sb.find_elements("#av-next-link a")
                        if next_btn and not sb.is_element_visible(
                            ".disabled"
                        ):  # Check i
                            # old_row = rows[0]
                            sb.execute_script("arguments[0].click();", next_btn[0])
                            human_delay(1.0, 3.0)
                        else:
                            self.custom_logger.info(
                                " Reached the last page. No available match found anywhere."
                            )
                            break  # No next button means we exhaustively searched all pages

                    except Exception as pagination_error:
                        self.custom_logger.warning(
                            f" Failed to navigate pagination: {pagination_error}",
                            "error",
                        )
                        break

                if not target_row:
                    # Performance not found on ticket page (e.g. Cloudflare block) — keep empty entry
                    self.custom_logger.warning(" No available performance found")
                    seat_pricing[perf_key] = []
                    continue

                # -----------------------------------
                # CLICK BUY
                # -----------------------------------
                buy_button = target_row.find_element(
                    By.CSS_SELECTOR, "a.btn.btn-primary, #popupDivOpen"
                )
                sb.execute_script("arguments[0].click();", buy_button)

                # ------------------------------------------------
                # WAIT FOR SEAT MAP
                # ------------------------------------------------
                seat_list, currency, capacity = self._extract_all_seats(sb)

                if seat_list:
                    has_seat_map = True
                    seat_pricing[perf_key] = seat_list
                    perf["capacity"] = capacity
                    perf["currency"] = currency

                    self.custom_logger.info(f" Seats: {len(seat_list)} ")
                else:
                    seat_pricing[perf_key] = []

                # -----------------------------------
                # CLOSE BUY TAB and return to ticket list
                # -----------------------------------
                try:
                    if sb.driver.current_window_handle != main_window:
                        sb.driver.close()
                        sb.driver.switch_to.window(main_window)
                except Exception:
                    pass

            except Exception as e:
                self.custom_logger.debug(f"⚠️ seat extraction error: {e}", "warning")
                seat_pricing.setdefault(perf_key, [])
                continue

        # If no performance yielded actual seat data, the show has no seat map
        if not has_seat_map:
            return {}

        return seat_pricing


def main():
    extractor = BlumenthalArtsExtractor(
        local_test=False, save_csv_locally=True, csv_incremental_mode=False
    )

    result = extractor.run()
    logger.info(f"Extraction result: {result}")

    if result.get("status") not in ("success", "validation_failed"):
        sys.exit(1)


if __name__ == "__main__":
    main()
