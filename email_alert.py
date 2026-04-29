"""
email_alert.py — Shared email helper for all scrapers.

SETUP (one-time):
  1. Go to https://myaccount.google.com/apppasswords
  2. Create an app password for "Mail"
  3. Paste it into GMAIL_APP_PASSWORD below (or set env var)

Any scraper can use this:
    from email_alert import send_alert
    send_alert(subject="Tennis slots open!", body="Court 5 at 9am...")
"""

import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# ── Config — fill these in ────────────────────────────────────────────────────
GMAIL_ADDRESS  = "bdsarine@gmail.com"
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")  # set via env or paste here
TO_ADDRESS     = "bdsarine@gmail.com"
# ─────────────────────────────────────────────────────────────────────────────


def send_alert(subject: str, body: str) -> bool:
    """
    Send an email alert. Returns True on success, False on failure.
    Safe to call even if no slots are found — just don't call it then.
    """
    if not GMAIL_APP_PASSWORD:
        print("[email] ERROR: GMAIL_APP_PASSWORD not set. See email_alert.py setup instructions.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = TO_ADDRESS

        # Plain text version
        msg.attach(MIMEText(body, "plain"))

        # HTML version — nicer in Gmail
        html_body = body.replace("\n", "<br>").replace("  ", "&nbsp;&nbsp;")
        html = f"""
        <html><body style="font-family: sans-serif; font-size: 14px; color: #222;">
        <h2 style="color: #2e7d32;">🎾 {subject}</h2>
        <pre style="background:#f5f5f5; padding:12px; border-radius:6px; font-size:13px;">{body}</pre>
        <p style="color:#888; font-size:12px;">Sent by your scraper at {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
        </body></html>
        """
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, TO_ADDRESS, msg.as_string())

        print(f"[email] Alert sent: {subject}")
        return True

    except Exception as e:
        print(f"[email] Failed to send: {e}")
        return False


def format_tennis_slots(results: dict) -> str:
    """Format tennis scraper results dict into a readable email body."""
    lines = []
    for court_name, slots in sorted(results.items()):
        if not slots:
            continue
        lines.append(f"\n{court_name}")
        by_date = {}
        for s in slots:
            by_date.setdefault(s.get("date", "?"), []).append(s)
        for date_str, day_slots in sorted(by_date.items()):
            try:
                from datetime import datetime
                friendly = datetime.strptime(date_str, "%Y-%m-%d").strftime("%a %b %d").replace(" 0", " ")
            except Exception:
                friendly = date_str
            lines.append(f"  {friendly} — {len(day_slots)} slot(s)")
            by_court = {}
            for s in day_slots:
                by_court.setdefault(s.get("court_num", ""), []).append(s.get("time", "?"))
            for court_num, times in sorted(by_court.items()):
                lines.append(f"    {court_num}: {', '.join(times)}")
    return "\n".join(lines)
