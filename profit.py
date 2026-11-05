"""
Profit estimate: what a raw card might be worth as a PSA 10, minus what it costs to get there.

PSA 10 value comes from current eBay PSA 10 listings of the same card (same player, year,
set, card number and parallel). Asking prices run higher than sales, so the estimate uses the
lower quartile of the asks, not the average. Comps refresh once a day and are cached between scans.
"""
import json, os, re, statistics
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache", "comps.json")
GRADED = "2750"  # eBay trading-card condition id for "Graded"

PARALLELS = [  # words that mark a parallel/insert; anything else is treated as base
    "x fractor", "xfractor", "refractor", "raywave", "wave", "mojo", "shimmer", "hyper", "sepia",
    "silver", "gold", "green", "blue", "red", "orange", "purple", "black", "pink", "aqua", "teal",
    "white", "yellow", "bronze", "camo", "tiger", "zebra", "snakeskin", "cracked ice", "disco",
    "lazer", "laser", "scope", "holo", "reverse", "auto", "autograph", "patch", "relic",
    "variation", "sp", "ssp", "image variation",
]
_PAR = sorted(PARALLELS, key=len, reverse=True)


def _norm(t):
    return " " + re.sub(r"[^a-z0-9/]+", " ", t.lower()).strip() + " "


def card_key(title, player, year, set_name, tcg=False):
    """(player, year, set, card number, parallel) parsed from a listing title.
    Pokémon-style "223/197" is a card number; sports-style "/99" is a numbered parallel."""
    t = _norm(title)
    if tcg:
        m = re.search(r"\b(\d{1,3})\s?/\s?\d{2,3}\b", title) or re.search(r"#\s*(\d{1,3})", title)
        t = re.sub(r"\d{1,3}\s?/\s?\d{2,3}", " ", t)
        num = str(int(m.group(1))) if m else ""
        found = [p.replace(" ", "") for p in _PAR if f" {p} " in t and p not in _norm(set_name)]
        return (player.lower(), str(year), set_name.lower(), num, "+".join(sorted(set(found))) or "base")
    for w in _norm(set_name).split() + _norm(player).split():
        t = t.replace(f" {w} ", " ")  # don't read "Prizm" or a player's name as a parallel
    num = re.search(r"#\s*([a-z]{0,5}\s?-?\s?\d{1,4}[a-z]?)", title.lower())
    number = re.sub(r"[\s-]", "", num.group(1)) if num else ""
    serial = re.search(r"/\s?(\d{1,4})\b", t)
    found, rest = [], t
    for p in _PAR:
        if f" {p} " in rest:
            found.append(p.replace(" ", ""))
            rest = rest.replace(f" {p} ", " ")
    if serial:
        found.append(f"/{serial.group(1)}")
    return (player.lower(), str(year), set_name.lower(), number, "+".join(sorted(found)) or "base")


def comp_query(s, d):
    """PSA 10 version of a raw search."""
    q = re.sub(r"\s-\S+|\s-\"[^\"]+\"", "", s["query"])  # drop the graded/slab exclusions
    return f"{q} psa 10 -lot -reprint -custom"


def is_psa10(title):
    t = _norm(title)
    return " psa 10 " in t or " psa10 " in t or (" psa " in t and " gem mt 10 " in t)


def load():
    try:
        return json.load(open(CACHE))
    except Exception:
        return {"groups": {}}


def save(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(c, open(CACHE, "w"), separators=(",", ":"))


def stale(c, q, hours):
    g = c["groups"].get(q)
    if not g:
        return True
    return datetime.fromisoformat(g["at"]) < datetime.now(timezone.utc) - timedelta(hours=hours)


def store(c, q, s, items):
    """Keep only card keys and prices from the PSA 10 results."""
    from scout import match_set  # local import avoids a cycle
    prices = {}
    for it in items:
        title = it.get("title", "")
        if not is_psa10(title):
            continue
        m = match_set(title, s)
        if not m:
            continue
        p = float((it.get("price") or {}).get("value", 0) or 0)
        ship = (it.get("shippingOptions") or [{}])[0].get("shippingCost") or {}
        p += float(ship.get("value", 0) or 0)
        if p > 0:
            k = "|".join(card_key(title, s["player"], m["year"], m["set"], s.get("category") == "tcg"))
            prices.setdefault(k, []).append(round(p, 2))
    c["groups"][q] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "prices": prices}


def estimate(c, listing, pcfg):
    """Attach psa10 value and profit to a listing when a matching comp exists."""
    k = card_key(listing["title"], listing["player"], listing["year"], listing["set"], listing.get("tcg", False))
    exact, loose = "|".join(k), "|".join(k[:4])
    prices = []
    for g in c["groups"].values():
        prices += g["prices"].get(exact, [])
    if not prices and k[3] and k[4] == "base":   # number known, no parallel words: match any base comp
        for g in c["groups"].values():
            prices += g["prices"].get(loose + "|base", [])
    if not prices:
        listing["est"] = None
        return
    prices.sort()
    value = prices[0] if len(prices) < 4 else statistics.quantiles(prices, n=4)[0]
    cost = listing["price"] + (listing["shipping"] or 0)
    net = value * (1 - float(pcfg.get("sell_fee_pct", 13.25)) / 100)
    profit = net - cost - float(pcfg.get("grading_fee", 25)) - float(pcfg.get("ship_to_grader", 5))
    listing["est"] = {"psa10": round(value, 2), "comps": len(prices), "profit": round(profit, 2),
                      "card": k[3], "parallel": k[4]}
