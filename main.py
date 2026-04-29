"""
NYC Parks Tennis Court Reservation Scraper
==========================================
Uses requests + BeautifulSoup — no browser required.
Lightweight enough to run on any cloud server.

SETUP:
    pip3 install requests beautifulsoup4

USAGE:
    python3 tennis_scraper.py                          # scan next 7 days, all courts
    python3 tennis_scraper.py --days 3                 # scan next 3 days
    python3 tennis_scraper.py --park "Riverside"       # filter by park name
    python3 tennis_scraper.py --email                  # email when slots found
    python3 tennis_scraper.py --watch --email          # check every 60s for weekend morning slots
    python3 tennis_scraper.py --loop 10 --email        # check every 10 min
"""

import requests
import argparse
import json
import sys
import os
import time
import re
from datetime import date, datetime, timedelta
from bs4 import BeautifulSoup

MAIN_URL = "https://www.nycgovparks.org/tennisreservation"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Watch mode config ─────────────────────────────────────────────────────────
WATCH_COURTS = ["Riverside Park", "Sutton East", "Alley Pond"]
WATCH_HOURS  = [8, 9, 10, 11]  # 8am-12pm
# ─────────────────────────────────────────────────────────────────────────────


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ── Fetching ──────────────────────────────────────────────────────────────────

def fetch(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        log(f"Error fetching {url}: {e}")
        return None


# ── Court discovery ───────────────────────────────────────────────────────────

def find_all_courts(park_filter=None):
    soup = fetch(MAIN_URL)
    if not soup:
        return [], []

    open_courts  = []
    closed_courts = []

    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        first = cells[0]
        # Get full cell text then strip the booking link text
        full_text = first.get_text(" ").strip()
        for strip_str in ["View Availability/Reserve", "View Availability", "Reserve"]:
            full_text = full_text.replace(strip_str, "")
        name = full_text.strip().rstrip(",").strip()
        if not name or len(name) <= 5 or name.lower().startswith("location"):
            continue
        if park_filter and park_filter.lower() not in name.lower():
            continue

        link = first.find("a", string=lambda t: t and (
            "availability" in t.lower() or "reserve" in t.lower()
        ))
        href = link["href"] if link else None

        if href:
            url = href if href.startswith("http") else f"https://www.nycgovparks.org{href}"
            open_courts.append({"name": name, "url": url})
        else:
            closed_courts.append(name)

    return open_courts, closed_courts


# ── Slot extraction ───────────────────────────────────────────────────────────

def scrape_court(court: dict, days: int) -> list:
    soup = fetch(court["url"])
    if not soup:
        return []

    slots = []
    today = date.today()

    for i in range(1, days + 1):
        d = today + timedelta(days=i)
        date_str = d.isoformat()

        div = soup.find("div", id=date_str)
        if not div:
            continue

        table = div.find("table")
        if not table:
            continue

        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            time_text = cells[0].get_text(strip=True)
            for col_idx, cell in enumerate(cells[1:], start=1):
                if cell.get_text(strip=True) == "Reserve this time":
                    court_num = headers[col_idx] if col_idx < len(headers) else f"Court {col_idx}"
                    slots.append({
                        "court":     court["name"],
                        "court_num": court_num,
                        "date":      date_str,
                        "time":      time_text,
                    })

    return slots


# ── Full scan ─────────────────────────────────────────────────────────────────

def scrape_all(days: int = 7, park_filter=None) -> dict:
    open_courts, closed_courts = find_all_courts(park_filter)

    if closed_courts:
        log(f"Watching {len(closed_courts)} closed court(s) -- will activate when they reopen:")
        for c in closed_courts:
            log(f"  [CLOSED]  {c}")

    if not open_courts:
        log("No courts currently accepting reservations.")
        return {}

    log(f"{len(open_courts)} court(s) open for booking:")
    for c in open_courts:
        log(f"  [OPEN]  {c['name']}")

    results = {}
    for court in open_courts:
        log(f"Checking {court['name']} ...")
        results[court["name"]] = scrape_court(court, days)

    return results


# ── Output formatting ─────────────────────────────────────────────────────────

def _dedup(results: dict) -> dict:
    seen = set()
    deduped = {}
    for court_name, slots in results.items():
        clean = []
        for s in slots:
            key = (court_name, s.get("court_num",""), s.get("date",""), s.get("time",""))
            if key not in seen:
                seen.add(key)
                clean.append(s)
        deduped[court_name] = clean
    return deduped


def _format_results(results: dict) -> str:
    deduped = _dedup(results)
    all_slots = [s for slots in deduped.values() for s in slots]
    if not all_slots:
        return "No open slots found."

    today_str = date.today().strftime("%a %b %d, %Y").replace(" 0", " ")
    lines = [f"NYC Tennis Court Availability -- {today_str}", ""]

    for court_name, slots in sorted(deduped.items()):
        lines.append(court_name)
        if not slots:
            lines.append("  Fully booked")
        else:
            by_date = {}
            for s in slots:
                by_date.setdefault(s.get("date","?"), []).append(s)
            for date_str, day_slots in sorted(by_date.items()):
                try:
                    friendly = datetime.strptime(date_str, "%Y-%m-%d").strftime("%a %b %d").replace(" 0", " ")
                except Exception:
                    friendly = date_str
                lines.append(f"  {friendly} -- {len(day_slots)} slot(s)")
                by_court = {}
                for s in day_slots:
                    by_court.setdefault(s.get("court_num",""), []).append(
                        s.get("time","?").replace(":00 ","").replace("a.m.","am").replace("p.m.","pm")
                    )
                for court_num, times in sorted(by_court.items()):
                    lines.append(f"    {court_num}:  {',  '.join(times)}")
        lines.append("")

    return "\n".join(lines)


def print_results(results: dict):
    all_slots = [s for slots in results.values() for s in slots]
    if not all_slots:
        log("No open slots found.")
        return
    print("\n" + _format_results(results))


def send_email(results: dict, subject=None):
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, script_dir)
        from email_alert import send_alert
        all_slots = [s for slots in results.values() for s in slots]
        send_alert(
            subject=subject or f"NYC Tennis: {len(all_slots)} slot(s) open!",
            body=_format_results(results)
        )
    except ImportError:
        log("email_alert.py not found -- skipping email.")


# ── Watch mode ────────────────────────────────────────────────────────────────

def _parse_hour(time_str: str):
    m = re.search(r'(\d{1,2}):\d{2}\s*(a\.m\.|p\.m\.)', time_str, re.I)
    if not m:
        return None
    hour = int(m.group(1))
    if 'p.m.' in m.group(2).lower() and hour != 12:
        hour += 12
    if 'a.m.' in m.group(2).lower() and hour == 12:
        hour = 0
    return hour


def _filter_watch_slots(results: dict) -> dict:
    filtered = {}
    for court_name, slots in results.items():
        if not any(w.lower() in court_name.lower() for w in WATCH_COURTS):
            continue
        matching = []
        for s in slots:
            try:
                d = datetime.strptime(s.get("date",""), "%Y-%m-%d")
                if d.weekday() not in (5, 6):  # Sat=5, Sun=6
                    continue
            except Exception:
                continue
            hour = _parse_hour(s.get("time",""))
            if hour is None or hour not in WATCH_HOURS:
                continue
            matching.append(s)
        if matching:
            filtered[court_name] = matching
    return filtered


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NYC Parks Tennis Court Scraper")
    parser.add_argument("--days",   type=int, default=7, help="Days ahead to scan (default: 7).")
    parser.add_argument("--park",   help="Filter by park name.")
    parser.add_argument("--email",  action="store_true", help="Send email when slots found.")
    parser.add_argument("--watch",  action="store_true",
                        help="Check every 60s for weekend morning slots at Riverside/Sutton East.")
    parser.add_argument("--loop",   type=int, metavar="MINUTES", help="Re-run every N minutes.")
    parser.add_argument("--output", help="Save results to JSON file.")
    args = parser.parse_args()

    def run_once():
        results = scrape_all(days=args.days, park_filter=args.park)
        print_results(results)
        all_slots = [s for slots in results.values() for s in slots]
        if args.output and all_slots:
            with open(args.output, "w") as f:
                json.dump(all_slots, f, indent=2)
            log(f"Saved to {args.output}")
        if all_slots and args.email:
            send_email(results)
        return all_slots

    if args.watch:
        log("Watch mode: checking every 60s for weekend morning slots at Riverside/Sutton East ...")
        already_alerted = set()
        while True:
            results = scrape_all(days=7)
            matches = _filter_watch_slots(results)
            if matches:
                new_slots = []
                for court_name, slots in matches.items():
                    for s in slots:
                        key = (court_name, s.get("date"), s.get("time"))
                        if key not in already_alerted:
                            already_alerted.add(key)
                            new_slots.append(s)
                if new_slots:
                    log(f"MATCH: {len(new_slots)} new weekend morning slot(s) found!")
                    print(_format_results(matches))
                    if args.email:
                        send_email(
                            matches,
                            subject=f"Tennis alert: {len(new_slots)} weekend morning slot(s) open!"
                        )
                else:
                    log("No new matching slots.")
            else:
                log("No matching slots -- checking again in 60s ...")
            time.sleep(60)

    elif args.loop:
        log(f"Loop mode -- checking every {args.loop} min.")
        while True:
            found = run_once()
            if found:
                break
            log(f"Sleeping {args.loop} min ...")
            time.sleep(args.loop * 60)

    else:
        run_once()


if __name__ == "__main__":
    main()
