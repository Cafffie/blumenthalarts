"""Configuration settings registry for Blumenthal Arts scraper."""

BASE_URL = "https://www.blumenthalarts.org"
RUN_HEADLESS = False
MAX_RETRIES = 3
RETRY_DELAY = (2, 4)

PAGES = [
    ("https://www.blumenthalarts.org/events-tickets/category/theater", "Play"),
    (
        "https://www.blumenthalarts.org/events-tickets/category/broadway-at-blumenthal",
        "Musical",
    ),
]

THEATRE_DETAILS_MAP = {
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

DEFAULT_THEATRE_DETAILS = {
    "address": "130 N Tryon St",
    "city": "Charlotte",
    "country": "USA",
}

QUEUE_COOKIES = [
    {
        "name": "Queue-it-307338e1-2327-4d90-945a-f5db32f94284",
        "value": "uifh=O7Y5LI5D1Op3tSyblb3-q6LUyY_AIiFlawVTLnDJtym0jwdEClKq7Gsr5-oFJ1eU0&WasRedirected=true&i=639020351466287405",
        "domain": "queue.atgtickets.com",
        "path": "/",
    },
    {
        "name": "__cf_bm",
        "value": "344oZL98yi.75g3IGrT8ZThq.E7fWxzpEXSsSMVO5dM-1766438377-1.0.1.1-vaP_44u8DBUsXi3Dq6dRrcceyHeo46FVzSYL9c82X1g78ZbUkQKqaVDU52Xaktck4wnHBdHo8wPAX24xNuusmaB_B_kcrN7A_7hXtLcelFE",
        "domain": ".atgtickets.com",
        "path": "/",
    },
    {
        "name": "QueueITAccepted-SDFrts345E-V3_bolt",
        "value": "EventId%3Dbolt%26QueueId%3D307338e1-2327-4d90-945a-f5db32f94284%26RedirectType%3Dqueue%26IssueTime%3D1766438382%26Hash%3Dde3124d738f782946a8fe837f8b27da9d85998998b6ccdcf94aa3fded8b51da6",
        "domain": "www.atgtickets.com",
        "path": "/",
    },
]
