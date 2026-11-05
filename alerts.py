"""
Alerts: opens a GitHub issue listing new cards that pass your alert rules.
GitHub then notifies you by email and push (GitHub mobile app). Free, no extra accounts.
"""
import json, os
from datetime import datetime, timezone, timedelta

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN = os.path.join(HERE, "cache", "alerted.json")


def qualifies(l, a):
    c = l.get("c") or {}
    if a.get("psa10_centering_only", True) and not (c.get("confidence", 0) >= 0.35 and c.get("worst", 99) <= 55.5):
        return False
    if a.get("max_price") and l["price"] > float(a["max_price"]):
        return False
    if a.get("buy_it_now_only", True) and l["auction"]:
        return False
    est = l.get("est")
    if a.get("min_profit") is not None:
        if est is None:
            return not a.get("require_profit_estimate", False)
        return est["profit"] >= float(a["min_profit"])
    return True


def _row(l):
    est = l.get("est")
    profit = f"${est['profit']:,.0f}" if est else "–"
    c = l["c"].get("centering", "–")
    sale = "Auction" if l["auction"] else "BIN"
    return f"| [{l['title'][:70]}]({l['url']}) | ${l['price']:,.2f} | {sale} | {c} | {profit} |"


def run(listings, a, demo=False):
    if not a.get("enabled", True):
        return
    try:
        seen = json.load(open(SEEN))
        first_run = False
    except Exception:
        seen, first_run = {}, True
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=45)).isoformat()
    seen = {k: v for k, v in seen.items() if v > cutoff}

    new = [l for l in listings if l["id"] not in seen and qualifies(l, a)]
    for l in listings:
        if qualifies(l, a):
            seen[l["id"]] = now.isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(SEEN), exist_ok=True)
    json.dump(seen, open(SEEN, "w"))

    if first_run:
        print(f"Alerts: first run, {len(new)} current matches recorded without alerting.")
        return
    if not new:
        print("Alerts: nothing new.")
        return
    new.sort(key=lambda l: -(l["est"]["profit"] if l.get("est") else -1e9))
    repo, tok = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    owner, name = (repo or "/").split("/")
    body = "\n".join([
        f"**{len(new)} new card{'s' if len(new) > 1 else ''}** passed your alert rules.",
        f"[Open the dashboard](https://{owner.lower()}.github.io/{name}/)", "",
        "| Card | Price | Sale | Centering | Est. profit |", "|---|---|---|---|---|",
        *[_row(l) for l in new[:40]],
        "" if len(new) <= 40 else f"\n…and {len(new) - 40} more on the dashboard.",
    ])
    title = f"{len(new)} new Card Scout pick{'s' if len(new) > 1 else ''}"
    if demo:
        print(f"Alerts (demo, not sent):\n{title}\n{body}")
        return
    _issue(title, body)


# ── Shortlist auction reminders ───────────────────────────────
REMINDED = os.path.join(HERE, "cache", "reminded.json")


def _owner():
    o = os.environ.get("GITHUB_REPOSITORY_OWNER")
    return [o] if o else []


def _ping():
    # Mentions and assignments are what the GitHub phone app pushes by default
    o = os.environ.get("GITHUB_REPOSITORY_OWNER")
    return f"\n\n@{o}" if o else ""


def _issue(title, body):
    repo, tok = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not (repo and tok):
        print(f"(not sent, no GitHub token)\n{title}\n{body}")
        return
    r = requests.post(f"https://api.github.com/repos/{repo}/issues",
                      headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                      json={"title": title, "body": body + _ping(), "assignees": _owner()}, timeout=30)
    print(f"Issue '{title}': {r.status_code}")


def remind(a, live_item=None):
    """Alert for shortlisted auctions ending within the reminder window.
    live_item(id) -> eBay item dict, used to show the current bid."""
    try:
        shortlist = json.load(open(os.path.join(HERE, "shortlist.json")))
    except Exception:
        return
    try:
        reminded = json.load(open(REMINDED))
    except Exception:
        reminded = {}
    window = float(a.get("auction_reminder_minutes", 75))
    now = datetime.now(timezone.utc)
    due = []
    for iid, it in shortlist.items():
        if not it.get("auction") or not it.get("ends") or iid in reminded:
            continue
        ends = datetime.fromisoformat(it["ends"].replace("Z", "+00:00"))
        mins = (ends - now).total_seconds() / 60
        if 0 < mins <= window:
            due.append((mins, iid, it))
    for mins, iid, it in sorted(due):
        bid = ""
        if live_item:
            try:
                live = live_item(iid) or {}
                cur = (live.get("currentBidPrice") or live.get("price") or {}).get("value")
                if cur:
                    bid = f" · current bid ${float(cur):,.2f} ({live.get('bidCount', 0)} bids)"
            except Exception:
                pass
        _issue(f"Ending in {round(mins)} min: {it.get('title', '')[:80]}",
               f"Your shortlisted auction ends in about **{round(mins)} minutes**{bid}.\n\n"
               f"[Open on eBay]({it.get('url')})")
        reminded[iid] = now.isoformat(timespec="seconds")
    cutoff = (now - timedelta(days=30)).isoformat()
    reminded = {k: v for k, v in reminded.items() if v > cutoff}
    os.makedirs(os.path.dirname(REMINDED), exist_ok=True)
    json.dump(reminded, open(REMINDED, "w"))
