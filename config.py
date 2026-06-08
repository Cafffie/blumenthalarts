"""Configuration settings registry for Blumenthal Arts scraper."""

BASE_URL = "https://www.blumenthalarts.org"
RUN_HEADLESS = False

PAGES = [
    ("https://www.blumenthalarts.org/events-tickets/category/broadway-at-blumenthal", "Musical"),
    ("https://www.blumenthalarts.org/events-tickets/category/theater", "Play")
]