#!/usr/bin/env python3
"""
Clark Nissan of Abilene - inventory scraper
=============================================

Pulls new + used inventory from abilenenissan.com and writes it into
data/snapshots/<date>.json, updates data/latest.json, and updates
data/manifest.json (a list of all snapshots taken so far).

This is designed to be run by the GitHub Actions workflow in
.github/workflows/scrape.yml on a schedule, so the site at
data/*.json stays current with zero manual work. You can also just
run it locally the same way:

    pip install requests beautifulsoup4
    python scraper.py

NOTE ON RELIABILITY
--------------------
This scrapes public HTML. If the dealer site changes its page
structure, this may need small selector tweaks in parse_vehicle_block().
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.abilenenissan.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
INVENTORY_SECTIONS = {"new": "/inventory/new", "used": "/inventory/used"}
MAX_PAGES_SAFETY = 25
MAX_SNAPSHOTS_KEPT = 60  # keep repo size sane

DATA_DIR = Path(__file__).parent / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"


def fetch_page(path, page=1):
    url = f"{BASE_URL}{path}"
    params = {
        "paymenttype": "cash", "instock": "true", "intransit": "true",
        "inproduction": "true", "page": page,
    }
    resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    return resp.text


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


def scrape_section(path, inventory_type):
    vehicles, seen_vins, page, total_expected = [], set(), 1, None
    while page <= MAX_PAGES_SAFETY:
        html = fetch_page(path, page)
        soup = BeautifulSoup(html, "html.parser")
        if total_expected is None:
            total_expected = get_total_results(soup)
        anchors = [a for a in soup.find_all("a", href=True) if "/viewdetails/" in a["href"]]
        if not anchors:
            break
        new_count = 0
        for a in anchors:
            vehicle = parse_vehicle_block(a, inventory_type)
            key = vehicle["vin"] or vehicle["url"]
            if key in seen_vins:
                continue
            seen_vins.add(key)
            vehicles.append(vehicle)
            new_count += 1
        print(f"  {path} page {page}: +{new_count} (total {len(vehicles)})")
        if new_count == 0 or (total_expected and len(vehicles) >= total_expected):
            break
        page += 1
        time.sleep(1)
    return vehicles


def main():
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    all_vehicles = []
    for inv_type, path in INVENTORY_SECTIONS.items():
        print(f"Scraping {inv_type} inventory...")
        try:
            all_vehicles.extend(scrape_section(path, inv_type))
        except requests.RequestException as e:
            print(f"  ERROR scraping {path}: {e}", file=sys.stderr)

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
