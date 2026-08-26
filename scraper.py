#!/usr/bin/env python3

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


BASE_URL = "https://www.abilenenissan.com"

INVENTORY_SECTIONS = {
    "new": "/inventory/new/nissan/",
    "used": "/inventory/used/",
}

MAX_PAGES_SAFETY = 25
MAX_SNAPSHOTS_KEPT = 60

DATA_DIR = Path(__file__).parent / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"


def clean(text):
    if not text:
        return None
    return re.sub(r"\s+", " ", text).strip()


def find_first(patterns, text, flags=re.I):
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return clean(match.group(1))
    return None


def money_to_int(value):
    if not value:
        return None

    value = re.sub(r"[^\d]", "", value)

    try:
        return int(value)
    except ValueError:
        return None


def number_to_int(value):
    if not value:
        return None

    value = value.replace(",", "").strip()

    try:
        return int(value)
    except ValueError:
        return None


def get_total_results(text):
    match = re.search(
        r"Results:\s*([\d,]+)\s*Vehicles",
        text,
        re.I,
    )

    if not match:
        return None

    return int(match.group(1).replace(",", ""))


def extract_vehicle_links(page):
    """
    Pull vehicle detail URLs from the rendered inventory page.
    The VIN is embedded in Clark Nissan's /viewdetails/ URLs.
    """

    links = page.locator('a[href*="/viewdetails/"]')

    results = []

    for i in range(links.count()):
        href = links.nth(i).get_attribute("href")

        if not href:
            continue

        href = urljoin(BASE_URL, href)

        match = re.search(
            r"/viewdetails/(?:new|used)/([A-HJ-NPR-Z0-9]{17})",
            href,
            re.I,
        )

        if not match:
            continue

        vin = match.group(1).upper()

        results.append({
            "vin": vin,
            "url": href,
        })

    # Deduplicate by VIN
    unique = {}

    for vehicle in results:
        unique[vehicle["vin"]] = vehicle

    return list(unique.values())


def parse_vehicle_detail(page, vehicle, inventory_type):
    """
    Visit a vehicle detail page and pull the important inventory fields.
    """

    url = vehicle["url"]
    vin = vehicle["vin"]

    print(f"    {vin}")

    try:
        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=45000,
        )

        # Give JavaScript inventory widgets time to populate.
        page.wait_for_timeout(1500)

    except PlaywrightTimeoutError:
        print(f"      WARNING: timeout loading {url}", file=sys.stderr)

    try:
        body = clean(page.locator("body").inner_text(timeout=10000)) or ""
    except Exception:
        body = ""

    # --------------------------
    # Vehicle title
    # --------------------------

    title = None

    try:
        headings = page.locator("h1, h2, h3")

        for i in range(min(headings.count(), 30)):
            candidate = clean(headings.nth(i).inner_text())

            if candidate and re.search(r"\b20\d{2}\b", candidate):
                title = candidate
                break

    except Exception:
        pass

    # Fallback to page title
    if not title:
        try:
            page_title = clean(page.title())

            match = re.search(
                r"(20\d{2}\s+.+?)(?:\s+for Sale|\s+\||$)",
                page_title,
                re.I,
            )

            if match:
                title = clean(match.group(1))

        except Exception:
            pass

    # --------------------------
    # Break title into fields
    # --------------------------

    year = None
    make = None
    model = None
    trim = None

    if title:
        match = re.search(
            r"\b(20\d{2}|19\d{2})\s+([A-Za-z]+)\s+([^\s]+)(?:\s+(.+))?",
            title,
        )

        if match:
            year = match.group(1)
            make = match.group(2)
            model = match.group(3)
            trim = clean(match.group(4))

    # --------------------------
    # Stock number
    # --------------------------

    stock = find_first(
        [
            r"Stock\s*#\s*:?\s*([A-Z0-9\-]+)",
            r"Stock\s*No\.?\s*:?\s*([A-Z0-9\-]+)",
            r"Stock Number\s*:?\s*([A-Z0-9\-]+)",
        ],
        body,
    )

    # --------------------------
    # Mileage
    # --------------------------

    mileage = find_first(
        [
            r"Mileage\s*:?\s*([\d,]+)",
            r"([\d,]+)\s*(?:miles|mi)\b",
        ],
        body,
    )

    mileage = number_to_int(mileage)

    # New vehicles should normally be effectively zero mileage.
    if inventory_type == "new" and mileage and mileage > 10000:
        mileage = None

    # --------------------------
    # Price
    # --------------------------

    price = find_first(
        [
            r"Your Price\s*\$([\d,]+)",
            r"Final Price\s*\$([\d,]+)",
            r"Selling Price\s*\$([\d,]+)",
            r"Internet Price\s*\$([\d,]+)",
        ],
        body,
    )

    final_price = money_to_int(price)

    # --------------------------
    # MSRP
    # --------------------------

    msrp = find_first(
        [
            r"MSRP\s*\$([\d,]+)",
        ],
        body,
    )

    msrp = money_to_int(msrp)

    # --------------------------
    # Colors
    # --------------------------

    exterior = find_first(
        [
            r"Exterior\s*:?\s*(.+?)\s+Interior\b",
            r"Exterior Color\s*:?\s*(.+?)\s+Interior",
        ],
        body,
    )

    interior = find_first(
        [
            r"Interior\s*:?\s*(.+?)\s+(?:Transmission|Engine|Drivetrain|Fuel)",
            r"Interior Color\s*:?\s*(.+?)\s+(?:Transmission|Engine|Drivetrain|Fuel)",
        ],
        body,
    )

    # Prevent accidental giant chunks of page text
    if exterior and len(exterior) > 80:
        exterior = None

    if interior and len(interior) > 80:
        interior = None

    # --------------------------
    # Transmission
    # --------------------------

    transmission = find_first(
        [
            r"Transmission\s*:?\s*(.+?)\s+(?:Engine|Drivetrain|Fuel|VIN|Stock)",
        ],
        body,
    )

    if transmission and len(transmission) > 80:
        transmission = None

    # --------------------------
    # Certified / Carfax flags
    # --------------------------

    lower_body = body.lower()

    certified = (
        "certified pre-owned" in lower_body
        or "certified pre owned" in lower_body
        or "nissan certified" in lower_body
    )

    carfax_one_owner = (
        "carfax one owner" in lower_body
        or "one owner" in lower_body
    )

    # --------------------------
    # Body type from URL slug
    # --------------------------

    body_type = None

    slug = url.lower()

    body_types = {
        "sport-utility": "sport utility",
        "suv": "suv",
        "crew-cab": "crew cab pickup",
        "pickup": "pickup",
        "sedan": "sedan",
        "hatchback": "hatchback",
        "crossover": "crossover",
        "van": "van",
        "convertible": "convertible",
    }

    for slug_piece, readable in body_types.items():
        if slug_piece in slug:
            body_type = readable
            break

    return {
        "vin": vin,
        "stock": stock,
        "year": year,
        "make": make,
        "model": model,
        "trim": trim,
        "title": title,
        "body_type": body_type,
        "inventory_type": inventory_type,
        "certified": certified,
        "carfax_one_owner": carfax_one_owner,
        "selling_price": final_price,
        "final_price": final_price,
        "msrp": msrp,
        "mileage": mileage,
        "exterior_color": exterior,
        "interior_color": interior,
        "transmission": transmission,
        "url": url,
    }


def scrape_section(browser, path, inventory_type):
    """
    Render each inventory results page with Chromium, collect detail-page
    URLs, then visit those pages to gather vehicle data.
    """

    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1440, "height": 1000},
    )

    listing_page = context.new_page()

    vehicle_links = {}
    total_expected = None

    for page_number in range(1, MAX_PAGES_SAFETY + 1):

        params = {
            "paymenttype": "cash",
            "instock": "true",
            "intransit": "true",
            "inproduction": "true",
            "page": page_number,
        }

        url = f"{BASE_URL}{path}?{urlencode(params)}"

        print(f"  Loading page {page_number}: {url}")

        try:
            listing_page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=45000,
            )

            # Let the site's JavaScript inventory component load.
            listing_page.wait_for_timeout(3000)

            # Scroll to trigger lazy-loaded inventory cards.
            listing_page.evaluate(
                """
                async () => {
                    for (let i = 0; i < 8; i++) {
                        window.scrollTo(0, document.body.scrollHeight);
                        await new Promise(r => setTimeout(r, 400));
                    }
                    window.scrollTo(0, 0);
                }
                """
            )

            listing_page.wait_for_timeout(1000)

        except PlaywrightTimeoutError:
            print(
                f"  WARNING: inventory page {page_number} timed out",
                file=sys.stderr,
            )

        body_text = clean(listing_page.locator("body").inner_text()) or ""

        if total_expected is None:
            total_expected = get_total_results(body_text)

            if total_expected:
                print(f"  Site reports {total_expected} {inventory_type} vehicles.")

        links = extract_vehicle_links(listing_page)

        new_count = 0

        for vehicle in links:
            if vehicle["vin"] not in vehicle_links:
                vehicle_links[vehicle["vin"]] = vehicle
                new_count += 1

        print(
            f"  Page {page_number}: +{new_count} unique vehicles "
            f"(total links: {len(vehicle_links)})"
        )

        # Stop if this page added nothing new.
        if new_count == 0:
            break

        # Stop once we've collected the advertised number.
        if total_expected and len(vehicle_links) >= total_expected:
            break

    listing_page.close()

    print(
        f"  Found {len(vehicle_links)} unique "
        f"{inventory_type} vehicle URLs."
    )

    # Now visit vehicle detail pages.
    detail_page = context.new_page()

    vehicles = []

    for vehicle in vehicle_links.values():
        try:
            parsed = parse_vehicle_detail(
                detail_page,
                vehicle,
                inventory_type,
            )

            vehicles.append(parsed)

        except Exception as exc:
            print(
                f"    ERROR parsing {vehicle['vin']}: {exc}",
                file=sys.stderr,
            )

            # Keep the VIN and URL even if some detail parsing fails.
            vehicles.append({
                "vin": vehicle["vin"],
                "inventory_type": inventory_type,
                "url": vehicle["url"],
            })

    detail_page.close()
    context.close()

    return vehicles


def save_snapshot(all_vehicles):
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    scraped_at = now.isoformat()
    date_str = now.strftime("%Y-%m-%d")

    snapshot = {
        "dealership": "Clark Nissan of Abilene",
        "source_url": BASE_URL,
        "scraped_at": scraped_at,
        "vehicle_count": len(all_vehicles),
        "vehicles": all_vehicles,
    }

    snapshot_path = SNAPSHOTS_DIR / f"{date_str}.json"

    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)

    latest_path = DATA_DIR / "latest.json"

    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)

    manifest_path = DATA_DIR / "manifest.json"

    manifest = []

    if manifest_path.exists():
        try:
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            manifest = []

    # Replace today's entry if workflow runs more than once.
    manifest = [
        item
        for item in manifest
        if item.get("date") != date_str
    ]

    manifest.append({
        "date": date_str,
        "file": f"snapshots/{date_str}.json",
        "count": len(all_vehicles),
        "scraped_at": scraped_at,
    })

    manifest.sort(
        key=lambda item: item["date"],
        reverse=True,
    )

    # Delete snapshots beyond retention limit.
    for old in manifest[MAX_SNAPSHOTS_KEPT:]:
        old_file = DATA_DIR / old["file"]

        if old_file.exists():
            old_file.unlink()

    manifest = manifest[:MAX_SNAPSHOTS_KEPT]

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(
        f"\nDone. {len(all_vehicles)} vehicles saved for {date_str}."
    )


def main():
    all_vehicles = []

    with sync_playwright() as playwright:

        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        try:
            for inventory_type, path in INVENTORY_SECTIONS.items():

                print(
                    f"\nScraping {inventory_type} inventory..."
                )

                try:
                    vehicles = scrape_section(
                        browser,
                        path,
                        inventory_type,
                    )

                    all_vehicles.extend(vehicles)

                    print(
                        f"  Scraped {len(vehicles)} "
                        f"{inventory_type} vehicles."
                    )

                except Exception as exc:
                    print(
                        f"ERROR scraping {inventory_type}: {exc}",
                        file=sys.stderr,
                    )

        finally:
            browser.close()

    # Important safety check:
    # never overwrite good inventory with an empty scrape.
    if not all_vehicles:
        print(
            "\nNo vehicles scraped — aborting without "
            "touching stored inventory.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Remove duplicate VINs across all sections.
    deduped = {}

    for vehicle in all_vehicles:
        vin = vehicle.get("vin")

        if vin:
            deduped[vin] = vehicle

    all_vehicles = list(deduped.values())

    save_snapshot(all_vehicles)


if __name__ == "__main__":
    main()
