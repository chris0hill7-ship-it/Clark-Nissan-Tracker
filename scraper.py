#!/usr/bin/env python3
"""
Clark Nissan of Abilene - inventory scraper
=============================================

Pulls new + used inventory from abilenenissan.com and writes it into
data/snapshots/<date>.json, updates data/latest.json, and updates
data/manifest.json (a list of all snapshots taken so far).

The dealer site loads its vehicle listings with JavaScript after the
initial page load, so this uses Playwright (a real headless browser)
to render each page before reading it, rather than a plain HTTP request.

    pip install playwright beautifulsoup4
    playwright install --with-deps chromium
    python scraper.py

NOTE ON RELIABILITY
--------------------
If the dealer site changes its page structure, this may need small
selector tweaks in parse_vehicle_block().
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.abilenenissan.com"
INVENTORY_SECTIONS = {"new": "/inventory/new", "used": "/inventory/used"}
MAX_PAGES_SAFETY = 25
MAX_SNAPSHOTS_KEPT = 60  # keep repo size sane

DATA_DIR = Path(__file__).parent / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"


def get_total_results(soup):
    match = re.search(r"Results:\s*([\d,]+)\s*Vehicles", soup.get_text(" "), re.I)
    return int(match.group(1).replace(",", "")) if match else None


def parse_vehicle_block(anchor, inventory_type):
    href = anchor.get("href", "")
    text = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True))

    def find(pattern, flags=0):
        m = re.search(pattern, text, flags)
        return m.group(1).strip() if m else None

    vin = find(r"\bvin([A-HJ-NPR-Z0-9]{17})\b")
    stock = find(r"Stock #([A-Za-z0-9\-]+)")
    final_price = find(r"Final Price\$?([\d,]+)")
    selling_price = find(r"Selling Price\$?([\d,]+)")
    exterior = find(r"Exterior(.+?)Interior")
    interior = find(r"Interior(.+?)Transmission")
    transmission = find(r"Transmission(\w+)")
    mileage = find(r"Mileage\s*([\d,]+)\s*Miles")
    certified = "certified logo" in text.lower() or "/cpo/" in href
    carfax_one_owner = "carfax one owner" in text.lower()

    title_blob = text.split("Final Price")[0].strip()
    half = len(title_blob) // 2
    first_half, second_half = title_blob[:half].strip(), title_blob[half:].strip()
    title = first_half if first_half and first_half == second_half else title_blob

    year = make = model = trim = None
    m = re.match(r"(\d{4})\s+(\S+)\s+(.+)", title)
    if m:
        year, make = m.group(1), m.group(2)
        rest = m.group(3).strip().split(" ", 1)
        model = rest[0]
        trim = rest[1] if len(rest) > 1 else None

    body_type = None
    slug_match = re.search(r"/viewdetails/[^/]+/[^/]+/[^/]+", href)
    if slug_match:
        body_match = re.search(
            r"(sport-utility|pickup|4dr-car|hatchback|crew-cab-pickup|van|convertible)",
            slug_match.group(0),
        )
        if body_match:
            body_type = body_match.group(1).replace("-", " ")

    return {
        "vin": vin, "stock": stock, "year": year, "make": make, "model": model,
        "trim": trim, "body_type": body_type, "inventory_type": inventory_type,
        "certified": certified, "carfax_one_owner": carfax_one_owner,
        "selling_price": int(selling_price.replace(",", "")) if selling_price else None,
        "final_price": int(final_price.replace(",", "")) if final_price else None,
        "mileage": int(mileage.replace(",", "")) if mileage else None,
        "exterior_color": exterior, "interior_color": interior,
        "transmission": transmission,
        "url": BASE_URL + href if href.startswith("/") else href,
    }


def scrape_vehicle_features(page, url):
    """Visit a single vehicle's detail page and pull its feature/equipment list."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(800)
        soup = BeautifulSoup(page.content(), "html.parser")
    except Exception as e:
        print(f"    could not load {url}: {e}", file=sys.stderr)
        return []

    features = set()
    heading_re = re.compile(r"features|options|equipment", re.I)

    # Strategy 1: headings that mention features/options, grab list items nearby
    for tag in ["h1", "h2", "h3", "h4", "h5", "h6", "button", "span", "div"]:
        for heading in soup.find_all(tag, string=heading_re):
            container = heading.find_parent(["div", "section"]) or heading.parent
            if not container:
                continue
            for li in container.find_all("li"):
                t = li.get_text(" ", strip=True)
                if t and len(t) < 80:
                    features.add(t)

    # Strategy 2: any element whose class/id mentions "feature" or "option"
    for el in soup.find_all(attrs={"class": re.compile(r"feature|option", re.I)}):
        for li in el.find_all("li"):
            t = li.get_text(" ", strip=True)
            if t and len(t) < 80:
                features.add(t)
    for el in soup.find_all(attrs={"id": re.compile(r"feature|option", re.I)}):
        for li in el.find_all("li"):
            t = li.get_text(" ", strip=True)
            if t and len(t) < 80:
                features.add(t)

    return sorted(features)


def scrape_section(page, path, inventory_type):
    vehicles, seen_vins = [], set()
    total_expected = None

    for page_num in range(1, MAX_PAGES_SAFETY + 1):
        url = (
            f"{BASE_URL}{path}?paymenttype=cash&instock=true"
            f"&intransit=true&inproduction=true&page={page_num}"
        )
        page.goto(url, wait_until="domcontentloaded", timeout=45000)

        try:
            page.wait_for_selector('a[href*="/viewdetails/"]', timeout=15000)
        except Exception:
            print(f"  {path} page {page_num}: no vehicle links appeared, stopping")
            break

        page.wait_for_timeout(1000)  # let any lazy-loaded content settle

        soup = BeautifulSoup(page.content(), "html.parser")
        if total_expected is None:
            total_expected = get_total_results(soup)

        anchors = [a for a in soup.find_all("a", href=True) if "/viewdetails/" in a["href"]]
        new_count = 0
        for a in anchors:
            vehicle = parse_vehicle_block(a, inventory_type)
            key = vehicle["vin"] or vehicle["url"]
            if key in seen_vins:
                continue
            seen_vins.add(key)
            vehicles.append(vehicle)
            new_count += 1

        print(f"  {path} page {page_num}: +{new_count} (total {len(vehicles)})")

        if new_count == 0:
            break
        if total_expected and len(vehicles) >= total_expected:
            break

        time.sleep(1)

    return vehicles


def main():
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    all_vehicles = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        )
        for inv_type, path in INVENTORY_SECTIONS.items():
            print(f"Scraping {inv_type} inventory...")
            try:
                all_vehicles.extend(scrape_section(page, path, inv_type))
            except Exception as e:
                print(f"  ERROR scraping {path}: {e}", file=sys.stderr)

        print(f"\nFetching feature details for {len(all_vehicles)} vehicles...")
        for i, vehicle in enumerate(all_vehicles, 1):
            if not vehicle.get("url"):
                vehicle["features"] = []
                continue
            vehicle["features"] = scrape_vehicle_features(page, vehicle["url"])
            if i % 10 == 0 or i == len(all_vehicles):
                print(f"  {i}/{len(all_vehicles)} vehicles processed")
            time.sleep(0.5)

        browser.close()

    if not all_vehicles:
        print("No vehicles scraped — aborting without touching stored data.", file=sys.stderr)
        sys.exit(1)

    scraped_at = datetime.now(timezone.utc).isoformat()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot = {
        "dealership": "Clark Nissan of Abilene",
        "source_url": BASE_URL,
        "scraped_at": scraped_at,
        "vehicle_count": len(all_vehicles),
        "vehicles": all_vehicles,
    }

    snapshot_path = SNAPSHOTS_DIR / f"{date_str}.json"
    with open(snapshot_path, "w") as f:
        json.dump(snapshot, f, indent=2)

    latest_path = DATA_DIR / "latest.json"
    with open(latest_path, "w") as f:
        json.dump(snapshot, f, indent=2)

    manifest_path = DATA_DIR / "manifest.json"
    manifest = []
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            manifest = []
    manifest = [m for m in manifest if m.get("date") != date_str]
    manifest.append({"date": date_str, "file": f"snapshots/{date_str}.json",
                      "count": len(all_vehicles), "scraped_at": scraped_at})
    manifest.sort(key=lambda m: m["date"], reverse=True)

    for old in manifest[MAX_SNAPSHOTS_KEPT:]:
        old_file = DATA_DIR / old["file"]
        if old_file.exists():
            old_file.unlink()
    manifest = manifest[:MAX_SNAPSHOTS_KEPT]

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nDone. {len(all_vehicles)} vehicles saved for {date_str}.")


if __name__ == "__main__":
    main()
