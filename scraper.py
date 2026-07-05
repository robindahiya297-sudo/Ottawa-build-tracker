"""
Ottawa New Construction Homes -> Google Sheets tracker.

Scrapes Zillow's public new-construction search results for Ottawa, ON,
deduplicates against an existing Google Sheet by listing URL, and appends
any newly discovered homes.

Required environment variables:
    GOOGLE_CREDENTIALS  Full JSON of a Google service-account key.
    SHEET_NAME          (optional) Google Sheet name. Defaults below.
"""

import json
import os
import sys
import time

import gspread
import requests
from bs4 import BeautifulSoup

SEARCH_URL = "https://www.zillow.com/ottawa-on/new-homes/"
SHEET_NAME = os.environ.get("SHEET_NAME", "Ottawa New Construction Leads")

HEADER_ROW = [
    "Plan / Model Name",
    "Builder",
    "Price",
    "Neighborhood / Location",
    "Beds",
    "Baths",
    "Sqft",
    "Listing URL",
    "Date Added",
]

# Browser-like headers. Zillow serves a captcha page to bare requests,
# so these need to look like a real Chrome session.
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
    "Referer": "https://www.zillow.com/",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}


def fetch_page(url: str, session: requests.Session) -> str:
    """Fetch a Zillow page, failing loudly if we got blocked."""
    resp = session.get(url, headers=REQUEST_HEADERS, timeout=30)
    if resp.status_code == 403 or "captcha" in resp.text.lower()[:5000]:
        sys.exit(
            "Blocked by Zillow's bot protection (HTTP "
            f"{resp.status_code}). Consider routing through a scraping "
            "proxy service or a residential IP."
        )
    resp.raise_for_status()
    return resp.text


def extract_search_state(html: str) -> dict:
    """
    Zillow renders search results client-side from a JSON blob embedded in
    a <script id="__NEXT_DATA__"> tag. Parsing that JSON is far more stable
    than scraping the listing-card HTML, whose class names change often.
    """
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        sys.exit(
            "Could not find __NEXT_DATA__ on the page - Zillow may have "
            "changed their page structure or served a bot-check page."
        )
    data = json.loads(script.string)
    try:
        return data["props"]["pageProps"]["searchPageState"]
    except KeyError:
        sys.exit("Unexpected __NEXT_DATA__ shape - Zillow changed their schema.")


def parse_listings(search_state: dict) -> list[dict]:
    """Flatten Zillow's listResults into the rows we care about."""
    results = (
        search_state.get("cat1", {})
        .get("searchResults", {})
        .get("listResults", [])
    )
    listings = []
    for item in results:
        detail_url = item.get("detailUrl") or ""
        if detail_url.startswith("/"):
            detail_url = "https://www.zillow.com" + detail_url
        if not detail_url:
            continue

        home_info = item.get("hdpData", {}).get("homeInfo", {}) or {}

        # New-construction cards sometimes carry builder/community info;
        # fall back gracefully when a field isn't present.
        title = (
            item.get("statusText")
            or item.get("marketingStatusSimplifiedCd")
            or item.get("addressStreet")
            or "Unknown"
        )
        builder = (
            item.get("builderName")
            or item.get("brokerName")
            or home_info.get("brokerName")
            or "N/A"
        )
        neighborhood = ", ".join(
            part
            for part in (
                item.get("addressStreet"),
                item.get("addressCity") or home_info.get("city"),
                item.get("addressState") or home_info.get("state"),
            )
            if part
        )

        listings.append(
            {
                "title": title,
                "builder": builder,
                "price": item.get("price")
                or (f"${home_info['price']:,.0f}" if home_info.get("price") else "N/A"),
                "neighborhood": neighborhood or "Ottawa, ON",
                "beds": item.get("beds") or home_info.get("bedrooms") or "",
                "baths": item.get("baths") or home_info.get("bathrooms") or "",
                "sqft": item.get("area") or home_info.get("livingArea") or "",
                "url": detail_url,
            }
        )
    return listings


def scrape_all_pages() -> list[dict]:
    """Walk the paginated search results until Zillow reports no next page."""
    session = requests.Session()
    listings = []
    page = 1
    while True:
        url = SEARCH_URL if page == 1 else f"{SEARCH_URL}{page}_p/"
        print(f"Fetching page {page}: {url}")
        state = extract_search_state(fetch_page(url, session))
        page_listings = parse_listings(state)
        if not page_listings:
            break
        listings.extend(page_listings)

        pagination = (
            state.get("cat1", {}).get("searchList", {}).get("pagination") or {}
        )
        if not pagination.get("nextUrl"):
            break
        page += 1
        time.sleep(3)  # be polite between pages
    return listings


def get_worksheet() -> gspread.Worksheet:
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        sys.exit("GOOGLE_CREDENTIALS environment variable is not set.")
    client = gspread.service_account_from_dict(json.loads(creds_json))
    worksheet = client.open(SHEET_NAME).sheet1
    if not worksheet.row_values(1):
        worksheet.append_row(HEADER_ROW)
    return worksheet


def main() -> None:
    listings = scrape_all_pages()
    print(f"Scraped {len(listings)} listings from Zillow.")
    if not listings:
        sys.exit("No listings found - aborting rather than writing nothing.")

    worksheet = get_worksheet()
    url_column = HEADER_ROW.index("Listing URL") + 1
    existing_urls = set(worksheet.col_values(url_column)[1:])  # skip header

    today = time.strftime("%Y-%m-%d")
    new_rows = [
        [
            home["title"],
            home["builder"],
            home["price"],
            home["neighborhood"],
            home["beds"],
            home["baths"],
            home["sqft"],
            home["url"],
            today,
        ]
        for home in listings
        if home["url"] not in existing_urls
    ]

    if new_rows:
        worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")
    print(
        f"Added {len(new_rows)} new listings, "
        f"skipped {len(listings) - len(new_rows)} already tracked."
    )


if __name__ == "__main__":
    main()
