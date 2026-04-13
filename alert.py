import yfinance as yf
import datetime
import requests
import xml.etree.ElementTree as ET
from email.mime.text import MIMEText
import base64
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

# ------------------------
# CONFIG
# ------------------------
STOCKS = {
    "MDT": {"name": "Medtronic", "ir": "https://news.medtronic.com/rss"},
    "HPQ": {"name": "HP Inc", "ir": None},
    "FMX": {"name": "Femsa", "ir": "https://femsa.gcs-web.com/rss/news-releases.xml"},
    "7270.T": {"name": "Subaru", "ir": "https://www.subaru.co.jp/press/news-en/feed/"},
    "MOH": {"name": "Molina", "ir": "https://investors.molinahealthcare.com/rss/news-releases.xml"},
    "DOX": {"name": "Amdocs", "ir": "https://investors.amdocs.com/rss/news-releases.xml"},
    "SOON.SW": {"name": "Sonova", "ir": None},
    "INTC": {"name": "Intel", "ir": "https://www.intc.com/rss/news-releases.xml"}
}

RECIPIENT = "smorgan@talariacapital.com.au"

# ------------------------
# PRICE DATA
# ------------------------
def get_price_data(ticker):
    data = yf.Ticker(ticker)
    hist = data.history(period="5d")

    if hist.empty or len(hist["Close"].dropna()) < 2:
        return None

    closes = hist["Close"].dropna()

    close = closes.iloc[-1]
    prev_close = closes.iloc[-2]

    change = close - prev_close
    pct = (change / prev_close) * 100

    return close, prev_close, change, pct

# ------------------------
# EX-DIV + EARNINGS
# ------------------------
def get_events(ticker):
    t = yf.Ticker(ticker)
    flags = []

    try:
        cal = t.calendar

        # Ex-dividend
        if "Ex-Dividend Date" in cal:
            ex_date = cal["Ex-Dividend Date"][0].date()
            if ex_date == datetime.date.today():
                flags.append("EX-DIV TODAY")

        # Earnings
        if "Earnings Date" in cal:
            earn_date = cal["Earnings Date"][0].date()
            delta = (earn_date - datetime.date.today()).days
            if 0 <= delta <= 7:
                flags.append(f"EARNINGS IN {delta}D")

    except:
        pass

    return ", ".join(flags) if flags else "None"

# ------------------------
# NEWS (filtered)
# ------------------------
def get_news(company, max_items=3):
    url = f"https://news.google.com/rss/search?q={company}"
    headlines = []

    cutoff = datetime.now(timezone.utc) - timedelta(days=5)

    try:
        r = requests.get(url, timeout=20)
        root = ET.fromstring(r.content)

        for item in root.findall(".//item"):
            title = item.find("title").text if item.find("title") is not None else ""
            link = item.find("link").text if item.find("link") is not None else ""
            pub_date_text = item.find("pubDate").text if item.find("pubDate") is not None else ""

            if not title or not link or not pub_date_text:
                continue

            pub_date = parsedate_to_datetime(pub_date_text)
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)

            if pub_date < cutoff:
                continue

            if any(x in title.lower() for x in ["stock", "price", "share"]):
                continue

            date_str = pub_date.strftime("%d %b %Y")
            headlines.append((title, link, date_str))

            if len(headlines) >= max_items:
                break
    except Exception:
        pass

    return headlines

# ------------------------
# PRESS RELEASES
# ------------------------
def get_press_releases(rss_url):
    if not rss_url:
        return []

    items = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=5)

    try:
        r = requests.get(rss_url, timeout=20)
        root = ET.fromstring(r.content)

        for item in root.findall(".//item"):
            title = item.find("title").text if item.find("title") is not None else ""
            link = item.find("link").text if item.find("link") is not None else ""
            pub_date_text = item.find("pubDate").text if item.find("pubDate") is not None else ""

            if not title or not link:
                continue

            if pub_date_text:
                pub_date = parsedate_to_datetime(pub_date_text)
                if pub_date.tzinfo is None:
                    pub_date = pub_date.replace(tzinfo=timezone.utc)

                # Filter: only last 5 days
                if pub_date < cutoff:
                    continue

                date_str = pub_date.strftime("%d %b %Y")
            else:
                date_str = "No date"

            items.append((title, link, date_str))

    except Exception:
        pass

    return items

# ------------------------
# BUILD EMAIL
# ------------------------
def build_email():
    today = datetime.now().strftime("%d %B %Y")
    body = f"""
    <div style="font-family: 'Lora', Georgia, 'Times New Roman', serif; font-size: 10.5pt; line-height: 1.4;">
    <h3>DAILY STOCK UPDATE – {today}</h3>
    """

    biggest = ("", 0)

    for ticker, meta in STOCKS.items():
        name = meta["name"]

        price_data = get_price_data(ticker)

        if price_data:
            close, prev, change, pct = price_data

            if abs(pct) > abs(biggest[1]):
                biggest = (name, pct)

            line = f"{name} ({ticker}) | {close:.2f} ({pct:+.2f}%)"
            if abs(pct) > 2:
                line += "  **"
            body += line + "<br>"
            body += f"Prev: {prev:.2f} | Δ {change:+.2f}<br>"
        else:
            body += f"{name} ({ticker}) | No price<br>"

        # events
        body += f"Flags: {get_events(ticker)}<br>"

        # press releases
        prs = get_press_releases(meta["ir"])
        if prs:
            body += "<b>Press Releases:</b><br>"
            for t, l, d in prs:
                body += f'- <a href="{l}">{t}</a> ({d})<br>'
        else:
            body += "Press Releases: None<br>"

        # news
        news = get_news(name)
        if news:
            body += "<b>News:</b><br>"
            for t, l, d in news:
                body += f'- <a href="{l}">{t}</a> ({d})<br>'
        else:
            body += "News: None<br>"

        body += "<br><hr><br>"

    body = f"BIGGEST MOVER: {biggest[0]} ({biggest[1]:+.2f}%)<br><br>" + body

    return body
    body += "</div>"

# ------------------------
# SEND EMAIL
# ------------------------
def send_email(body):
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from email.mime.text import MIMEText
    import base64
    import os

    SCOPES = ['https://www.googleapis.com/auth/gmail.send']

    creds = None

    # Load existing token
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    # If no valid credentials → authenticate
    if not creds or not creds.valid:
        from google.oauth2.credentials import Credentials
        import os

        SCOPES = ['https://www.googleapis.com/auth/gmail.send']

        if not os.path.exists('token.json'):
            raise Exception("token.json not found — run locally first to authenticate")

        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

        # Save token
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    # Build Gmail service
    service = build('gmail', 'v1', credentials=creds)

    # Create email
    message = MIMEText(body, "html")
    message['to'] = RECIPIENT
    message['subject'] = "Daily Stock Update"

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    # Send
    service.users().messages().send(
        userId="me",
        body={"raw": raw}
    ).execute()

# ------------------------
# RUN
# ------------------------
if __name__ == "__main__":
    email_body = build_email()
    send_email(email_body)
