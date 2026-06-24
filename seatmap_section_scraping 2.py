def extract_all_seats(driver):
    """Extracts seats and pricing from all sections sequentially without looping back."""
    log("\nExtracting seats from all seat map sections...")

    all_seats = {}
    currency = None

    # Wait for macro-level tier selections to load
    WebDriverWait(driver, 10).until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, "g#screenMap polygon.picker")
    ))
    time.sleep(1)

    # Gather the unique IDs of all sections to prevent stale element issues
    sections = driver.find_elements(By.CSS_SELECTOR, "g#screenMap polygon.picker")
    section_ids = [sec.get_attribute("id") for sec in sections if sec.get_attribute("id")]
    
    log(f"🧭 Found {len(section_ids)} master seat sections to process.")

    # Loop precisely through each section ID exactly once
    for index, sec_id in enumerate(section_ids, 1):
        try:
            # Re-find the element by ID to ensure it isn't stale
            sec = driver.find_element(By.ID, sec_id)
            aria = sec.get_attribute("aria-label") or f"Section {index}"
            log(f"🎭 Switching to section ({index}/{len(section_ids)}): {aria}")
            
            # Click the section via JavaScript event injection
            driver.execute_script("""
                var element = arguments[0];
                var evt = document.createEvent("MouseEvents");
                evt.initMouseEvent("click", true, true, window, 0, 0, 0, 0, 0, false, false, false, false, 0, null);
                element.dispatchEvent(evt);
            """, sec)
            
            # Wait for the corresponding section's individual seat circles to render
            WebDriverWait(driver, 10).until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "circle[data-seat-row][data-seat-seat]")
            ))
            time.sleep(2)  # Give DOM stability transition time

            # Collect seats specific to the active view
            seats = driver.find_elements(By.CSS_SELECTOR, "circle[data-seat-row][data-seat-seat]")
            log(f"📦 Found {len(seats)} seats in this section view")

            for seat in seats:
                row_name = seat.get_attribute("data-seat-row")
                seat_no = seat.get_attribute("data-seat-seat")
                section = seat.get_attribute("data-seat-section")
                aria_seat = (seat.get_attribute("aria-label") or "")

                if not currency:
                    currency = detect_currency(aria_seat)

                match = re.search(r"\$([\d]+(?:\.\d+)?)", aria_seat)
                if not match:
                    continue
                    
                price = float(match.group(1))
                seat_id = f"{section} {row_name}{seat_no}".strip()
                
                all_seats[seat_id] = {
                    "seat": seat_id,
                    "ticket_price": price
                }

        except Exception as e:
            log(f"⚠️ Error extraction failed on section {sec_id}: {e}", "warning")
            continue  # Proceed to the next section tier even if one fails

    seat_list = list(all_seats.values())
    capacity = len(seat_list)
    log(f"🎟 Total unique seats extracted: {capacity}")

    return seat_list, currency, capacity
