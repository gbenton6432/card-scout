"""
Grade Screener: for each screener player, find the cards whose PSA 10s sell in your target range
and are worth grading - the PSA 10 price vs the raw price plus grading.

Prices (until a sold-data source is connected) are the average of the 3 cheapest Buy It Now
asks, shipping included, auctions excluded, for PSA 10 / 9 / 8 and for raw copies of the card.

  python screener.py                 # players due for a refresh (config: screener.players_per_run)
  python screener.py "Kobe Bryant"   # one player now

Writes site/screener/data.json (one entry per player, kept between runs).
"""
import json, os, re, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.parse import quote_plus

import yaml

import scout
from cards import card_key
from ebay import Ebay, PAGE, OutOfBudget

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "site", "screener", "data.json")
GRADED, UNGRADED = 2750, 4000
CATEGORY = "261328"  # Sports trading cards
# Screening is open: every year, every major brand, no gem-rate cutoff
BRANDS = ["topps", "bowman", "finest", "stadium club", "panini", "prizm", "donruss", "optic", "select", "mosaic",
          "hoops", "contenders", "national treasures", "flawless", "immaculate", "chronicles", "upper deck", "ud",
          "fleer", "skybox", "metal", "ultra", "e x", "sp authentic", "spx", "exquisite", "collector s choice",
          "score", "leaf", "pacific", "bowman s best", "stadium", "gallery", "heritage", "parkside"]
NOT_RAW = re.compile(r"\b(psa|bgs|sgc|cgc|beckett|graded|slab|lot|reprint|custom)\b", re.I)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def grade_of(title):
    m = re.search(r"\bpsa\s*(10|9|8)\b", title, re.I)
    return int(m.group(1)) if m else None


def low3(v):
    """Average of the 3 cheapest Buy It Now asks (fewer if fewer are listed)."""
    v = sorted(v)[:3]
    return round(sum(v) / len(v), 2) if v else None


def total(it):
    p = float((it.get("price") or {}).get("value") or 0)
    for o in it.get("shippingOptions") or []:
        return p + float((o.get("shippingCost") or {}).get("value") or 0)
    return p


def set_key(label):
    """Spelling-proof set name, so '1996 UPPER DECK #58' and 'Upper Deck' group together."""
    s = scout._norm(re.sub(r"#\s*\S+", " ", label or "")).strip()
    s = re.sub(r"^(panini|upper deck) (?=(collector s choice|sp authentic|spx|ud3|prizm|donruss|select|mosaic|"
               r"hoops|contenders|optic|national treasures|flawless|immaculate|chronicles))", "", s)
    s = re.sub(r"\b(rc|rookie|rookies|card|basketball|baseball|football|soccer|nba|mlb|nfl)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_label(label):
    s = re.sub(r"#\s*\S+", " ", label or "")
    s = re.sub(r"\b(RC|Rookie|Card)\b", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" -,")
    return s if not s.isupper() else s.title()


class Screener:
    def __init__(self, cfg, ebay):
        self.cfg, self.e = cfg, ebay
        sc = cfg.get("screener", {})
        self.lo, self.hi = float(sc.get("psa10_min", 200)), float(sc.get("psa10_max", 1000))
        self.cost = float(sc.get("grading_cost", 40))
        self.target = float(sc.get("target_multiple", 2.0))
        self.fee = float(cfg.get("grading", {}).get("sell_fee_pct", 13.25)) / 100
        self.keep = int(sc.get("max_cards_per_player", 50))
        self.rates = scout.load_card_rates()

    def _search(self, q, cond, lo, hi, pages):
        out = []
        for page in range(pages):
            j = self.e._get("/buy/browse/v1/item_summary/search", {
                "q": q, "category_ids": CATEGORY, "limit": PAGE, "offset": page * PAGE,
                "filter": f"conditionIds:{{{cond}}},price:[{lo}..{hi}],priceCurrency:USD,deliveryCountry:US,"
                          "buyingOptions:{FIXED_PRICE}"}) or {}
            items = j.get("itemSummaries", [])
            out += items
            if len(items) < PAGE:
                break
        return out

    def run_player(self, sp):
        name, sport = sp["name"], sp["sport"]
        grades = (10, 9, 8) if sp.get("psa8") else (10, 9)
        cfg = dict(self.cfg)
        cfg["players"] = [{"name": name, "sport": sport, "years": sp.get("years", "1980-"), "gem_rate_min": 0,
                           "no_cutoff": [{"years": "1980-", "brands": BRANDS}],
                           **({"not_words": sp["not_words"]} if sp.get("not_words") else {})}]
        m = scout.Matcher(cfg)
        p = scout.players_from(cfg)[0]
        labels = defaultdict(Counter)
        set_rate = {}  # GemRate's set-level PSA 10 rate, when the set is one GemRate lists

        def key(t):
            r = m.match(t, p)
            if not r:
                return None
            y, st = r
            num, par = card_key(t, name, st["set"], False)
            if not num:
                return None
            k = (str(y), set_key(st["set"]), num.upper(), par or "base")
            labels[k][clean_label(st["set"])] += 1
            if st.get("gem_rate") is not None:
                set_rate[k] = float(st["gem_rate"])
            return k

        asks, example = defaultdict(lambda: defaultdict(list)), {}
        pages10 = int(sp.get("pages", 15))
        for g in grades:
            # PSA 10s: search below the range too, so a card's cheapest asks aren't cut off
            lo, hi = (self.lo / 3, self.hi * 1.5) if g == 10 else (10, self.hi)
            for it in self._search(f"{name} PSA {g}", GRADED, lo, hi, pages10 if g == 10 else max(3, pages10 // 3)):
                t = it.get("title", "")
                if grade_of(t) != g:
                    continue
                k = key(t)
                if k:
                    asks[k][f"psa{g}"].append(total(it))
                    example.setdefault(k, t)
        # Cards whose PSA 10s could be in range; then raw copies of each, searched directly
        cands = [k for k, v in asks.items() if len(v["psa10"]) >= 2 and self.lo * 0.8 <= low3(v["psa10"]) <= self.hi * 1.1]
        cands.sort(key=lambda k: -len(asks[k]["psa10"]))
        for k in cands[: int(sp.get("raw_lookups", 150))]:
            label = labels[k].most_common(1)[0][0]
            par = "" if k[3] == "base" else re.sub(r"[/+]", " ", k[3]).strip()
            try:
                items = self._search(f"{k[0]} {label} {name} {k[2]} {par}".strip(), UNGRADED, 1, self.hi, 1)
            except OutOfBudget:
                break
            for it in items:
                t = it.get("title", "")
                if not NOT_RAW.search(t) and key(t) == k:
                    asks[k]["raw"].append(total(it))
        rows = []
        for k in cands:
            v = asks[k]
            p10, raw = low3(v["psa10"]), low3(v["raw"])
            if p10 is None or not (self.lo <= p10 <= self.hi) or raw is None:
                continue
            p9, p8 = low3(v["psa9"]), low3(v["psa8"])
            label = labels[k].most_common(1)[0][0]
            cr = scout.card_rate(self.rates, {"player": name, "year": k[0], "set_name": label, "card": k[2],
                                              "parallel": k[3]})
            gem = cr["rate"] if cr and cr.get("rate") is not None else None
            gem_src = "card" if gem is not None else None
            if gem is None and k in set_rate:  # fall back to the whole set's rate
                gem, gem_src = round(set_rate[k], 1), "set"
            cost = raw + self.cost
            # Expected resale after eBay's fee: PSA 10 at the gem rate, otherwise the next grade down
            ev = None
            if gem is not None:
                fallback = p9 if p9 is not None else p8
                if fallback is not None:
                    ev = round((gem / 100 * p10 + (1 - gem / 100) * fallback) * (1 - self.fee), 2)
            row = {
                "player": name, "year": k[0], "set": label, "num": k[2], "par": "" if k[3] == "base" else k[3],
                "card": f"{k[0]} {label} #{k[2]}" + ("" if k[3] == "base" else f" {k[3].replace('+', ' ')}"),
                "p10": p10, "n10": len(v["psa10"]), "p9": p9, "n9": len(v["psa9"]), "p8": p8, "n8": len(v["psa8"]),
                "raw": raw, "nraw": len(v["raw"]), "cost": round(cost, 2),
                "mult": round(p10 / cost, 2), "pass": p10 >= self.target * cost,
                "mult9": round(p9 / cost, 2) if p9 is not None else None,
                "p8_covers": (p8 >= cost) if (sp.get("psa8") and p8 is not None) else None,
                "profit10": round(p10 * (1 - self.fee) - cost, 2),
                "gem": gem, "gem_src": gem_src, "graded": cr["graded"] if cr else None, "ev": ev,
                "ev_profit": round(ev - cost, 2) if ev is not None else None,
                "example": example.get(k, ""),
            }
            q = f"{k[0]} {label} {name} {k[2]} {row['par'].replace('+', ' ')}".strip()
            row["links"] = {
                "raw": f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(q + ' -psa -bgs -sgc -cgc')}&LH_BIN=1&_sop=15",
                "sold10": f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(q + ' PSA 10')}&LH_Sold=1&LH_Complete=1",
                "soldraw": f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(q + ' -psa -bgs -sgc -cgc')}&LH_Sold=1&LH_Complete=1",
            }
            rows.append(row)
        # Best first: passes the target, then by multiple; keep the top N
        rows.sort(key=lambda r: (not r["pass"], -r["mult"]))
        return {"name": name, "sport": sport, "psa8": bool(sp.get("psa8")), "updated": now_iso(),
                "cards": rows[: self.keep], "found": len(rows), "calls": self.e.used}


def main():
    cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml")))
    sc = cfg.get("screener", {})
    players = sc.get("players", [])
    data = {"players": {}}
    if os.path.exists(OUT):
        data = json.load(open(OUT))
    want = [a for a in sys.argv[1:]]
    if want:
        todo = [p for p in players if p["name"] in want]
    else:  # the players refreshed longest ago
        todo = sorted(players, key=lambda p: (data["players"].get(p["name"]) or {}).get("updated", ""))
        todo = todo[: int(sc.get("players_per_run", 3))]
    from tools.check_item import _NoStore
    ebay = Ebay(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"], _NoStore(),
                budget=int(sc.get("calls_per_run", 600)))
    s = Screener(cfg, ebay)
    for p in todo:
        t = time.time()
        before = ebay.used
        try:
            res = s.run_player(p)
        except OutOfBudget:
            print("Out of eBay calls for this run")
            break
        data["players"][p["name"]] = res
        print(f"{p['name']}: {res['found']} cards in range, kept {len(res['cards'])}, "
              f"{sum(r['pass'] for r in res['cards'])} pass {s.target:g}x; {ebay.used - before} calls, {time.time() - t:.0f}s")
    # drop players no longer on the list
    names = {p["name"] for p in players}
    data["players"] = {k: v for k, v in data["players"].items() if k in names}
    data["settings"] = {"psa10_min": s.lo, "psa10_max": s.hi, "grading_cost": s.cost,
                        "target_multiple": s.target, "sell_fee_pct": round(s.fee * 100, 2), "max_cards": s.keep,
                        "price_source": "Average of the 3 cheapest Buy It Now asks (shipping included)"}
    data["updated"] = now_iso()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(data, open(OUT, "w"), indent=0)


if __name__ == "__main__":
    main()
