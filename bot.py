"""Forward new posts from an X account to a Telegram channel, 100% free.

No X API is used. Posts are read from (in order):
  1. your own feeds in EXTRA_FEEDS (optional, any RSS URL)
  2. public Nitter RSS feeds (xcancel.com etc.)
  3. X's public "syndication" timeline
The first source that answers wins.
"""
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

USERNAME = os.environ.get("X_USERNAME") or "Dexerto"
TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]

FIRST_RUN_HOURS = int(os.environ.get("FIRST_RUN_HOURS") or "4")
LINK_PREVIEW = os.environ.get("TG_LINK_PREVIEW") == "1"
UA = os.environ.get("USER_AGENT") or (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
NITTER_INSTANCES = [
    s.strip().rstrip("/")
    for s in (
        os.environ.get("NITTER_INSTANCES")
        or "https://xcancel.com,https://nitter.poast.org,https://nitter.privacydev.net"
    ).split(",")
    if s.strip()
]
EXTRA_FEEDS = [u.strip() for u in (os.environ.get("EXTRA_FEEDS") or "").split(",") if u.strip()]

STATE_FILE = Path(__file__).with_name("state.json")
STATUS_RE = re.compile(r"/status/(\d+)")
HEADERS = {"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/html, */*"}


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------- state ----------
def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


# ---------- sources ----------
def parse_rss(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    posts = []
    for item in root.iter("item"):
        title = html.unescape(item.findtext("title") or "").strip()
        if title.startswith("RT by @") or title.startswith("R to @"):
            continue  # retweets / replies are skipped
        ref = f"{item.findtext('link') or ''} {item.findtext('guid') or ''}"
        m = STATUS_RE.search(ref)
        if m:
            posts.append({"id": m.group(1), "text": title})
    return posts


def fetch_url(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


def from_extra_feeds() -> list[dict]:
    for url in EXTRA_FEEDS:
        try:
            return parse_rss(fetch_url(url))
        except Exception as e:  # noqa: BLE001
            log(f"  extra feed failed ({url}): {e}")
    raise RuntimeError("no extra feed worked")


def from_nitter() -> list[dict]:
    for inst in NITTER_INSTANCES:
        try:
            posts = parse_rss(fetch_url(f"{inst}/{USERNAME}/rss"))
            log(f"  nitter ok: {inst} ({len(posts)} items)")
            return posts
        except Exception as e:  # noqa: BLE001
            log(f"  nitter failed ({inst}): {e}")
    raise RuntimeError("no nitter instance worked")


def from_syndication() -> list[dict]:
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{USERNAME}"
    page = fetch_url(url)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        raise RuntimeError("syndication: no data in page")
    entries = json.loads(m.group(1))["props"]["pageProps"]["timeline"]["entries"]
    posts = []
    for e in entries:
        if e.get("type") != "tweet":
            continue
        t = e["content"]["tweet"]
        if t.get("retweeted_status") or t.get("in_reply_to_status_id_str"):
            continue
        pid = t.get("id_str")
        if pid:
            text = html.unescape(t.get("full_text") or t.get("text") or "")
            posts.append({"id": pid, "text": text.strip()})
    log(f"  syndication ok ({len(posts)} items)")
    return posts


def fetch_posts() -> list[dict]:
    sources = []
    if EXTRA_FEEDS:
        sources.append(("extra feeds", from_extra_feeds))
    sources += [("nitter", from_nitter), ("syndication", from_syndication)]
    for name, fn in sources:
        log(f"Trying {name}...")
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            log(f"  {name} failed: {e}")
    sys.exit("All free sources failed this run (nothing sent, state unchanged).")


# ---------- helpers ----------
def id_time(post_id: str) -> datetime:
    """Post IDs embed their creation time."""
    ms = (int(post_id) >> 22) + 1288834974657
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def pick_new(posts: list[dict], last_id: str | None) -> list[dict]:
    seen, out = set(), []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=FIRST_RUN_HOURS)
    for p in posts:
        if p["id"] in seen:
            continue
        seen.add(p["id"])
        if last_id:
            if int(p["id"]) <= int(last_id):
                continue
        elif id_time(p["id"]) < cutoff:  # first run: don't flood the channel
            continue
        out.append(p)
    return sorted(out, key=lambda p: int(p["id"]))  # oldest first


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": not LINK_PREVIEW}
    for _ in range(5):
        r = requests.post(url, json=payload, timeout=30)
        if r.status_code == 429:
            time.sleep(r.json().get("parameters", {}).get("retry_after", 5) + 1)
            continue
        if not r.ok:
            sys.exit(f"Telegram error {r.status_code}: {r.text}")
        return
    sys.exit("Telegram: too many rate-limit retries")


def main() -> None:
    state = load_state()
    posts = pick_new(fetch_posts(), state.get("last_id"))
    log(f"{len(posts)} new post(s) from @{USERNAME}")
    for p in posts:
        link = f"https://x.com/{USERNAME}/status/{p['id']}"
        send_telegram(f"{p['text'][:3500]}\n\n{link}")
        state["last_id"] = p["id"]
        save_state(state)  # saved after every send, so it never re-sends
        time.sleep(3)


if __name__ == "__main__":
    main()
