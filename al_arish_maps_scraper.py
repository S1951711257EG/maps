#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Al Arish (North Sinai, Egypt) - Google Maps business scraper.

Collects Restaurants, Grocery stores, Pharmacies, Supermarkets, Seafood
markets, Bakeries, Butcher shops, Fish restaurants etc. for Al Arish city.

For every listing it saves (whatever Google Maps exposes):
    category, name, address, latitude, longitude, phone,
    website, email (harvested from the business website when reachable),
    rating, review count, plus code and the Google Maps URL.

Two modes
---------
1) Browser mode (default): a headless Chromium (Playwright) opens Google
   Maps, scrolls the result feed for each category and opens every place
   page to read its details.
       pip install playwright requests
       python -m playwright install chromium
       python al_arish_maps_scraper.py

2) API mode (optional, most reliable & ToS-compliant): official Google
   Places API.  Needs a key with "Places API (New)" enabled:
       python al_arish_maps_scraper.py --api-key YOUR_KEY

Google Colab (plain .py file - no notebook needed)
--------------------------------------------------
The script is Colab-ready and self-installs everything it needs
(playwright, chromium, nest_asyncio) on first run:

    # upload this file, then in a Colab cell:
    !python al_arish_maps_scraper.py --per-category 20

Both launch styles work: `!python ...` (fresh process) and `%run ...`
(inside the kernel - the script detects the already-running event loop
and applies nest_asyncio automatically). Output files land in /content;
grab them from the file browser in the left sidebar. Skip the self-setup
with --no-setup once you have pre-installed the dependencies yourself.

Useful flags
------------
    --per-category 20                  limit listings per category (default 60)
    --categories restaurant,pharmacy   only run some categories
    --no-email                         skip the e-mail harvesting step
    --headed                           show the browser window (debug)
    --out results/al_arish             output base name -> .csv / .json / _raw.jsonl
    --dry-run                          just print the planned searches and exit
    --no-setup                         skip automatic dependency installation

Outputs
-------
    <out>.json         clean merged data (UTF-8)
    <out>.csv          same data for Excel (UTF-8 BOM)
    <out>_raw.jsonl    one JSON record per place, written while scraping
                       (crash-safe; keeps partial progress)

Fair-use notes
--------------
Google Maps is heavily protected: keep --per-category modest, leave the
built-in random delays in place, and prefer --api-key for large runs.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import glob
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests

# --------------------------------------------------------------------------- #
# Configuration                                                                #
# --------------------------------------------------------------------------- #

CITY = "Al Arish, North Sinai, Egypt"
# Al Arish town centre (taken from the user's map link) + a zoom that covers town
LAT0, LNG0, ZOOM = 31.132093, 33.8032762, 13

CATEGORIES = {
    "restaurant":     "Restaurants",
    "fish_restaurant": "Fish restaurants",
    "grocery":        "Grocery stores",
    "supermarket":    "Supermarkets",
    "pharmacy":       "Pharmacies",
    "seafood_market": "Seafood markets",
    "bakery":         "Bakeries",
    "butcher_shop":   "Butcher shops",
    "fruit_veg":      "Fruit and vegetable stores",
    "cafe":           "Cafes",
}

OUTPUT_COLUMNS = [
    "category", "name", "address", "latitude", "longitude", "phone",
    "email", "website", "rating", "reviews", "plus_code", "maps_url",
    "scraped_at",
]

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--lang=en-US",
]

STEALTH_JS = ("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
              "Object.defineProperty(navigator,'languages',{get:()=>['en-US','en','ar']});")

FEED_SEL = 'div[role="feed"]'
FEED_LINK_SEL = 'div[role="feed"] a[href*="/maps/place/"]'
END_TEXT = "You've reached the end of the list"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
EMAIL_JUNK = ("example.", "sentry", "wixpress", "schema", "yourdomain",
              "domain.com", "email.com", "test.com", "godaddy", "@2x",
              "squarespace", "wix.com", "yoast", " protection", "cloudflare")
EMAIL_BAD_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css",
                 ".js", ".ico", ".woff", ".mp4")
SOCIAL_SKIP = ("facebook.com", "instagram.com", "whatsapp.com", "twitter.com",
               "x.com", "tiktok.com", "youtube.com", "linktr.ee", "waze.com",
               "google.com", "goo.gl")

# Google Colab / Jupyter detection (affects setup hints and event-loop handling)
IN_COLAB = bool(os.environ.get("COLAB_RELEASE_TAG")) or os.path.isdir("/content")

# Google Places API (New) -----------------------------------------------------
API_TEXT_SEARCH = "https://places.googleapis.com/v1/places:searchText"
API_FIELD_MASK = ("places.id,places.displayName,places.formattedAddress,"
                  "places.location,places.internationalPhoneNumber,"
                  "places.websiteUri,places.rating,places.userRatingCount,"
                  "places.googleMapsUri")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- #
# Small helpers                                                                #
# --------------------------------------------------------------------------- #

def search_url(label: str, lang: str) -> str:
    q = quote_plus(f"{label} in {CITY}")
    return (f"https://www.google.com/maps/search/{q}/"
            f"@{LAT0},{LNG0},{ZOOM}z?hl={lang}")


def parse_latlng(url: str):
    """Pull latitude/longitude out of a Google Maps place URL."""
    m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", url)
    if not m:
        m = re.search(r"/@(-?\d+\.\d+),(-?\d+\.\d+)", url)
    if m:
        return m.group(1), m.group(2)
    return "", ""


async def grab_text(page, selectors, timeout: int = 3000) -> str:
    """Try several CSS selectors, return first non-empty inner_text."""
    for sel in selectors:
        try:
            txt = (await page.locator(sel).first.inner_text(timeout=timeout) or "").strip()
            txt = re.sub(r"\s+", " ", txt).strip()   # collapse newlines/tabs
            if txt:
                return txt
        except Exception:
            continue
    return ""


async def check_block(page):
    """Raise a friendly error if Google shows a captcha / block page."""
    try:
        html = (await page.content()).lower()
    except Exception:
        return
    if "unusual traffic" in html or "recaptcha" in html:
        raise RuntimeError(
            "Google is showing a captcha / 'unusual traffic' block page. "
            "Wait a while, lower --per-category, or use --api-key instead.")


async def handle_consent(page):
    """Click the cookie-consent button when Google shows one."""
    for label in ("Accept all", "I agree", "Reject all"):
        try:
            btn = page.get_by_role("button", name=label)
            if await btn.count():
                await btn.first.click(timeout=3000)
                await page.wait_for_load_state("domcontentloaded")
                return
        except Exception:
            continue


def clean_emails(raw) -> list:
    out = []
    for e in raw:
        el = e.lower().strip(".")
        if len(el) > 60 or len(el) < 6:
            continue
        if any(el.endswith(x) for x in EMAIL_BAD_EXT):
            continue
        if any(p in el for p in EMAIL_JUNK):
            continue
        if el not in out:
            out.append(el)
    return out


def harvest_email(site: str) -> str:
    """Fetch the business website homepage (+ /contact) and regex e-mails."""
    if not site or any(s in site.lower() for s in SOCIAL_SKIP):
        return ""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9,ar;q=0.8"}
    found: list = []
    for url in (site, site.rstrip("/") + "/contact"):
        try:
            r = requests.get(url, headers=headers, timeout=8, allow_redirects=True)
            if r.status_code != 200 or not r.text:
                continue
            found = clean_emails(EMAIL_RE.findall(r.text))
        except Exception:
            continue
        if found:
            break
    return "; ".join(found[:3])


# --------------------------------------------------------------------------- #
# Environment setup - makes the script run on Google Colab as-is               #
# --------------------------------------------------------------------------- #

def _pip_install(packages: list) -> bool:
    """pip install quietly; retry with --break-system-packages on PEP668 boxes."""
    cmd = [sys.executable, "-m", "pip", "install", "-q"] + packages
    print("Installing python package(s):", " ".join(packages))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and "externally-managed-environment" in (r.stderr or ""):
        r = subprocess.run(cmd + ["--break-system-packages"],
                           capture_output=True, text=True)
    if r.returncode != 0:
        print((r.stderr or r.stdout or "").strip()[-800:])
        return False
    return True


def chromium_cached() -> bool:
    """True when a playwright chromium build is already on disk."""
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not root:
        if sys.platform == "darwin":
            root = os.path.expanduser("~/Library/Caches/ms-playwright")
        elif os.name == "nt":
            root = os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright")
        else:
            root = os.path.expanduser("~/.cache/ms-playwright")
    # chrome-linux/ (older) or chrome-linux64/ (newer) - both accepted
    return bool(glob.glob(os.path.join(root, "chromium-*", "chrome-linux*", "chrome")))


def ensure_deps(packages_only: bool = False):
    """Self-install python packages (+ chromium browser) when missing.

    This is what makes the script work on a fresh Google Colab VM without
    any manual pip/install steps. On a machine that already has everything
    every check below is a cheap no-op. Skip with --no-setup.
    """
    missing = []
    for pkg, mod in (("playwright", "playwright"), ("requests", "requests"),
                     ("nest_asyncio", "nest_asyncio")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing and not _pip_install(missing):
        sys.exit("Automatic install failed - run manually:\n"
                 "    pip install playwright requests nest_asyncio")

    if packages_only or chromium_cached():
        return
    print("Downloading chromium for playwright (one-time, ~150 MB)...")
    if subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                      check=False).returncode != 0:
        print("  !! chromium download failed - run: "
              "python -m playwright install chromium")
        return
    if os.name == "posix":
        # A fresh download on Linux (e.g. Google Colab) still needs chromium's
        # system libraries (libatk, libnss3, ...). Colab runs as root so this
        # just works; elsewhere it may need sudo - failure is not fatal here,
        # the launcher retries install-deps if the first launch fails.
        print("Installing chromium system libraries "
              "(one-time, ~2-4 min; needs root/sudo)...")
        subprocess.run([sys.executable, "-m", "playwright", "install-deps",
                        "chromium"], check=False)


def _is_missing_libs_error(exc: Exception) -> bool:
    """True when chromium failed to launch for lack of system libraries.

    Playwright >=1.49 reports this as TargetClosedError whose text embeds
    the browser log line 'error while loading shared libraries: libatk...'
    (the older 'Host system is missing dependencies' wording also exists).
    """
    if os.name != "posix":
        return False
    msg = str(exc).lower()
    return ("missing dependencies" in msg
            or "error while loading shared libraries" in msg
            or "libatk" in msg)


def run_async(coro):
    """asyncio.run() that also works inside the Google Colab / Jupyter kernel.

    The IPython kernel already runs an event loop, so a plain asyncio.run()
    would die with 'asyncio.run() cannot be called from a running event
    loop'. When a running loop is detected we apply nest_asyncio first,
    which makes the nested call legal. Normal terminal runs take the
    simple asyncio.run() path unchanged.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:                 # plain script: no loop running yet
        return asyncio.run(coro)
    import nest_asyncio                  # loop already running (Colab / %run)
    nest_asyncio.apply()
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Browser-mode scraping                                                        #
# --------------------------------------------------------------------------- #

async def collect_feed(page, label: str, args) -> list:
    """Open a Google Maps search and scroll the feed, returning place URLs."""
    url = search_url(label, args.lang)
    print(f"\n=== {label} ===")
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await handle_consent(page)
    try:
        await page.wait_for_selector(FEED_SEL, timeout=20000)
    except Exception:
        await check_block(page)
        raise RuntimeError(
            f"Could not find the results feed for '{label}'. "
            "Google Maps layout may have changed or the page was blocked.")

    hrefs: list = []
    stale, end_seen = 0, False
    while len(hrefs) < args.per_category and not end_seen:
        for link in await page.locator(FEED_LINK_SEL).all():
            h = await link.get_attribute("href")
            if h and h not in hrefs:
                hrefs.append(h)
        if len(hrefs) >= args.per_category:
            break
        if await page.get_by_text(END_TEXT).count():
            break
        before = len(hrefs)
        await page.locator(FEED_SEL).evaluate("el => el.scrollTo(0, el.scrollHeight)")
        await page.wait_for_timeout(random.randint(900, 1700))
        stale = stale + 1 if len(hrefs) == before else 0
        if stale >= 4:
            end_seen = True
    print(f"  found {len(hrefs)} listing(s) in the feed")
    return hrefs[:args.per_category]


async def extract_place(page, href: str, cat: str, args, browser) -> dict:
    """Open one place page and read every detail we can get."""
    full = href if href.startswith("http") else "https://www.google.com" + href
    if "?" in full:
        full += "&hl=" + args.lang
    else:
        full += "?hl=" + args.lang

    rec = {c: "" for c in OUTPUT_COLUMNS}
    rec.update({"category": cat, "maps_url": full, "scraped_at": now_iso()})
    try:
        await page.goto(full, wait_until="domcontentloaded", timeout=args.timeout * 1000)
        await check_block(page)
        await page.wait_for_selector("h1", timeout=12000)
        await page.wait_for_timeout(1200)          # let the detail panel settle

        rec["name"] = await grab_text(page, ["h1.DUwDvf", "h1"])
        rec["address"] = await grab_text(page, ['button[data-item-id="address"]'])

        try:
            pid = await page.locator('button[data-item-id^="phone:tel:"]') \
                             .first.get_attribute("data-item-id", timeout=3000)
            rec["phone"] = pid.split("phone:tel:")[-1].strip() if pid else ""
        except Exception:
            pass
        try:
            rec["website"] = (await page.locator('a[data-item-id="authority"]')
                              .first.get_attribute("href", timeout=3000)) or ""
        except Exception:
            pass
        rec["plus_code"] = await grab_text(page, ['button[data-item-id="oloc"]'])
        rec["rating"] = await grab_text(page, ["div.F7nice span:first-child"])
        try:
            label = await page.locator('span[aria-label*="review" i]') \
                              .first.get_attribute("aria-label", timeout=3000)
            m = re.search(r"[\d,]+", label or "")
            rec["reviews"] = m.group(0).replace(",", "") if m else ""
        except Exception:
            pass

        # The place page sometimes shows a '@lat,lng' of the viewport only;
        # the URL 'data' blob (!3d..!4d..) is exact, so prefer it.
        lat, lng = parse_latlng(page.url) or parse_latlng(full)
        rec["latitude"], rec["longitude"] = lat, lng
    except Exception as exc:
        rec["error"] = str(exc)[:200]

    if rec["website"] and not args.no_email:
        rec["email"] = harvest_email(rec["website"])
    return rec


async def run_browser_mode(args, cats) -> list:
    from playwright.async_api import async_playwright

    base = Path(args.out).expanduser().resolve()
    raw_path = str(base.with_suffix("")) + "_raw.jsonl"
    rows: list = []

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=not args.headed,
                                               args=LAUNCH_ARGS)
        except Exception as exc:
            # Fresh Colab/Linux VMs often lack chromium's system libraries
            if _is_missing_libs_error(exc):
                print("Chromium system libraries are missing (libatk, libnss3, "
                      "...) - running 'playwright install-deps chromium' "
                      "(one-time, ~2-4 min, needs root/sudo)...")
                subprocess.run([sys.executable, "-m", "playwright",
                                "install-deps", "chromium"], check=False)
                try:
                    browser = await pw.chromium.launch(headless=not args.headed,
                                                       args=LAUNCH_ARGS)
                except Exception as exc2:
                    raise RuntimeError(
                        "Chromium still fails to launch. Run this in a Colab "
                        "cell and retry:  !playwright install-deps chromium\n"
                        f"Original error: {str(exc2)[:300]}") from exc2
            else:
                raise
        ctx = await browser.new_context(
            user_agent=USER_AGENT, locale="en-US", timezone_id="Africa/Cairo",
            viewport={"width": 1366, "height": 850})
        await ctx.add_init_script(STEALTH_JS)
        page = await ctx.new_page()

        with open(raw_path, "a", encoding="utf-8") as rawf:
            for cat_key, label in cats:
                try:
                    hrefs = await collect_feed(page, label, args)
                except RuntimeError as exc:
                    print(f"  !! {exc}")
                    continue
                for i, href in enumerate(hrefs, 1):
                    rec = await extract_place(page, href, cat_key, args, browser)
                    rows.append(rec)
                    rawf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    rawf.flush()
                    tel = rec["phone"] or "-"
                    print(f"  [{i}/{len(hrefs)}] {rec['name'] or '(unnamed)'}"
                          f" | tel {tel} | {rec['latitude']},{rec['longitude']}")
        await browser.close()
    return rows


# --------------------------------------------------------------------------- #
# API mode (official Google Places API, New)                                   #
# --------------------------------------------------------------------------- #

def run_api_mode(args, cats) -> list:
    rows: list = []
    for cat_key, label in cats:
        print(f"\n=== {label} (Places API) ===")
        token, got = None, 0
        while got < args.per_category:
            body = {"textQuery": f"{label} in {CITY}",
                    "pageSize": min(20, args.per_category - got)}
            if token:
                body["pageToken"] = token
            try:
                r = requests.post(
                    API_TEXT_SEARCH,
                    params={"key": args.api_key},
                    headers={"Content-Type": "application/json",
                             "X-Goog-FieldMask": API_FIELD_MASK},
                    json=body, timeout=30)
                r.raise_for_status()
            except Exception as exc:
                print(f"  !! API error: {exc}")
                break
            data = r.json()
            for p in data.get("places", []):
                loc = p.get("location", {})
                rec = {c: "" for c in OUTPUT_COLUMNS}
                rec.update({
                    "category": cat_key,
                    "name": (p.get("displayName", {}) or {}).get("text", ""),
                    "address": p.get("formattedAddress", ""),
                    "latitude": loc.get("latitude", ""),
                    "longitude": loc.get("longitude", ""),
                    "phone": p.get("internationalPhoneNumber", ""),
                    "website": p.get("websiteUri", ""),
                    "rating": p.get("rating", ""),
                    "reviews": p.get("userRatingCount", ""),
                    "maps_url": p.get("googleMapsUri", ""),
                    "scraped_at": now_iso(),
                })
                if rec["website"] and not args.no_email:
                    rec["email"] = harvest_email(rec["website"])
                rows.append(rec)
                got += 1
                print(f"  [{got}] {rec['name']} | tel {rec['phone'] or '-'}")
            token = data.get("nextPageToken")
            if not token:
                break
            time.sleep(2)
    return rows


# --------------------------------------------------------------------------- #
# Output helpers                                                               #
# --------------------------------------------------------------------------- #

def dedupe(rows: list) -> list:
    """Merge duplicates across categories (same name + phone/coords)."""
    merged, order = {}, []
    for r in rows:
        key = (r["name"].strip().lower(),
               r.get("phone", "").strip() or f'{r.get("latitude")},{r.get("longitude")}')
        if key in merged:
            tags = merged[key]["category"].split(";")
            if r["category"] not in tags:
                merged[key]["category"] = ";".join(tags + [r["category"]])
        else:
            merged[key] = r
            order.append(key)
    return [merged[k] for k in order]


def write_outputs(rows: list, base: Path):
    json_path = Path(str(base.with_suffix("")) + ".json")
    csv_path = Path(str(base.with_suffix("")) + ".csv")
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow({c: r.get(c, "") for c in OUTPUT_COLUMNS})
    return json_path, csv_path


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def parse_args():
    p = argparse.ArgumentParser(
        description="Scrape Al Arish businesses (restaurants, groceries, "
                    "pharmacies, supermarkets, seafood markets...) from Google Maps.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--categories", default=",".join(CATEGORIES),
                   help="comma list of category keys: " + ",".join(CATEGORIES))
    p.add_argument("--per-category", type=int, default=60,
                   help="max listings to collect per category")
    p.add_argument("--out", default="al_arish_businesses",
                   help="output base path (.csv/.json/_raw.jsonl appended)")
    p.add_argument("--lang", default="en", help="Google Maps interface language")
    p.add_argument("--headed", action="store_true", help="show the browser window")
    p.add_argument("--no-email", action="store_true",
                   help="skip harvesting e-mails from business websites")
    p.add_argument("--no-dedupe", action="store_true",
                   help="keep a place that appears in several categories")
    p.add_argument("--timeout", type=int, default=25,
                   help="per-place page-load timeout in seconds")
    p.add_argument("--api-key", default=os.environ.get("GOOGLE_MAPS_API_KEY", ""),
                   help="Google Places API (New) key -> use API mode instead of a browser")
    p.add_argument("--dry-run", action="store_true",
                   help="print the planned searches and exit")
    p.add_argument("--no-setup", action="store_true",
                   help="skip automatic dependency installation (playwright, "
                        "chromium, nest_asyncio) - Colab self-setup")
    return p.parse_args()


def resolve_categories(args):
    keys = [k.strip() for k in args.categories.split(",") if k.strip()]
    unknown = [k for k in keys if k not in CATEGORIES]
    if unknown:
        sys.exit(f"Unknown category key(s): {', '.join(unknown)}\n"
                 f"Available: {', '.join(CATEGORIES)}")
    return [(k, CATEGORIES[k]) for k in keys]


def main():
    args = parse_args()
    cats = resolve_categories(args)

    if args.dry_run:
        print("Planned searches (browser mode):")
        for _, label in cats:
            print("  " + search_url(label, args.lang))
        return

    base = Path(args.out).expanduser().resolve()
    base.parent.mkdir(parents=True, exist_ok=True)
    print(f"Al Arish Google Maps scraper - {len(cats)} category(ies), "
          f"max {args.per_category}/category, mode: "
          f"{'API' if args.api_key else 'browser'}")

    t0 = time.time()
    if args.api_key:
        if not args.no_setup:
            ensure_deps(packages_only=True)   # API mode only needs requests
        rows = run_api_mode(args, cats)
    else:
        if not args.no_setup:
            ensure_deps()
        rows = run_async(run_browser_mode(args, cats))

    if not args.no_dedupe:
        rows = dedupe(rows)
    json_path, csv_path = write_outputs(rows, base)

    print(f"\n--- done: {len(rows)} unique place(s) in {time.time() - t0:.0f}s ---")
    print(f"    {json_path}")
    print(f"    {csv_path}")
    if IN_COLAB:
        print("    (Colab: files are in /content - use the file browser in the "
              "left sidebar, or run files.download('%s') in a cell)" % csv_path)


if __name__ == "__main__":
    main()
