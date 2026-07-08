def _scrape_performance_seats(self, sb) -> tuple[list, int | None, str | None]:
        """Extract all seat data from the currently-loaded performance page.

        Returns (seats, capacity, currency).
        seats is an empty list when scraping failed or no available seats found.
        """
        seat_data = []
        perf_capacity = 0
        currency = None

        try:
            sb.wait_for_ready_state_complete()
            human_delay(2, 3)

            dropdown_selector = SELECTORS["seating_dropdown"]
            has_dropdown = False
            areas = []

            try:
                sb.wait_for_element_present(dropdown_selector, timeout=15)
                has_dropdown = True
                self.custom_logger.info("Dropdown found on main page")
            except Exception:
                pass

            if not has_dropdown:
                try:
                    iframes = sb.find_elements("iframe")
                    for iframe in iframes:
                        try:
                            sb.switch_to_frame(iframe)
                            human_delay(2, 3)
                            sb.execute_script("window.scrollTo(0, 300);")
                            human_delay(1, 2)
                            sb.execute_script("window.scrollTo(0, 0);")
                            human_delay(1, 2)
                            sb.wait_for_element_present(dropdown_selector, timeout=25)
                            has_dropdown = True
                            self.custom_logger.info("Dropdown found in iframe")
                            break
                        except Exception:
                            sb.switch_to_default_content()
                except Exception as iframe_err:
                    self.custom_logger.warning("iframe search failed: %s", iframe_err)

            if has_dropdown:
                raw_options = sb.execute_script(
                    """
                    var select = document.querySelector(arguments[0]);
                    if (!select) return [];
                    var options = [];
                    for (var i = 0; i < select.options.length; i++) {
                        options.push(select.options[i].text.trim());
                    }
                    return options;
                    """,
                    dropdown_selector,
                )
                areas = [o for o in raw_options if o and o != "The Matcham Auditorium"]
                self.custom_logger.info("Found dropdown with areas: %s", areas)
            else:
                self.custom_logger.info("No dropdown — using single level seating")
                areas = ["Stalls"]

            prev_seat_count = -1  # sentinel: no area scraped yet

            for area in areas:
                try:
                    self.custom_logger.info("Selecting area: %s", area)

                    if has_dropdown:
                        try:
                            result = sb.execute_script(
                                """
                                var select = document.querySelector(arguments[0]);
                                if (!select) return false;
                                var areaName = arguments[1];
                                for (var i = 0; i < select.options.length; i++) {
                                    if (select.options[i].text.trim() === areaName) {
                                        select.value = select.options[i].value;
                                        select.dispatchEvent(new Event('change', { bubbles: true }));
                                        return true;
                                    }
                                }
                                return false;
                                """,
                                dropdown_selector,
                                area,
                            )
                            if not result:
                                self.custom_logger.warning(
                                    "Could not find area %s in dropdown", area
                                )
                                continue
                            sb.wait_for_ready_state_complete()
                            for _ in range(15):
                                human_delay(2, 3)
                                # Break only when the seat count changes from the
                                # previous area — proving the iframe re-rendered.
                                # Without this check the stale previous-area chart
                                # (still visible during re-render) triggers a false
                                # break and every subsequent area returns wrong data.
                                _cur_count = len(
                                    sb.find_elements(
                                        By.CSS_SELECTOR, SELECTORS["all_seats"]
                                    )
                                )
                                if _cur_count > 0 and _cur_count != prev_seat_count:
                                    break
                                sb.execute_script("window.scrollTo(0, 300);")
                                human_delay(1, 2)
                                sb.execute_script("window.scrollTo(0, 0);")
                        except Exception as dropdown_error:
                            self.custom_logger.warning(
                                "Failed to select area %s: %s", area, dropdown_error
                            )
                            continue

                    self.custom_logger.info("Scraping seats for: %s", area)

                    try:
                        all_seats = sb.find_elements(
                            By.CSS_SELECTOR, SELECTORS["all_seats"]
                        )
                        self.custom_logger.info(f" Found {len(all_seats)} unique seats. ")
                        area_capacity = len(all_seats)
                        prev_seat_count = area_capacity  # update for next area
                        perf_capacity += area_capacity

                        self.custom_logger.info(
                            "Area: %s | Total Seats: %s", area, area_capacity
                        )

                        seat_tooltips = sb.execute_script(
                            """
                            var elems = document.querySelectorAll(arguments[0]);
                            var out = [];
                            for (var i = 0; i < elems.length; i++) {
                                out.push(elems[i].getAttribute('tooltip') || elems[i].getAttribute('title') || '');
                            }
                            return out;
                            """,
                            SELECTORS["available_seats"],
                        )

                        for tooltip in seat_tooltips:
                            try:
                                if not tooltip or tooltip == "Unavailable":
                                    continue
                                price_match = re.search(
                                    r"[£$€](\d+(?:\.\d+)?)", tooltip
                                )
                                if not price_match:
                                    continue
                                price_val = price_match.group(1)
                                parts = tooltip.split(" - ")
                                price_idx = next(
                                    (
                                        i
                                        for i, p in enumerate(parts)
                                        if re.match(r"[£$€]", p.strip())
                                    ),
                                    None,
                                )
                                seat_id = (
                                    " ".join(p.strip() for p in parts[:price_idx])
                                    if price_idx
                                    else parts[0].strip()
                                )
                                seat_data.append(
                                    {
                                        "seat": f"{area} {seat_id}",
                                        "ticket_price": float(price_val),
                                    }
                                )
                                if currency is None:
                                    currency = get_currency_from_price(
                                        price_match.group()
                                    )
                            except Exception as seat_error:
                                self.custom_logger.warning(
                                    "Failed to parse seat: %s", seat_error
                                )
                                continue

                    except Exception as seat_extraction_error:
                        self.custom_logger.error(
                            "Seat extraction error for area %s: %s",
                            area,
                            seat_extraction_error,
                        )
                        continue

                except Exception as area_error:
                    self.custom_logger.warning(
                        "Failed to process area %s: %s", area, area_error
                    )
                    continue

        except Exception as e:
            self.custom_logger.error("Seat map scraping failed: %s", e)
        finally:
            try:
                sb.switch_to_default_content()
            except Exception:
                pass

        return seat_data, (perf_capacity if perf_capacity > 0 else None), currency
