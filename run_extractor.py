"""Blumenthal Arts extractor implementation using the framework.

Listing:   SeleniumBase UC Mode (scrapes target category pages)
Detail:    SeleniumBase UC Mode (navigates to individual show info)
Seat map:  SVG Map extraction inside individual show ticket pages.
"""
import json
import re
import time
from datetime import date, datetime

import pandas as pd
from dateutil import parser as dateutil_parser
from selenium.webdriver.common.by import By

from utils.base_extractor import BaseExtractor
from utils.logger import setup_logger
from utils.scraping_helpers import (
    get_scrape_datetime,
    human_delay,
    standardize_category,
)

from .blumenthal_arts_config import PAGES, RUN_HEADLESS

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

        self.custom_logger.info("🚀 Starting SeleniumBase Browser Context...")

        # ------------------------------------------------------------------
        # Phase 1 — collect event listings from all category pages
        # ------------------------------------------------------------------
        all_events = []
        with SB(uc=True, headless=RUN_HEADLESS, rtf=True) as sb:
            for page_idx, (url, category) in enumerate(PAGES, start=1):
                self.custom_logger.info(
                    f"\n🌍 CATEGORY CORRELATION {page_idx}/{len(PAGES)} → {category}"
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
        # Phase 2 — per-show detail extraction (one fresh browser per show)
        # ------------------------------------------------------------------
        total = len(all_events)
        for i, e in enumerate(all_events, start=1):
            self.custom_logger.info(
                f"\n🎭 EVENT SPECIFIC EXTRACTION {i}/{total} → {e['title']}"
            )
            try:
                with SB(uc=True, headless=RUN_HEADLESS, rtf=True) as sb:
                    if not self._safe_get(sb, e["venue_url"]):
                        continue

                    self._scroll_to_load_all(sb)
                    performances = self._extract_events_performance_dates(sb)
                    try:
                        seat_pricing = self._extract_seat_pricing_metrics(sb, performances)
                    except Exception as exc:
                        self.custom_logger.warning(
                            "Seat pricing extraction failed for %s: %s", e["title"], exc
                        )
                        seat_pricing = {}

                    if seat_pricing:
                        capacity = max(
                            [p.get("capacity", 0) for p in performances], default=0
                        ) or 2100
                        currency = next(
                            (p.get("currency") for p in performances if p.get("currency")),
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
                        f"✅ Extracted Row Record Saved: {e['title']}"
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
            dt = dateutil_parser.parse(text, dayfirst=True, fuzzy=True)
            if dt.date() < date.today():
                dt = dt.replace(year=dt.year + 1)
            return dt
        except Exception:
            return None

    def _detect_currency(self, text: str) -> str | None:
        if not text:
            return None
        if "£" in text:
            return "GBP"
        elif "$" in text:
            return "USD"
        elif "€" in text:
            return "EUR"
        return None

    def _get_theatre_details(self, theatre_name: str) -> dict:
        normalized_name = theatre_name.lower().strip() if theatre_name else ""
        theatre_map = {
            "belk theater": {
                "address": "130 N Tryon St",
                "city": "Charlotte",
                "country": "USA",
            },
            "booth playhouse": {
                "address": "130 N Tryon St",
                "city": "Charlotte",
                "country": "USA",
            },
            "knight theater": {
                "address": "550 South Tryon Street",
                "city": "Charlotte",
                "country": "USA",
            },
            "stage door theater": {
                "address": "130 N Tryon St",
                "city": "Charlotte",
                "country": "USA",
            },
        }
        for key, data in theatre_map.items():
            if key in normalized_name:
                return data
        return {"address": "130 N Tryon St", "city": "Charlotte", "country": "USA"}

    # ------------------------------------------------------------------ #
    # Operational Data Pipeline Components                               #
    # ------------------------------------------------------------------ #

    def _extract_events(self, sb, category: str) -> list:
        self.custom_logger.info(f"🎭 Extracting events for category: {category}")
        cards = sb.find_elements("div.eventItem, div.event-list-item, article.event")
        if not cards:
            cards = sb.find_elements(".event")

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
                    f"Event list item parse error at index {i}: {e}"
                )
        return events

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
            for idx, block in enumerate(blocks, start=1):
                try:
                    try:
                        month = block.find_element(
                            By.CSS_SELECTOR, ".m-date__month, .month"
                        ).text.strip()
                        day = block.find_element(
                            By.CSS_SELECTOR, ".m-date__day, .day"
                        ).text.strip()
                        time_text = block.find_element(
                            By.CSS_SELECTOR, "span.time.cell, .time"
                        ).text.strip()
                        date_string = f"{month} {day} {year} {time_text}"
                        parsed_dt = self._parse_date(date_string)
                    except Exception:
                        continue  # skip blocks whose date elements can't be found

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

    def _extract_all_seats(self, sb) -> tuple:
        all_seats = {}
        seen_snapshots = set()

        currency = None

        while True:
            try:
                sb.wait_for_element_present(
                    "circle[data-seat-row], g#screenMap polygon.picker", timeout=10
                )
                human_delay(1.0, 2.0)

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

                if seat_fingerprint in seen_snapshots or not seats:
                    break
                seen_snapshots.add(seat_fingerprint)

                sections = sb.find_elements("g#screenMap polygon.picker")
                for sec in sections:
                    if sec.is_displayed():
                        sb.execute_script(
                            "arguments[0].dispatchEvent(new MouseEvent('click', {bubbles: true}));",
                            sec,
                        )
                        human_delay(1.0, 1.5)
                        break

                for seat in seats:
                    row_name = seat.get_attribute("data-seat-row")
                    seat_no = seat.get_attribute("data-seat-seat")
                    section = seat.get_attribute("data-seat-section")
                    aria = seat.get_attribute("aria-label") or ""

                    if not currency:
                        currency = self._detect_currency(aria)

                    match = re.search(r"\$([\d]+(?:\.\d+)?)", aria)
                    if match:
                        price = float(match.group(1))
                        seat_id = f"{section} {row_name}{seat_no}".strip()
                        all_seats[seat_id] = {"seat": seat_id, "ticket_price": price}

                try:
                    seatmap_arrow = sb.find_element(
                        "div.map-container button.bottom-arrow"
                    )
                    if (
                        "disabled" in seatmap_arrow.get_attribute("class")
                        or not seatmap_arrow.is_displayed()
                    ):
                        break
                    sb.execute_script("arguments[0].click();", seatmap_arrow)
                    human_delay(1.5, 2.0)
                except Exception:
                    break
            except Exception as e:
                self.custom_logger.debug(f"Seat canvas extraction subloop failure: {e}")
                break

        seat_list = list(all_seats.values())
        return seat_list, currency, len(seat_list)

    def _extract_seat_pricing_metrics(self, sb, performances) -> dict:
        seat_pricing = {}
        has_seat_map = False

        try:
            main_window = sb.driver.current_window_handle
        except Exception as e:
            self.custom_logger.warning("Browser session lost before seat pricing: %s", e)
            return {}

        for perf in performances:
            perf_key = f"{perf['date']} {perf['time']}"
            try:
                sb.uc_open_with_reconnect(perf["get_ticket_btn"], reconnect_time=6)
                human_delay(1.5, 2.5)

                # Refresh main_window after reconnect — UC mode may assign a new handle
                try:
                    main_window = sb.driver.current_window_handle
                except Exception:
                    pass

                if len(sb.driver.window_handles) > 1:
                    new_tab = [h for h in sb.driver.window_handles if h != main_window][0]
                    sb.driver.switch_to.window(new_tab)

                if self._is_bot_protection(sb):
                    self.custom_logger.warning(
                        "UC Captcha Intercept triggered on ticket page. Solving..."
                    )
                    sb.uc_gui_click_captcha()
                    human_delay(2.0, 3.0)

                sb.wait_for_element_present("div.result-box-item", timeout=12)
                rows = sb.find_elements("div.result-box-item")
                target_row = None
                is_sold_out = False

                for row in rows:
                    try:
                        availability = row.find_element(
                            By.CSS_SELECTOR, ".availability-text"
                        ).text.strip()
                        dt_text = row.find_element(
                            By.CSS_SELECTOR, ".start-date"
                        ).text.strip()
                        row_dt = dateutil_parser.parse(dt_text)

                        if (
                            row_dt.strftime("%Y-%m-%d") == perf["date"]
                            and row_dt.strftime("%H:%M") == perf["time"]
                        ):
                            if "Sold Out" in availability:
                                is_sold_out = True
                            else:
                                target_row = row
                            break
                    except Exception:
                        continue

                if is_sold_out:
                    # Performance is sold out — omit from seat_pricing, keep in upcoming_performances
                    self.custom_logger.info("Performance %s is sold out — omitting from seat_pricing", perf_key)
                    continue

                if not target_row:
                    # Performance not found on ticket page (e.g. Cloudflare block) — keep empty entry
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
                else:
                    seat_pricing[perf_key] = []

                # Close any new tab opened by the buy button and return to ticket list
                try:
                    if sb.driver.current_window_handle != main_window:
                        sb.driver.close()
                        sb.driver.switch_to.window(main_window)
                except Exception:
                    pass

            except Exception as e:
                self.custom_logger.debug(f"Seat pricing mapping instance failure: {e}")
                seat_pricing.setdefault(perf_key, [])
                continue

        # If no performance yielded actual seat data, the show has no seat map
        if not has_seat_map:
            return {}

        return seat_pricing


def main():
    extractor = BlumenthalArtsExtractor(local_test=False)
    extractor.run()


if __name__ == "__main__":
    main()
