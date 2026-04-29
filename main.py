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
}


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def fetch(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
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
                    lines.append(f"    {court_num}:  {',  '.join(times)}")
        lines.append("")

    return "\n".join(lines)


def send_email(results: dict):
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not app_password:
        log("GMAIL_APP_PASSWORD not set — skipping email.")
        return

    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    gmail = "bdsarine@gmail.com"
    body  = format_results(results)
    all_slots = [s for slots in results.values() for s in slots]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"NYC Tennis: {len(all_slots)} slot(s) open!"
    msg["From"]    = gmail
    msg["To"]      = gmail

    msg.attach(MIMEText(body, "plain"))
    html = f"""<html><body style="font-family:sans-serif;font-size:14px">
    <h2 style="color:#2e7d32">🎾 NYC Tennis Availability</h2>
    <pre style="background:#f5f5f5;padding:12px;border-radius:6px">{body}</pre>
    </body></html>"""
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail, app_password)
            server.sendmail(gmail, gmail, msg.as_string())
        log("Email sent.")
    except Exception as e:
        log(f"Email failed: {e}")


def main():
    log("Running scheduled tennis availability check...")

    open_courts, closed_courts = find_all_courts()

    if closed_courts:
        log(f"{len(closed_courts)} closed court(s): {closed_courts}")

    if not open_courts:
        log("No courts currently open.")
        return

    log(f"Checking {len(open_courts)} open court(s)...")
    results = {}
    for court in open_courts:
        log(f"  {court['name']}...")
        results[court["name"]] = scrape_court(court, days=7)

    print("\n" + format_results(results))
    send_email(results)
    log("Done.")


if __name__ == "__main__":
    main()
