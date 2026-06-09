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
