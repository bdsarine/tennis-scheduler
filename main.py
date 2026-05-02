"""
NYC Parks Tennis Court Scheduler
=================================
Runs once, emails full availability, then exits.
Deployed on Railway as a cron job at 8am and 5pm ET.
"""

import requests
import json
import sys
import os
from datetime import date, datetime, timedelta
from bs4 import BeautifulSoup

MAIN_URL = "https://www.nycgovparks.org/tennisreservation"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# Shared session — warmed up once at startup
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def warmup():
    """Visit the homepage once to get cookies before scraping."""
    import time
    try:
        SESSION.get("https://www.nycgovparks.org", timeout=15)
        time.sleep(1)
        log("Session warmed up.")
    except Exception as e:
        log(f"Warmup failed: {e}")


def fetch(url: str):
    try:
        resp = SESSION.get(url, timeout=15)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        log(f"Error fetching {url}: {e}")
        return None


def find_all_courts():
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
        full_text = first.get_text(" ").strip()
        for strip_str in ["View Availability/Reserve", "View Availability", "Reserve"]:
            full_text = full_text.replace(strip_str, "")
        name = full_text.strip().rstrip(",").strip()

        if not name or len(name) <= 5 or name.lower().startswith("location"):
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



def _group_time_ranges(times: list) -> list:
    """Group consecutive hours into ranges e.g. ['8am','9am','10am'] -> ['8am-10am']"""
    if not times:
        return []

    def to_hour(t):
        import re as _re
        m = _re.search(r'(\d{1,2})(am|pm)', t, _re.I)
        if not m:
            return None
        h = int(m.group(1))
        if 'pm' in m.group(2).lower() and h != 12:
            h += 12
        if 'am' in m.group(2).lower() and h == 12:
            h = 0
        return h

    def fmt(h):
        if h == 0: return "12am"
        if h < 12: return f"{h}am"
        if h == 12: return "12pm"
        return f"{h-12}pm"

    hours = [to_hour(t) for t in times]
    hours = [h for h in hours if h is not None]
    hours.sort()

    ranges = []
    if not hours:
        return times

    start = hours[0]
    end   = hours[0]
    for h in hours[1:]:
        if h == end + 1:
            end = h
        else:
            ranges.append(fmt(start) if start == end else f"{fmt(start)}-{fmt(end)}")
            start = end = h
    ranges.append(fmt(start) if start == end else f"{fmt(start)}-{fmt(end)}")
    return ranges

def format_results(results: dict) -> str:
    all_slots = [s for slots in results.values() for s in slots]
    if not all_slots:
        return "No open slots found."

    today_str = date.today().strftime("%a %b %d, %Y").replace(" 0", " ")
    lines = [f"NYC Tennis Court Availability -- {today_str}", ""]

    for court_name, slots in sorted(results.items()):
        lines.append(court_name)
        if not slots:
            lines.append("  Fully booked")
        else:
            by_date = {}
            for s in slots:
                by_date.setdefault(s.get("date", "?"), []).append(s)
            for date_str, day_slots in sorted(by_date.items()):
                try:
                    friendly = datetime.strptime(date_str, "%Y-%m-%d").strftime("%a %b %d").replace(" 0", " ")
                except Exception:
                    friendly = date_str
                lines.append(f"  {friendly} -- {len(day_slots)} slot(s)")
                by_court = {}
                for s in day_slots:
                    by_court.setdefault(s.get("court_num", ""), []).append(
                        s.get("time", "?").replace(":00 ", "").replace("a.m.", "am").replace("p.m.", "pm")
                    )
                for court_num, times in sorted(by_court.items()):
                    ranges = _group_time_ranges(times)
                    lines.append(f"    {court_num}:  {',  '.join(ranges)}")
        lines.append("")

    return "\n".join(lines)


def send_email(results: dict):
    api_key = os.environ.get("SENDGRID_API_KEY", "")
    if not api_key:
        log("SENDGRID_API_KEY not set -- skipping email.")
        return
    import urllib.request, json as _json
    body = format_results(results)
    all_slots = [s for slots in results.values() for s in slots]
    payload = _json.dumps({
        "personalizations": [{"to": [{"email": "bdsarine@gmail.com"}]}],
        "from": {"email": "bdsarine@gmail.com", "name": "Tennis Scheduler"},
        "subject": f"NYC Tennis: {len(all_slots)} slot(s) open!",
        "content": [{"type": "text/plain", "value": body + "\n\nBook now: https://www.nycgovparks.org/tennisreservation"}]
    }).encode()
    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=15)
        log("Email sent via SendGrid.")
    except Exception as e:
        log(f"Email failed: {e}")


def run_check():
    """Run one full availability check and send email."""
    log("Running scheduled tennis availability check...")

    open_courts, closed_courts = find_all_courts()

    if not open_courts:
        log("No courts currently open.")
        return

    results = {}
    for court in open_courts:
        results[court["name"]] = scrape_court(court, days=7)

    print("\n" + format_results(results))
    send_email(results)
    log("Done.")


def main():
    import time as _time
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", action="store_true", help="Run immediately and exit.")
    args = parser.parse_args()

    log("Scheduler started -- warming up session...")
    warmup()

    if args.now:
        run_check()
        return

    last_run_hour = -1  # track which hour we last ran

    while True:
        # Railway runs in UTC. 8am ET = 12 UTC, 5pm ET = 21 UTC (EDT, UTC-4)
        now = datetime.now(tz=__import__("datetime").timezone.utc)
        current_hour = now.hour

        if current_hour in (12, 21) and current_hour != last_run_hour:
            log(f"Scheduled run at UTC {current_hour}:00 ...")
            run_check()
            last_run_hour = current_hour
        else:
            if current_hour not in (12, 21):
                last_run_hour = -1

        _time.sleep(60)  # check every minute


if __name__ == "__main__":
    main()
