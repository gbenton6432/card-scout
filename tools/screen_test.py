"""
Grade-screener test (step 1): which of a player's cards trade as PSA 10s in a price range, and how
their PSA 10 asking prices compare with raw asking prices. ASKING prices only (eBay active
listings) - sold prices come from a sales source in step 2.

  python tools/screen_test.py "Shohei Ohtani" baseball [--psa8] [--lo 200] [--hi 1000]

Prints a table and writes screen_<player>.json.
"""
import argparse, json, os, re, statistics, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import yaml  # noqa: E402
import scout  # noqa: E402
from cards import card_key  # noqa: E402
from ebay import Ebay, PAGE  # noqa: E402
from tools.check_item import _NoStore  # noqa: E402

GRADED, UNGRADED = 2750, 4000
BRANDS = ["topps", "bowman", "finest", "stadium club", "panini", "prizm", "donruss", "optic", "select", "mosaic",
          "hoops", "contenders", "national treasures", "flawless", "immaculate", "chronicles", "upper deck", "ud",
          "fleer", "skybox", "metal", "ultra", "e x", "sp authentic", "spx", "exquisite", "collector s choice",
          "score", "leaf", "pacific", "bowman s best", "stadium", "gallery", "heritage"]


def grade_of(title):
    m = re.search(r"\bpsa\s*(10|9|8)\b", title, re.I)
    return int(m.group(1)) if m else None


def low3(v):
    """Average of the 3 cheapest Buy It Now asks (fewer if fewer are listed)."""
    v = sorted(v)[:3]
    return sum(v) / len(v) if v else None


def search(e, q, cond, lo, hi, pages, bin_only=True):
    out = []
    for p in range(pages):
        j = e._get("/buy/browse/v1/item_summary/search", {
            "q": q, "category_ids": "261328", "limit": PAGE, "offset": p * PAGE,
            "filter": f"conditionIds:{{{cond}}},price:[{lo}..{hi}],priceCurrency:USD,deliveryCountry:US"
                      + (",buyingOptions:{FIXED_PRICE}" if bin_only else "")}) or {}
        items = j.get("itemSummaries", [])
        out += items
        if len(items) < PAGE:
            break
    return out


def total(it):
    p = float((it.get("price") or {}).get("value") or 0)
    if "AUCTION" in (it.get("buyingOptions") or []) and it.get("currentBidPrice"):
        p = float(it["currentBidPrice"]["value"])
    ship = 0.0
    for o in it.get("shippingOptions") or []:
        ship = float((o.get("shippingCost") or {}).get("value") or 0)
        break
    return p + ship


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("player"); ap.add_argument("sport")
    ap.add_argument("--psa8", action="store_true"); ap.add_argument("--lo", type=float, default=200)
    ap.add_argument("--hi", type=float, default=1000); ap.add_argument("--pages", type=int, default=10)
    ap.add_argument("--raw-cards", type=int, default=120)
    a = ap.parse_args()
    cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml")))
    # Screening: no gem-rate cutoff, any major brand, every year
    cfg["players"] = [{"name": a.player, "sport": a.sport, "years": "1980-", "gem_rate_min": 0,
                       "no_cutoff": [{"years": "1980-", "brands": BRANDS}]}]
    m = scout.Matcher(cfg)
    p = scout.players_from(cfg)[0]
    e = Ebay(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"], _NoStore(), budget=200)

    def key(t):
        r = m.match(t, p)
        if not r:
            return None
        y, st = r
        num, par = card_key(t, a.player, st["set"], False)
        if not num:
            return None
        return (y, st["set"], num, par or "base")

    grades = (10, 9, 8) if a.psa8 else (10, 9)
    asks = defaultdict(lambda: defaultdict(list))
    titles = {}
    # PSA-graded listings: 10s in the target range decide which cards count; 9s/8s priced lower
    for g in grades:
        # 10s: search below the range too, so a card's cheapest PSA 10 asks aren't cut off
        lo, hi = (a.lo / 3, a.hi * 1.5) if g == 10 else (10, a.hi)
        for it in search(e, f"{a.player} PSA {g}", GRADED, lo, hi, a.pages if g == 10 else max(3, a.pages // 2)):
            t = it.get("title", "")
            if grade_of(t) != g:
                continue
            k = key(t)
            if k:
                asks[k][f"psa{g}"].append(total(it))
                titles.setdefault(k, t)
    # Raw: search each candidate card directly (most-listed PSA 10s first), Buy It Now only
    cands = sorted(asks, key=lambda k: -len(asks[k].get("psa10", [])))[:a.raw_cards]
    for k in cands:
        par = "" if k[3] == "base" else k[3].replace("+", " ").replace("/", "")
        for it in search(e, f"{k[0]} {k[1]} {a.player} {k[2]} {par}".strip(), UNGRADED, 1, a.hi, 1):
            t = it.get("title", "")
            if re.search(r"\b(psa|bgs|sgc|cgc|beckett|graded|lot|reprint)\b", t, re.I):
                continue
            if key(t) == k:
                asks[k]["raw"].append(total(it))
    rows = []
    for k, v in asks.items():
        if not v.get("psa10"):
            continue
        p10 = low3(v["psa10"])
        if len(v["psa10"]) < 2:   # one PSA 10 listing isn't a price
            continue
        rw = low3(v["raw"]) if v.get("raw") else None
        rate = m.card_rate(a.player, k) if hasattr(m, "card_rate") else None
        rows.append({"card": f"{k[0]} {k[1]} #{k[2]} {'' if k[3] == 'base' else k[3]}".strip(),
                     "psa10_ask": round(p10), "n10": len(v["psa10"]),
                     "psa9_ask": round(low3(v["psa9"])) if v.get("psa9") else None,
                     "psa8_ask": round(low3(v["psa8"])) if v.get("psa8") else None,
                     "raw_ask": round(rw, 2) if rw else None, "nraw": len(v.get("raw", [])),
                     "mult": round(p10 / (rw + 40), 2) if rw else None, "example": titles[k][:80]})
    rows = [r for r in rows if a.lo <= r["psa10_ask"] <= a.hi]
    rows.sort(key=lambda r: (r["mult"] is None, -(r["mult"] or 0), -r["n10"]))
    print(f"\n{a.player}: {len(rows)} cards whose 3 cheapest PSA 10 Buy It Now asks average ${a.lo:.0f}-{a.hi:.0f} "
          f"(2+ listed); prices = average of the 3 cheapest Buy It Now asks incl. shipping; eBay calls {e.used}")
    print(f"{'card':58} {'PSA10':>6} {'n':>3} {'PSA9':>5}" + (f" {'PSA8':>5}" if a.psa8 else "") +
          f" {'raw':>7} {'n':>3} {'x(raw+40)':>9}")
    for r in rows[:80]:
        print(f"{r['card'][:58]:58} {r['psa10_ask']:>6} {r['n10']:>3} {r['psa9_ask'] or '-':>5}" +
              (f" {r['psa8_ask'] or '-':>5}" if a.psa8 else "") +
              f" {r['raw_ask'] if r['raw_ask'] is not None else '-':>7} {r['nraw']:>3} {r['mult'] if r['mult'] else '-':>9}")
    json.dump(rows, open(f"screen_{a.player.replace(' ', '_')}.json", "w"), indent=1)


if __name__ == "__main__":
    main()
