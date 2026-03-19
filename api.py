"""Xanterra API client for Yellowstone lodge availability."""

import json
import logging
import urllib.parse
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_BASE_URL = "https://webapi.xanterra.net/v1/api"
_PROPERTY = "yellowstonenationalparklodges"

# Fallback names for known lodge codes (rooms API returns 404)
_KNOWN_NAMES = {
    "YLCL": "Canyon Lodge",
    "YLGV": "Grant Village Lodge",
    "YLMH": "Mammoth Hotel",
    "YLLH": "Lake Hotel",
    "YLLL": "Lake Lodge",
    "YLOI": "Old Faithful Inn",
    "YLOL": "Old Faithful Lodge",
    "YLOS": "Old Faithful Snow Lodge",
    "YLRL": "Roosevelt Lodge",
}


def _fetch_json(url: str, params: dict, timeout: int) -> dict:
    """Fetch a JSON URL using a real Chromium browser to bypass Cloudflare."""
    from playwright.sync_api import sync_playwright

    full_url = url + "?" + urllib.parse.urlencode(params)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            extra_http_headers={
                "Origin": "https://secure.yellowstonenationalparklodges.com",
                "Referer": "https://secure.yellowstonenationalparklodges.com/",
                "Accept": "application/json",
            }
        )
        page = context.new_page()
        page.goto(full_url, wait_until="networkidle", timeout=timeout * 1000)
        text = page.inner_text("body")
        browser.close()

    return json.loads(text)


def fetch_monthly_availability(year: int, month: int, nights: int, timeout: int = 60) -> dict:
    """GET availability for the given month.

    Returns a dict keyed by ISO date string (YYYY-MM-DD), values are
    dicts of {hotel_code: hotel_info}.
    Raises ValueError if response is malformed.
    """
    url = f"{_BASE_URL}/availability/hotels/{_PROPERTY}"
    params = {
        "date": f"{year:04d}-{month:02d}-01",
        "limit": 31,
        "rate_code": "INTERNET",
        "nights": nights,
    }
    data = _fetch_json(url, params, timeout)
    if "availability" not in data:
        raise ValueError(
            f"Unexpected API response: missing 'availability' key. Got: {list(data.keys())}"
        )

    # Convert MM/DD/YYYY date keys to YYYY-MM-DD
    converted = {}
    for date_str, hotels in data["availability"].items():
        try:
            m, d, y = date_str.split("/")
            iso = f"{y}-{m}-{d}"
        except ValueError:
            iso = date_str  # already ISO or unknown format; keep as-is
        converted[iso] = hotels

    return converted


def get_lodge_names() -> dict:
    """Return {hotel_code: lodge_name} using known names."""
    return dict(_KNOWN_NAMES)


def build_booking_url(hotel_code: str, check_in: str, nights: int) -> str:
    """Return a direct booking URL for the given hotel and dates."""
    return (
        f"https://secure.yellowstonenationalparklodges.com/booking/lodging-select"
        f"?dateFrom={check_in}&nights={nights}&destination={hotel_code}"
    )


def find_available_lodges(check_in: str, check_out: str, lodge_filter) -> list:
    """Find lodges available for all nights between check_in and check_out.

    lodge_filter: the string "any" or a list of hotel codes.
    Returns list of dicts: {hotel_code, lodge_name, check_in, check_out, nights, booking_url}
    """
    ci = date.fromisoformat(check_in)
    co = date.fromisoformat(check_out)
    nights = (co - ci).days
    if nights <= 0:
        raise ValueError(f"check_out ({check_out}) must be after check_in ({check_in})")

    # Collect all (year, month) pairs spanned by the stay
    months_needed = set()
    d = ci
    while d < co:
        months_needed.add((d.year, d.month))
        if d.month == 12:
            d = date(d.year + 1, 1, 1)
        else:
            d = date(d.year, d.month + 1, 1)

    # Fetch availability for each month and merge
    merged: dict[str, dict] = {}
    for year, month in sorted(months_needed):
        logger.info("Fetching availability for %04d-%02d (nights=%d)...", year, month, nights)
        monthly = fetch_monthly_availability(year, month, nights)
        merged.update(monthly)

    lodge_names = get_lodge_names()

    # Build set of ISO dates that must be available (check_in night through last night)
    required_dates = set()
    current = ci
    while current < co:
        required_dates.add(current.isoformat())
        current += timedelta(days=1)

    # For each hotel, track which required dates it's open with rooms available
    hotel_dates: dict[str, set] = {}
    for date_str, hotels in merged.items():
        if date_str not in required_dates:
            continue
        for hotel_code, info in hotels.items():
            if ":RV" in hotel_code:
                continue
            status = info.get("status", "")
            per_guests = info.get("perGuests", {})
            has_rooms = any(
                guest_info.get("a", 0) > 0
                for guest_info in per_guests.values()
            )

            if status == "OPEN" and has_rooms:
                hotel_dates.setdefault(hotel_code, set()).add(date_str)

    # Only include hotels available on ALL required dates
    results = []
    for hotel_code, avail_dates in hotel_dates.items():
        if not required_dates.issubset(avail_dates):
            continue
        if lodge_filter != "any" and hotel_code not in lodge_filter:
            continue

        lodge_name = lodge_names.get(hotel_code, hotel_code)
        results.append({
            "hotel_code": hotel_code,
            "lodge_name": lodge_name,
            "check_in": check_in,
            "check_out": check_out,
            "nights": nights,
            "booking_url": build_booking_url(hotel_code, check_in, nights),
        })

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from datetime import date as _date
    today = _date.today()
    test_year = today.year if today.month < 7 else today.year + 1
    print(f"Fetching availability for {test_year}-07 (1 night)...")
    avail = fetch_monthly_availability(test_year, 7, 1)
    dates = sorted(avail.keys())
    print(f"Got {len(dates)} dates. First: {dates[0] if dates else 'none'}")
    if dates:
        sample_date = dates[0]
        hotels = avail[sample_date]
        non_rv = {k: v for k, v in hotels.items() if ":RV" not in k}
        print(f"Non-RV hotels on {sample_date}: {list(non_rv.keys())}")
        for code, info in non_rv.items():
            guests = info.get("perGuests", {})
            total_avail = sum(g.get("a", 0) for g in guests.values())
            print(f"  {code}: status={info.get('status')} available_rooms_sum={total_avail}")
