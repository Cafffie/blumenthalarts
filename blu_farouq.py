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
    human_scroll,
    safe_get_denver,
    standardize_category,
)

from .blumenthal_arts_config import (
    DEFAULT_THEATRE_DETAILS,
    MAX_RETRIES,
    PAGES,
    QUEUE_COOKIES,
    RETRY_DELAY,
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
                human_delay(1.5, 3.0)

                loaded = False
                for attempt in range(1, MAX_RETRIES + 1):
                    if safe_get_denver(sb, url):
                        loaded = True
                        break
                    self.custom_logger.warning(
                        f"Listing load attempt {attempt}/{MAX_RETRIES} failed for {url}"
                    )
                    if attempt < MAX_RETRIES:
                        human_delay(*RETRY_DELAY)

                if not loaded:
                    continue

                human_scroll(sb)
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

            extracted = False
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    with SB(uc=True, headless=RUN_HEADLESS, rtf=True) as sb:
                        if not safe_get_denver(sb, e["venue_url"]):
                            raise RuntimeError(f"Failed to load {e['venue_url']}")

                        human_scroll(sb)
                        performances = self._extract_events_performance_dates(sb)

                        seat_pricing = self._extract_seat_pricing_metrics(
                            sb, performances
                        )

                        if seat_pricing:
                            capacity = (
                                max(
                                    (p.get("capacity", 0) for p in performances),
                                    default=0,
                                )
                                or None
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
                        extracted = True
                        break

                except Exception as exc:
                    self.custom_logger.warning(
                        f"Show extraction attempt {attempt}/{MAX_RETRIES} failed "
                        f"for {e['title']}: {exc}"
                    )
                    if attempt < MAX_RETRIES:
                        human_delay(*RETRY_DELAY)

            if not extracted:
                self.custom_logger.error(
                    f"All {MAX_RETRIES} attempts failed for {e['title']}"
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
        safe_get_denver(sb, url)

        for cookie in QUEUE_COOKIES:
            try:
                if cookie["domain"] in sb.get_current_url():
                    sb.add_cookie(cookie)
            except Exception as e:
                self.custom_logger.debug(f"Failed to add cookie {cookie['name']}: {e}")

        sb.refresh()
        human_delay(1.5, 2.5)

        accept_cookies(sb.driver, logger=self.custom_logger)

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
                    f"Event list item parse error at block index {i}: {e}"
                )

        self.custom_logger.debug(f" Total base events extracted: {len(events)}")
        return events

    # ============================================================
    # PERFORMANCE DATES EXTRACTION
    # ============================================================

    def _extract_events_performance_dates(self, sb) -> list:
        performances = []
        try:
            try:
                year = (
                    sb.find_element(".event_heading .m-date__year")
                    .text.replace(",", "")
                    .strip()
                )
            except Exception:
                year = str(datetime.now().year)

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
        """Extracts seats and pricing from the currently open SVG modal.

        capacity = total circles across all sections (available + unavailable).
        """
        self.custom_logger.info(" \nExtracting seats from all seat map sections...")

        all_seats = {}  # priced seats: seat_id -> {seat, ticket_price}
        all_seat_ids = set()  # ALL seat IDs (available + unavailable) for capacity
        seen_snapshots = set()
        click_count = 0
        section_click_count = 0
        currency = None

        # 1. Attempt to find macro-level sections (Polygons)
        if sb.is_element_visible("g#screenMap polygon.picker"):
            sections = sb.find_elements("g#screenMap polygon.picker")
            section_ids = [sec.get_attribute("id") for sec in sections]
            self.custom_logger.info(
                f" Found {len(section_ids)} tier sections to process."
            )

        else:
            section_ids = [None]  # Fallback: No overlays found
            self.custom_logger.warning(
                " Flat-map theater detected. Processing single-view layout."
            )

        # 2. Iterate through sections
        for index, sec_id in enumerate(section_ids, 1):
            try:
                sb.wait_for_element_present(
                    "circle[data-seat-row], g#screenMap polygon.picker", timeout=10
                )
                human_delay(1.0, 2.0)

                sections = sb.find_elements("g#screenMap polygon.picker")
                if sections:
                    self.custom_logger.info(f" \nFound {len(sections)} seat sections")

                    for sec in sections:
                        aria = sec.get_attribute("aria-label") or ""

                        if sec.is_displayed():
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
                            self.custom_logger.info(
                                f" Clicked section ({section_click_count}): {aria}"
                            )
                            human_delay(1.0, 1.5)
                            break

                seats = sb.find_elements("circle[data-seat-row][data-seat-seat]")
                seat_fingerprint = "|".join(
                    sorted(
                        [
                            (s.get_attribute("data-seat-row") or "")
                            + (s.get_attribute("data-seat-seat") or "")
                            for s in seats
                        ]
                    )
                )

                if seat_fingerprint in seen_snapshots:
                    self.custom_logger.info(
                        f" Switching to section ({index}/{len(section_ids)}): {aria}"
                    )

                    # Click the section via JavaScript event injection
                    sb.execute_script(
                        """
                        var element = document.getElementById(arguments[0]);
                        var evt = document.createEvent("MouseEvents");
                        evt.initMouseEvent("click", true, true, window, 0, 0, 0, 0, 0, false, false, false, false, 0, null);
                        element.dispatchEvent(evt);
                    """,
                        sec_id,
                    )
                    self.custom_logger.info(
                        f" Processing Section {index}/{len(section_ids)} (ID: {sec_id})..."
                    )
                    human_delay(
                        1.0, 1.5
                    )  # Stability pause for the DOM to render the seats

                # 3. Scrape visible seats
                seats = sb.find_elements("circle[data-seat-row][data-seat-seat]")
                self.custom_logger.info(f" Found {len(seats)} seats in current view.")

                for seat in seats:
                    row_name = seat.get_attribute("data-seat-row")
                    seat_no = seat.get_attribute("data-seat-seat")
                    section = seat.get_attribute("data-seat-section")
                    aria = seat.get_attribute("aria-label") or ""
                    status = seat.get_attribute("data-status") or ""

                    seat_id = f"{section} {row_name}{seat_no}".strip()
                    all_seat_ids.add(seat_id)  # count all seats for capacity

                    if not currency:
                        currency = get_currency_from_price(aria)

                    # Only include available seats (data-status="A") in seat_pricing
                    if status != "A":
                        continue

                    match = re.search(r"\$([\d]+(?:\.\d+)?)", aria)
                    if not match:
                        continue

                    price = float(match.group(1))
                    all_seats[seat_id] = {"seat": seat_id, "ticket_price": price}

                try:
                    seatmap_arrow = sb.find_element(
                        "div.map-container button.bottom-arrow"
                    )

                    if not seatmap_arrow.is_displayed() or "disabled" in (
                        seatmap_arrow.get_attribute("class") or ""
                    ):
                        self.custom_logger.info(
                            " Arrow button is hidden or disabled. Map processing complete."
                        )
                        break

                    sb.execute_script("arguments[0].click();", seatmap_arrow)
                    click_count += 1
                    self.custom_logger.info(f" Clicked seat map arrow ({click_count})")
                    human_delay(1.5, 2.0)

                except Exception:
                    self.custom_logger.info(
                        " Reached final seat map section (Arrow element missing)"
                    )
                    break

            except Exception as e:
                self.custom_logger.debug(f"Seat canvas extraction subloop failure: {e}")
                break

        seat_list = list(all_seats.values())
        capacity = len(all_seat_ids)  # available + unavailable
        self.custom_logger.info(
            f" Total capacity: {capacity} seats ({len(seat_list)} priced)"
        )
        return seat_list, currency, capacity

    def _extract_seat_pricing_metrics(self, sb, performances) -> dict:
        seat_pricing = {}
        has_seat_map = False

        main_window = sb.driver.current_window_handle

        for i, perf in enumerate(performances, 1):
            perf_key = f"{perf['date']} {perf['time']}"

            self.custom_logger.info(
                f"  [{i}/{len(performances)}] Seats for {perf['date']} {perf['time']}"
            )

            try:
                self._safe_navigate_to_ticket_page(sb, perf["get_ticket_btn"])

                try:
                    main_window = sb.driver.current_window_handle
                except Exception:
                    pass

                if len(sb.driver.window_handles) > 1:
                    new_tab = [h for h in sb.driver.window_handles if h != main_window][
                        0
                    ]
                    sb.driver.switch_to.window(new_tab)

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

                            if row_date == perf["date"] and row_time == perf["time"]:
                                if "Sold Out" in availability:
                                    self.custom_logger.info(
                                        f"Performance {perf_key} is sold out."
                                    )
                                    is_sold_out = True
                                else:
                                    target_row = row
                                    self.custom_logger.info(
                                        f" Matched performance: {row_date} {row_time}"
                                    )
                                break

                        except Exception as e:
                            self.custom_logger.warning(f" Row match failed: {e}")
                            continue

                    if is_sold_out:
                        seat_pricing[perf_key] = []
                        break

                    if target_row:
                        break

                    try:
                        next_btn = sb.find_elements("#av-next-link a")
                        if next_btn and not sb.is_element_visible(".disabled"):
                            old_row = rows[0]
                            sb.execute_script("arguments[0].click();", next_btn[0])
                            sb.wait_for_stale(old_row, timeout=10)
                        else:
                            self.custom_logger.info(
                                " Reached the last page. No available match found anywhere."
                            )
                            break

                    except Exception as pagination_error:
                        self.custom_logger.warning(
                            f" Failed to navigate pagination: {pagination_error}"
                        )
                        break

                if not target_row:
                    self.custom_logger.warning(" No available performance found")
                    seat_pricing[perf_key] = []
                    continue

                buy_button = target_row.find_element(
                    By.CSS_SELECTOR, "a.btn.btn-primary, #popupDivOpen"
                )
                sb.execute_script("arguments[0].click();", buy_button)

                seat_list, currency, capacity = self._extract_all_seats(sb)

                if seat_list:
                    has_seat_map = True
                    seat_pricing[perf_key] = seat_list
                    perf["capacity"] = capacity
                    perf["currency"] = currency
                    self.custom_logger.info(f" Seats: {capacity} ")
                else:
                    seat_pricing[perf_key] = []

                try:
                    if sb.driver.current_window_handle != main_window:
                        sb.driver.close()
                        sb.driver.switch_to.window(main_window)
                except Exception:
                    pass

            except Exception as e:
                self.custom_logger.debug(f"seat extraction error: {e}")
                seat_pricing.setdefault(perf_key, [])
                continue

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
