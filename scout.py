"""
Card Scout: searches eBay for the cards in config.yaml, drops consigners,
measures centering on the front photo, and writes site/data.json for the dashboard.

Run:  EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=... python scout.py
Test without eBay keys:  python scout.py --demo
"""
import base64, json, math, os, re, sys, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
import yaml

from centering import measure
import profit, alerts

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "site", "data.json")
API = "https://api.ebay.com"
CATEGORIES = {"sports": "261328",  # Sports Trading Card Singles
              "tcg": "183454"}     # CCG Individual Cards (Pokémon, Magic, etc.)
UNGRADED = "4000"                  # eBay trading-card condition id for "Ungraded"


def token(cid, secret):
    r = requests.post(
        f"{API}/identity/v1/oauth2/token",
        headers={"Authorization": "Basic " + base64.b64encode(f"{cid}:{secret}".encode()).decode(),
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
        timeout=30)
    if r.status_code != 200:
        sys.exit(f"eBay sign-in failed ({r.status_code}): {r.text[:300]}\n"
                 "Check the App ID / Cert ID, and that your Production keyset is enabled.")
    return r.json()["access_token"]


def search(tok, s, d, cons, graded=False):
    if graded:  # PSA 10 comps for the profit estimate
        filters = ["buyingOptions:{FIXED_PRICE}", f"conditionIds:{{{profit.GRADED}}}"]
    else:
        filters = [f"buyingOptions:{{{'|'.join(d.get('buying_options', ['FIXED_PRICE','AUCTION']))}}}"]
    if not graded and (s.get("min_price") is not None or s.get("max_price") is not None):
        filters += [f"price:[{s.get('min_price','')}..{s.get('max_price','')}]", "priceCurrency:USD"]
    if not graded and d.get("raw_only", True):
        filters.append(f"conditionIds:{{{UNGRADED}}}")
    if d.get("ship_to_country"):
        filters.append(f"deliveryCountry:{d['ship_to_country']}")
    blocked = cons.get("blocked_sellers") or []
    if blocked:
        filters.append(f"excludeSellers:{{{'|'.join(blocked[:250])}}}")
    params = {"q": profit.comp_query(s, d) if graded else s["query"],
              "filter": ",".join(filters), "limit": 200, "sort": "newlyListed"}
    cat = CATEGORIES.get(s.get("category", "sports"), s.get("category"))
    if cat:
        params["category_ids"] = cat
    items, offset, cap = [], 0, 200 if graded else int(d.get("max_results_per_search", 400))
    while offset < cap:
        params["offset"] = offset
        r = requests.get(f"{API}/buy/browse/v1/item_summary/search", params=params,
                         headers={"Authorization": f"Bearer {tok}",
                                  "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"}, timeout=30)
        if r.status_code in (401, 403):
            sys.exit(f"eBay refused the search ({r.status_code}): {r.text[:300]}\n"
                     "Your keyset may need Browse API access — see README, step 1.")
        r.raise_for_status()
        page = r.json().get("itemSummaries", [])
        items += page
        if len(page) < params["limit"]:
            break
        offset += params["limit"]
        time.sleep(0.3)
    return items[:cap]


def big_image(url):
    return re.sub(r"s-l\d+", "s-l1600", url or "")


def to_listing(it, meta):
    imgs = [big_image((it.get("image") or {}).get("imageUrl"))]
    imgs += [big_image(a.get("imageUrl")) for a in it.get("additionalImages", [])]
    imgs = [u for u in imgs if u]
    seller = it.get("seller") or {}
    price = it.get("price") or it.get("currentBidPrice") or {}
    ship = (it.get("shippingOptions") or [{}])[0].get("shippingCost", {})
    return {
        "id": it.get("itemId"), "title": it.get("title", ""), "url": it.get("itemWebUrl"),
        "player": meta.get("player", ""), "year": str(meta.get("year", "")), "set": meta.get("set", ""),
        "images": imgs,
        "price": float(price.get("value", 0) or 0),
        "shipping": float(ship.get("value", 0) or 0) if ship else None,
        "auction": "AUCTION" in (it.get("buyingOptions") or []),
        "ends": it.get("itemEndDate"), "listed": it.get("itemCreationDate"),
        "seller": seller.get("username", ""), "feedback": seller.get("feedbackScore"),
        "feedback_pct": seller.get("feedbackPercentage"),
        "condition": it.get("condition", ""),
    }


def drop_consigners(listings, cons):
    words = [w.lower() for w in cons.get("blocked_title_words") or []]
    blocked = {s.lower() for s in cons.get("blocked_sellers") or []}
    max_fb = int(cons.get("max_seller_feedback") or 0)
    max_per = int(cons.get("max_listings_per_seller") or 0)
    counts = Counter(l["seller"] for l in listings)
    kept, dropped = [], Counter()
    for l in listings:
        t = l["title"].lower()
        if l["seller"].lower() in blocked:
            dropped["blocked seller"] += 1
        elif any(w in t for w in words):
            dropped["title keyword"] += 1
        elif max_fb and (l["feedback"] or 0) > max_fb:
            dropped["high-volume seller (feedback)"] += 1
        elif max_per and counts[l["seller"]] > max_per:
            dropped["high-volume seller (listing count)"] += 1
        else:
            kept.append(l)
    return kept, dict(dropped)


def fetch_and_measure(l, session):
    if not l["images"]:
        l["c"] = {"confidence": 0, "note": "no photos"}
        return l
    try:
        src = l["images"][0]
        if src.startswith("http"):
            r = session.get(src, timeout=30)
            r.raise_for_status()
            data = r.content
        else:  # demo images stored next to the dashboard
            data = open(os.path.join(HERE, "site", src), "rb").read()
        l["c"] = measure(data)
    except Exception as e:
        l["c"] = {"confidence": 0, "note": f"photo download failed ({type(e).__name__})"}
    return l


TCG_SPORTS = {"pokemon", "tcg", "magic", "mtg", "one piece", "lorcana", "yugioh", "yu-gi-oh"}


def _years(spec):
    """'2018-' -> (2018, 9999); '2003-2016' -> (2003, 2016); None -> all years."""
    if spec in (None, ""):
        return 0, 9999
    spec = str(spec).replace(" ", "")
    if "-" in spec:
        a, b = spec.split("-", 1)
        return int(a), int(b) if b else 9999
    return int(spec), int(spec)


def _norm(t):
    return " " + re.sub(r"[^a-z0-9]+", " ", t.lower()).strip() + " "


def load_gem_rates():
    """Set list kept up to date automatically (see README: Keeping gem rates current)."""
    try:
        return yaml.safe_load(open(os.path.join(HERE, "gem_rates.yaml"))).get("sets") or []
    except FileNotFoundError:
        return []


def build_searches(cfg):
    """Group every qualifying player × set pair into as few eBay searches as possible:
    one search per player per year, with that year's sets OR'd together.
    Results are matched back to the exact set by title afterwards."""
    d = cfg.get("defaults", {})
    base_ex = [w.lower() for w in d.get("exclude_words") or []]
    neg = " ".join(f'-"{w}"' if " " in w else f"-{w}" for w in base_ex)
    cutoff = float(cfg.get("gem_rate_min", 0) or 0)
    sets = list(cfg.get("sets") or []) + load_gem_rates()
    sets = [st for st in sets if float(st.get("gem_rate", 0) or 0) >= cutoff]
    out = []
    for p in cfg.get("players") or []:
        lo, hi = _years(p.get("years"))
        sport = str(p.get("sport", "")).lower()
        tcg = sport in TCG_SPORTS
        groups = {}
        for st in sets:
            year = int(str(st["year"])[:4])
            if str(st.get("sport", "")).lower() != sport or not (lo <= year <= hi):
                continue
            groups.setdefault("" if tcg else str(st["year"]), []).append(st)
        for year, sts in sorted(groups.items()):
            for i in range(0, len(sts), 6):  # keep each OR list short
                chunk = sts[i:i + 6]
                names = [c["set"] for c in chunk]
                ors = names[0] if len(names) == 1 else "(" + ",".join(names) + ")"
                out.append({
                    "name": f"{p['name']} {year}".strip(),
                    "player": p["name"], "year": year, "sets": chunk,
                    "query": f"{year} {p['name']} {ors} {neg}".strip(),
                    "category": "tcg" if tcg else "sports",
                    "min_price": p.get("min_price", d.get("min_price")),
                    "max_price": p.get("max_price", d.get("max_price")),
                })
    for e in cfg.get("extra_searches") or []:
        e = dict(e)
        e.setdefault("player", e.get("name", "")); e.setdefault("year", "")
        e["sets"] = [{"set": e.get("set", ""), "year": e.get("year", ""), "gem_rate": e.get("gem_rate")}]
        e["exact"] = True
        e.setdefault("min_price", d.get("min_price")); e.setdefault("max_price", d.get("max_price"))
        out.append(e)
    return out


def match_set(title, s):
    """Which of the search's sets this listing belongs to, or None if it's from another product."""
    if s.get("exact"):
        st = s["sets"][0]
        return {"year": str(st.get("year", "")), "set": st.get("set", ""), "gem_rate": st.get("gem_rate")}
    t = _norm(title)
    if s["year"] and f" {s['year']} " not in t:
        return None
    for st in sorted(s["sets"], key=lambda x: -len(x["set"])):  # "Topps Chrome Update" before "Topps Chrome"
        if _norm(st["set"]) in t and not any(_norm(w) in t for w in st.get("exclude") or []):
            return {"year": str(st["year"]), "set": st["set"], "gem_rate": st.get("gem_rate")}
    return None


def scan_interval(n_searches, daily_limit):
    """Hours between scans so a day of scans stays under eBay's daily call limit."""
    per_scan = max(1, n_searches) * 1.3 + 1   # ~30% of searches need a second page
    return max(1, math.ceil(per_scan * 24 / daily_limit))


def set_output(**kv):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            for k, v in kv.items():
                f.write(f"{k}={v}\n")


def main():
    cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml")))
    d, cons, photos = cfg.get("defaults", {}), cfg.get("consigners", {}), cfg.get("photos", {})

    searches = build_searches(cfg)
    pairs = sum(len(s["sets"]) for s in searches)
    limit = int(d.get("daily_call_limit", 5000)) - 300   # headroom for retries and manual runs
    pcfg = cfg.get("profit", {})
    comp_hours = float(pcfg.get("refresh_hours", 24)) if pcfg.get("enabled", True) else 0
    comp_calls = len(searches) * 24 / comp_hours if comp_hours else 0
    every = scan_interval(len(searches), limit - comp_calls)
    print(f"{pairs} player × set pairs grouped into {len(searches)} eBay searches; "
          f"scanning every {every} hour{'s' if every > 1 else ''} keeps under {limit + 300:,} calls/day")
    if "--list" in sys.argv:
        for s in searches:
            print(f"  {s['query']}")
        return
    # Shortlist auction reminders run on every wake-up (twice an hour); they cost ~1 call each
    if "--demo" not in sys.argv and os.environ.get("EBAY_CLIENT_ID"):
        _tok = {}
        def live_item(iid):
            _tok.setdefault("t", token(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"]))
            r = requests.get(f"{API}/buy/browse/v1/item/{requests.utils.quote(iid, safe='')}",
                             headers={"Authorization": f"Bearer {_tok['t']}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"},
                             timeout=30)
            return r.json() if r.ok else None
        alerts.remind(cfg.get("alerts", {}), live_item)

    # Full scans: once an hour at most, less often if the call budget needs it
    now = datetime.now(timezone.utc)
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and (now.minute >= 30 or now.hour % every):
        print("Reminder check only; no full scan this time.")
        set_output(ran="false")
        return

    if "--demo" in sys.argv:
        from demo import demo_items
        raw = demo_items()
    else:
        cid, sec = os.environ.get("EBAY_CLIENT_ID"), os.environ.get("EBAY_CLIENT_SECRET")
        if not cid or not sec:
            sys.exit("Set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET (see README).")
        tok = token(cid, sec)
        raw = []
        for s in searches:
            found = search(tok, s, d, cons)
            print(f"{s['name']}: {len(found)} listings")
            raw += [(it, s) for it in found]

    seen, listings = set(), []
    off_set = 0
    for it, s in raw:
        if it.get("itemId") in seen:
            continue
        m = match_set(it.get("title", ""), s)
        if not m:
            off_set += 1
            continue
        seen.add(it.get("itemId"))
        l = to_listing(it, {"player": s["player"], **m})
        l["tcg"] = s.get("category") == "tcg"
        listings.append(l)
    print(f"{off_set} results dropped because the title didn't match a qualifying year/set")

    total = len(listings)
    listings, dropped = drop_consigners(listings, cons)
    print(f"{total} unique listings, {total - len(listings)} dropped as consigners/dealers: {dropped}")

    with requests.Session() as sess, ThreadPoolExecutor(8) as pool:
        listings = list(pool.map(lambda l: fetch_and_measure(l, sess), listings))

    min_photos = int(photos.get("min_photos", 2))
    for l in listings:
        flags = []
        if len(l["images"]) < min_photos:
            flags.append(f"only {len(l['images'])} photo")
        if l["c"].get("confidence", 0) < 0.35:
            flags.append("centering unreadable")
        l["flags"] = flags
    if photos.get("hide_low_confidence"):
        listings = [l for l in listings if l["c"].get("confidence", 0) >= 0.35]

    if pcfg.get("enabled", True):
        comps = profit.load()
        if "--demo" in sys.argv:
            from demo import demo_comps
            comps = demo_comps()
        else:
            todo = [s for s in searches if profit.stale(comps, s["query"], comp_hours)]
            for s in todo:
                profit.store(comps, s["query"], s, search(tok, s, d, cons, graded=True))
            live = {s["query"] for s in searches}
            comps["groups"] = {q: g for q, g in comps["groups"].items() if q in live}
            profit.save(comps)
            print(f"PSA 10 comps refreshed for {len(todo)} searches")
        for l in listings:
            profit.estimate(comps, l, pcfg)
        n = sum(1 for l in listings if l.get("est"))
        print(f"Profit estimates for {n} of {len(listings)} listings")

    alerts.run(listings, cfg.get("alerts", {}), demo="--demo" in sys.argv)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "gem_rates": {f"{st['year']} {st['set']}": st.get("gem_rate") for s in searches for st in s["sets"] if st.get("gem_rate") is not None},
               "profit": {k: pcfg.get(k) for k in ("grading_fee", "ship_to_grader", "sell_fee_pct")},
               "dropped": dropped, "listings": listings},
              open(OUT, "w"), separators=(",", ":"))
    print(f"Wrote {len(listings)} listings to {OUT}")
    set_output(ran="true")


if __name__ == "__main__":
    main()
