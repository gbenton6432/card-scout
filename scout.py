"""
Card Scout scanner.

Keeps a database of every raw listing for your players in sets at or above your gem rate
cutoff, and publishes the ones worth a look to the dashboard.

Each run (hourly):
  1. New listings: one newest-first search per player, stopping at listings already seen.
  2. Inventory sweep: works through every player-year, a page at a time, with whatever
     eBay calls are left in today's budget. Finds older listings and notices ones that ended.
     Player-years with more than 10,000 results are split into price bands.
  3. Measures centering on photos not measured yet (each photo once, ever).
  4. Writes per-player data files for the dashboard, then sends alerts.

Run:  EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=... python scout.py
Test without eBay keys:  python scout.py --demo
"""
import json, math, os, re, statistics, sys, time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

import requests
import yaml

import alerts
from cards import card_key
from centering import measure
from ebay import Ebay, OutOfBudget, PAGE, MAX_RESULTS, big_image
from store import Store, now_iso

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_DATA = os.path.join(HERE, "site", "data")
TCG_SPORTS = {"pokemon", "tcg", "magic", "mtg", "one piece", "lorcana", "yugioh", "yu-gi-oh"}
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


# ── Sets and title matching ─────────────────────────────────────────────

def _norm(t):
    return " " + re.sub(r"[^a-z0-9]+", " ", t.lower()).strip() + " "


def _aliases(name):
    """Ways sellers write a set name: 'Panini Donruss Optic' -> also 'Donruss Optic', 'Optic'."""
    n = _norm(name).strip()
    out = {n}
    if n.startswith("panini "):
        out.add(n[7:])
    if "disney100" in n:  # GemRate writes DISNEY100; sellers write "Disney 100"
        out.add(n.replace("disney100", "disney 100"))
    if " sapphire edition" in n:  # sellers often drop "Edition"
        out |= {x.replace(" sapphire edition", " sapphire") for x in list(out)}
    if " fifa " in f" {n} ":  # sellers often leave out "FIFA"
        out |= {re.sub(r"\bfifa ", "", x) for x in list(out)}
    # Sellers' short forms of competition names
    for full, short in (("uefa women s champions league", "uwcl"), ("uefa champions league", "ucl")):
        out |= {x.replace(full, short) for x in list(out) if full in x}
    for full, short in (("donruss optic", "optic"), ("topps chrome update", "chrome update")):
        for x in list(out):
            if x.startswith(full):
                out.add(short + x[len(full):])
    return [f" {x} " for x in out if len(x) >= 4]


def _years(spec):
    """'2018-' -> (2018, 9999); '2003-2016' -> (2003, 2016); None -> all years."""
    if spec in (None, ""):
        return 0, 9999
    spec = str(spec).replace(" ", "")
    if "-" in spec:
        a, b = spec.split("-", 1)
        return int(a), int(b) if b else 9999
    return int(spec), int(spec)


def load_gem_rates():
    try:
        return yaml.safe_load(open(os.path.join(HERE, "gem_rates.yaml"))).get("sets") or []
    except FileNotFoundError:
        return []


class Matcher:
    """Finds a listing's year and set from its title and checks the set's gem rate."""

    def __init__(self, cfg):
        self.cutoff = float(cfg.get("gem_rate_min", 0) or 0)
        # Per-sport overrides, e.g. {disney: 0} to keep every Topps Chrome Disney set
        by_sport = {str(k).lower(): float(v or 0) for k, v in (cfg.get("gem_rate_min_by_sport") or {}).items()}
        self.sets = defaultdict(list)  # (sport, year) -> [set]
        merged = {}
        # Sets listed in config.yaml without a rate count until GemRate's rate arrives
        for st in list(cfg.get("sets") or []) + load_gem_rates():
            key = (str(st.get("sport", "")).lower(), int(str(st["year"])[:4]), st["set"].lower())
            if key not in merged or st.get("gem_rate") is not None:
                merged[key] = st
        for (sport, year, _), st in merged.items():
            rate = st.get("gem_rate")
            cut = by_sport.get(sport, self.cutoff)
            st = dict(st, _aliases=_aliases(st["set"]),
                      _ok=rate is None or cut <= 0 or float(rate) >= cut)
            self.sets[(sport, year)].append(st)

    @staticmethod
    def ok(player, st):
        """Gem-rate check; a player's own gem_rate_min (e.g. 0) replaces the sport cutoff."""
        for lo, hi, prefixes in player.get("open") or []:   # e.g. every 2024 Panini set
            if lo <= int(str(st["year"])[:4]) <= hi and st["set"].lower().startswith(prefixes):
                return True
        if player.get("gem_min") is None:
            return st["_ok"]
        rate = st.get("gem_rate")
        return rate is None or player["gem_min"] <= 0 or float(rate) >= player["gem_min"]

    @staticmethod
    def allowed(player, st):
        """Brand rule: the set must start with one of the player's allowed set prefixes
        (or one of a no_cutoff entry's brands for that year)."""
        name = st["set"].lower()
        for lo, hi, prefixes in player.get("open") or []:
            if lo <= int(str(st["year"])[:4]) <= hi and name.startswith(prefixes):
                return True
        return not player["sets"] or any(name.startswith(p) for p in player["sets"])

    def qualifying_years(self, player):
        if player.get("rules"):  # title rules: every year in the player's range is searched
            return list(range(max(player["lo"], 2000), min(player["hi"], datetime.now().year) + 1))
        years = {y for (sp, y), sts in self.sets.items()
                 if sp == player["sport"] and player["lo"] <= y <= player["hi"]
                 and any(self.ok(player, s) and self.allowed(player, s) for s in sts)}
        for lo, hi, _ in player.get("open") or []:  # no_cutoff years are always searched
            years |= {y for y in range(max(lo, player["lo"]), min(hi, player["hi"], datetime.now().year) + 1)}
        return sorted(years)

    def match(self, title, player):
        """(year, set dict) if the title is from a qualifying set, else None."""
        t = _norm(title)
        if any(f" {w} " in t for w in player.get("not_words") or []):
            return None
        if player.get("rules"):
            return self.match_rules(t, title, player)
        for y in dict.fromkeys(int(m) for m in YEAR_RE.findall(title)):
            if not (player["lo"] <= y <= player["hi"]):
                continue
            sets = self.sets.get((player["sport"], y))
            best, best_len = None, 0
            for st in sets or []:
                for a in st["_aliases"]:
                    if a in t and len(a) > best_len and not any(_norm(w) in t for w in st.get("exclude") or []):
                        best, best_len = st, len(a)
            if best and self.ok(player, best) and self.allowed(player, best):
                return y, best
            # no_cutoff year: any card from those brands counts, even a set GemRate doesn't list
            for lo, hi, prefixes in player.get("open") or []:
                if lo <= y <= hi and (best is None or best["set"].lower().startswith(prefixes)):
                    brand = next((b for b in prefixes if f" {b} " in t), None)
                    if brand:
                        return y, {"sport": player["sport"], "year": y, "gem_rate": None,
                                   "set": self.title_set(title, y, player, brand.title())}
            if best:
                return None
        return None

    @staticmethod
    def title_set(title, y, player, fallback):
        """Set name from the title: the words between the year and the player's first name."""
        m = re.search(rf"\b{y}(?:-\d{{2,4}})?\s+(.{{3,60}}?)\s+{re.escape(player['name'].split()[0])}\b", title, re.I)
        return m.group(1).strip(" -,") if m and len(m.group(1).split()) <= 8 else fallback


    def match_rules(self, t, title, player):
        """Player-specific rule: the title has one brand word and one 'also' word (no gem-rate
        cutoff). The set is GemRate's name when one matches, else e.g. 'Topps Chrome'."""
        r = player["rules"]
        brand = next((b for b in r["brands"] if f" {b} " in t), None)
        also = next((a for a in r["also"] if f" {a} " in t), None)
        if not brand or not also:
            return None
        for y in dict.fromkeys(int(m) for m in YEAR_RE.findall(title)):
            if not (player["lo"] <= y <= player["hi"]):
                continue
            best, best_len = None, 0
            for st in self.sets.get((player["sport"], y)) or []:
                for a in st["_aliases"]:
                    if a in t and len(a) > best_len:
                        best, best_len = st, len(a)
            if best:
                return y, best
            # Set name = the words between the year and the player's name, e.g.
            # "2023 Panini Prizm Women's World Cup Trinity Rodman" -> "Panini Prizm Women's World Cup"
            m = re.search(rf"\b{y}(?:-\d{{2,4}})?\s+(.{{3,60}}?)\s+{re.escape(player['name'].split()[0])}\b", title, re.I)
            if m and len(m.group(1).split()) <= 8:
                label = m.group(1).strip(" -,")
            else:
                fix = {"usa": "USA", "uswnt": "USWNT"}
                label = " ".join(fix.get(w, w.title()) for w in f"{brand} {also}".split())
            return y, {"sport": player["sport"], "year": y, "set": label, "gem_rate": None}
        return None


# ── Turning eBay results into listings ──────────────────────────────────

def players_from(cfg):
    out = []
    brands = cfg.get("set_brands") or {}
    for p in cfg.get("players") or []:
        lo, hi = _years(p.get("years"))
        sport = str(p.get("sport", "")).lower()
        jersey = p.get("jersey", [])
        sets_only = p.get("sets_only") or brands.get(sport) or brands.get("sports" if sport not in TCG_SPORTS | {"disney"} else sport) or []
        out.append({"name": p["name"], "sport": sport, "lo": lo, "hi": hi,
                    "jersey": [int(j) for j in (jersey if isinstance(jersey, list) else [jersey])],
                    "search": p.get("search") or p["name"],
                    **{k: float(p[k]) for k in ("min_price", "max_price") if p.get(k) is not None},
                    "sets": [x.lower() for x in sets_only],
                    "not_words": [_norm(w).strip() for w in p.get("not_words") or []],
                    "gem_min": float(p["gem_rate_min"]) if p.get("gem_rate_min") is not None else None,
                    # no_cutoff: [{years: 2024, brands: [Panini, ...]}] = those sets skip the gem-rate cutoff
                    "open": [(*_years(o["years"]), tuple(_norm(b).strip() for b in o["brands"]))
                             for o in p.get("no_cutoff") or []],
                    "rules": {"brands": [_norm(x).strip() for x in p["title_rule"]["brands"]],
                              "also": [_norm(x).strip() for x in p["title_rule"]["also"]]} if p.get("title_rule") else None,
                    "category": "tcg" if sport in TCG_SPORTS else "nonsport" if sport == "disney" else "sports"})
    return out


def neg_words(d):
    return " ".join(f'-"{w}"' if " " in w else f"-{w}" for w in d.get("exclude_words") or [])


def consigner_reason(it, cons):
    title = (it.get("title") or "").lower()
    seller = (it.get("seller") or {})
    if (seller.get("username") or "").lower() in {s.lower() for s in cons.get("blocked_sellers") or []}:
        return "blocked seller"
    if any(w.lower() in title for w in cons.get("blocked_title_words") or []):
        return "title keyword"
    return seller_reason(int(seller.get("feedbackScore") or 0), seller.get("feedbackPercentage"), cons)


def seller_reason(score, pct, cons):
    """Seller checks: low positive rating, or too little feedback (eBay doesn't share account age,
    so a very low feedback score stands in for 'new seller')."""
    max_fb = int(cons.get("max_seller_feedback") or 0)
    if max_fb and score > max_fb:
        return "high-volume seller (feedback)"
    min_pct = float(cons.get("min_positive_pct") or 0)
    if min_pct and pct not in (None, "") and float(pct) < min_pct:
        return "seller rating below minimum"
    if score < int(cons.get("min_seller_feedback") or 0):
        return "new or unproven seller"
    return None


def to_listing(it, player, year, st):
    imgs = [big_image((it.get("image") or {}).get("imageUrl"))]
    imgs += [big_image(a.get("imageUrl")) for a in it.get("additionalImages", [])]
    imgs = [u for u in imgs if u]
    seller = it.get("seller") or {}
    opts = it.get("buyingOptions") or []
    # Auctions are priced at the current bid; a Buy It Now option on an auction is kept separately
    if "AUCTION" in opts:
        price = it.get("currentBidPrice") or it.get("price") or {}
        bin_price = (it.get("price") or {}).get("value") if "FIXED_PRICE" in opts and it.get("currentBidPrice") else None
    else:
        price, bin_price = it.get("price") or {}, None
    ship = (it.get("shippingOptions") or [{}])[0].get("shippingCost")
    number, parallel = card_key(it.get("title", ""), player["name"], st["set"], player["category"] == "tcg")
    return {
        "id": it.get("itemId"), "player": player["name"], "sport": player["sport"], "year": str(year),
        "set_name": st["set"], "gem": st.get("gem_rate"), "title": it.get("title", ""),
        "url": it.get("itemWebUrl"), "img": imgs[0] if imgs else "", "n_img": len(imgs),
        "price": float(price.get("value", 0) or 0),
        "bin": float(bin_price) if bin_price else None,
        "ship": float(ship.get("value", 0) or 0) if ship else None,
        "auction": int("AUCTION" in (it.get("buyingOptions") or [])),
        "offers": int("BEST_OFFER" in (it.get("buyingOptions") or [])),
        "ends": it.get("itemEndDate"), "listed": it.get("itemCreationDate"),
        "seller": seller.get("username", ""), "feedback": int(seller.get("feedbackScore") or 0),
        "fb_pct": float(seller["feedbackPercentage"]) if seller.get("feedbackPercentage") not in (None, "") else None,
        "card": number, "parallel": parallel,
        # 1 = several copies for sale (a "pick your card" listing, or quantity over 1); 0 = one copy;
        # None = not known yet (Buy It Now listings get their quantity looked up later)
        "multi": 1 if it.get("itemGroupHref") or it.get("itemGroupType") else (0 if "AUCTION" in opts else None),
    }


class Scanner:
    def __init__(self, cfg, store, ebay):
        self.cfg, self.store, self.ebay = cfg, store, ebay
        self.d, self.cons = cfg.get("defaults", {}), cfg.get("consigners", {})
        self.matcher = Matcher(cfg)
        self.players = players_from(cfg)
        self.neg = neg_words(self.d)
        self.stats = Counter()

    def ingest(self, items, player):
        """Store the qualifying listings from one page of results."""
        for it in items:
            if consigner_reason(it, self.cons):
                self.stats["consigner"] += 1
                continue
            m = self.matcher.match(it.get("title", ""), player)
            if not m:
                self.stats["other set or under cutoff"] += 1
                continue
            if self.store.upsert(to_listing(it, player, *m)):
                self.stats["new listings"] += 1

    def price_range(self, p):
        """A player's own min_price / max_price, else the defaults."""
        return (p.get("min_price", self.d.get("min_price")), p.get("max_price", self.d.get("max_price")))

    # 1. New listings
    def new_listings(self, max_pages=3):
        for p in self.players:
            for page in range(max_pages):
                items, _ = self.ebay.search(f"{p['search']} {self.neg}", p["category"], self.d, self.cons,
                                            *self.price_range(p), offset=page * PAGE)
                seen_before = sum(1 for it in items if self.store.known(it.get("itemId")))
                self.ingest(items, p)
                if seen_before or len(items) < PAGE:
                    break
            self.store.commit()

    # 2. Inventory sweep
    def ensure_units(self):
        want = set()
        for p in self.players:
            for y in self.matcher.qualifying_years(p):
                want.add((p["name"], str(y)))
        have = {(r["player"], r["year"]) for r in self.store.db.execute("SELECT player, year FROM units")}
        by_name = {p["name"]: p for p in self.players}
        for player, year in want - have:
            lo, hi = self.price_range(by_name[player])
            self.store.db.execute("INSERT INTO units(key,player,year,lo,hi) VALUES(?,?,?,?,?)",
                                  (f"{player}|{year}|{lo}|{hi}", player, year, lo, hi))
        # a player's price limits changed: restart those sweeps with the new range. A player-year
        # may be split into several price bands, so compare the whole span they cover.
        spans = {}
        for r in self.store.db.execute("SELECT player, year, lo, hi FROM units"):
            k = (r["player"], r["year"])
            lo0, hi0 = spans.get(k, (r["lo"], r["hi"]))
            spans[k] = (min(lo0 or 0, r["lo"] or 0), max(hi0 or 0, r["hi"] or 0))
        for (player, year), (lo0, hi0) in spans.items():
            p = by_name.get(player)
            if not p or (player, year) not in want:
                continue
            lo, hi = self.price_range(p)
            if (float(lo0 or 0), float(hi0 or 0)) != (float(lo or 0), float(hi or 0)):
                self.store.db.execute("DELETE FROM units WHERE player=? AND year=?", (player, year))
                self.store.db.execute("INSERT INTO units(key,player,year,lo,hi) VALUES(?,?,?,?,?)",
                                      (f"{player}|{year}|{lo}|{hi}", player, year, lo, hi))
        for player, year in have - want:  # player removed or set no longer qualifies
            self.store.db.execute("DELETE FROM units WHERE player=? AND year=?", (player, year))
        self.store.commit()

    def sweep(self):
        by_name = {p["name"]: p for p in self.players}
        run_start = now_iso()
        while True:
            u = self.store.db.execute(
                "SELECT * FROM units ORDER BY (finished IS NOT NULL), finished, key LIMIT 1").fetchone()
            if not u or (u["finished"] and u["finished"] >= run_start and u["next_offset"] == 0):
                return  # nothing to do, or every unit already swept during this run
            p = by_name[u["player"]]
            if u["next_offset"] == 0:
                self.store.db.execute("UPDATE units SET started=? WHERE key=?", (now_iso(), u["key"]))
            items, total = self.ebay.search(f"{u['year']} {p['search']} {self.neg}", p["category"], self.d,
                                            self.cons, u["lo"], u["hi"], offset=u["next_offset"])
            hi_cap = u["hi"] if u["hi"] is not None else 100000
            if total > MAX_RESULTS - PAGE and hi_cap - (u["lo"] or 0) > 2:
                # Too many results to page through: split into two price bands
                lo, hi = u["lo"] or 0, hi_cap
                mid = round(lo + (hi - lo) / 2, 2)
                self.store.db.execute("DELETE FROM units WHERE key=?", (u["key"],))
                for a, b in ((lo, mid), (mid + 0.01, hi)):
                    self.store.db.execute("INSERT OR IGNORE INTO units(key,player,year,lo,hi) VALUES(?,?,?,?,?)",
                                          (f"{p['name']}|{u['year']}|{a}|{b}", p["name"], u["year"], a, b))
                self.store.commit()
                continue
            self.ingest(items, p)
            nxt = u["next_offset"] + PAGE
            if len(items) < PAGE or nxt >= min(total, MAX_RESULTS):
                # Finished this unit: anything here not seen since it started has ended
                started = self.store.db.execute("SELECT started FROM units WHERE key=?", (u["key"],)).fetchone()[0]
                gone = self.store.db.execute(
                    "UPDATE listings SET active=0 WHERE active=1 AND player=? AND year=? AND price BETWEEN ? AND ? "
                    "AND last_seen < ?", (p["name"], u["year"], u["lo"] or 0, u["hi"] or 1e12, started)).rowcount
                self.stats["ended (found by sweep)"] += gone
                self.store.db.execute("UPDATE units SET total=?, next_offset=0, finished=? WHERE key=?",
                                      (total, now_iso(), u["key"]))
            else:
                self.store.db.execute("UPDATE units SET total=?, next_offset=? WHERE key=?", (total, nxt, u["key"]))
            self.store.commit()

    def end_auctions(self):
        n = self.store.db.execute("UPDATE listings SET active=0 WHERE active=1 AND auction=1 AND ends < ?",
                                  (now_iso(),)).rowcount
        self.stats["auctions ended"] += n
        self.store.commit()


# ── 3. Centering, measured once per listing ─────────────────────────────

def measure_new(store, minutes, demo=False, read_serials=True):
    """Centering (and the serial stamp, for sports cards) for photos not measured yet."""
    deadline = time.time() + minutes * 60
    http = requests.Session()
    if read_serials:
        try:
            from serials import read_serial
        except ImportError:
            print("Serial reading skipped: rapidocr-onnxruntime isn't installed")
            read_serials = False

    def one(row):
        if time.time() > deadline:
            return row["id"], None
        try:
            if row["img"].startswith("http"):
                r = http.get(row["img"], timeout=30)
                r.raise_for_status()
                data = r.content
            else:
                data = open(os.path.join(HERE, "site", row["img"]), "rb").read()
            c = measure(data)
            # Skip the serial read for photos that clearly fail centering; they're dropped anyway
            clearly_off = c.get("confidence", 0) >= 0.6 and c.get("worst", 0) > 56.5
            if read_serials and row["sport"] not in TCG_SPORTS and not clearly_off:
                try:
                    sn = read_serial(data, c.get("quad"))
                    if sn:
                        c["sn"] = sn
                except Exception as e:
                    c["sn_error"] = type(e).__name__
            return row["id"], c
        except Exception as e:
            return row["id"], {"confidence": 0, "note": f"photo didn't load ({type(e).__name__})"}

    done = 0
    while time.time() < deadline:
        rows = store.db.execute("SELECT id, img, sport FROM listings WHERE active=1 AND measured_at IS NULL "
                                "ORDER BY (c IS NOT NULL), (COALESCE(json_extract(c, '$.confidence'), 0) > 0), "
                                "first_seen DESC LIMIT 400").fetchall()
        if not rows:
            break
        with ThreadPoolExecutor(8) as pool:
            for iid, c in pool.map(one, rows):
                if c is None:
                    continue
                store.db.execute("UPDATE listings SET c=?, measured_at=? WHERE id=?", (json.dumps(c), now_iso(), iid))
                done += 1
        store.commit()
    left = store.db.execute("SELECT COUNT(*) FROM listings WHERE active=1 AND measured_at IS NULL").fetchone()[0]
    print(f"Measured {done} photos; {left} waiting for the next run")
    return done, left


# ── 4. Dashboard data ───────────────────────────────────────────────────

def load_card_rates():
    """Card-level PSA gem rates from GemRate (card_rates.json), indexed by player/year/set."""
    idx = defaultdict(list)
    try:
        rows = json.load(open(os.path.join(HERE, "card_rates.json")))["cards"]
    except FileNotFoundError:
        return idx
    for player, year, set_name, num, parallel, total, gems, psa9 in rows:
        idx[(player, str(year), set_name)].append(
            (re.sub(r"[\s#-]", "", str(num)).upper(), parallel, set(_norm(parallel).split()) - {"base"}, total, gems, psa9))
    return idx


def card_rate(idx, r):
    """GemRate's numbers for this exact card (number + parallel), if it can be identified."""
    if not r["card"]:
        return None
    cands = [c for c in idx.get((r["player"], r["year"], r["set_name"]), []) if c[0] == r["card"]]
    if not cands:
        return None
    want = {w for w in re.split(r"[+]", r["parallel"] or "") if w and w != "base" and not w.startswith("/")}
    want = {{"autograph": "auto", "xfractor": "x"}.get(w, w) for w in want}
    best, score = None, 0
    for c in cands:
        have = {{"autograph": "auto", "fractor": "x"}.get(w, w) for w in c[2]}
        if not want and not have:
            s_ = 2.0
        elif want or have:
            s_ = len(want & have) / max(1, len(want | have))
        if s_ > score:
            best, score = c, s_
    if not best or score < 0.5:
        return None
    _, parallel, _, total, gems, psa9 = best
    return {"rate": round(100 * gems / total, 1) if total else None, "graded": total, "psa10": gems,
            "psa9": psa9, "parallel": parallel}


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def off_center(c, photos):
    bar = float(photos.get("psa10_max_split", 55)) + float(photos.get("tolerance", 1.5))
    return c.get("confidence", 0) >= float(photos.get("min_confidence_to_drop", 0.6)) and c.get("worst", 0) > bar


def multi_copies(data):
    """True when an eBay item lookup shows more than one copy for sale."""
    for a in (data or {}).get("estimatedAvailabilities") or []:
        if a.get("availabilityThresholdType") == "MORE_THAN":
            return True
        for k in ("estimatedAvailableQuantity", "estimatedRemainingQuantity"):
            if (a.get(k) or 0) > 1:
                return True
    return False


def refresh_listings(store, ebay, cfg, calls):
    """Look listings up again, one eBay call each (eBay only gives its 20-at-a-time lookup to
    approved apps): anything sold or ended is removed, and Buy It Now quantities are filled in
    (several copies = dropped). Order: quantity not known yet, then cards with PSA 10 centering,
    then everything else, longest since last checked first."""
    photos = cfg.get("photos", {})
    bar = float(photos.get("psa10_max_split", 55)) + float(photos.get("tolerance", 1.5))
    rows = store.db.execute(
        "SELECT id, auction FROM listings WHERE active=1 ORDER BY "
        "CASE WHEN multi IS NULL AND auction=0 THEN 0 "
        "WHEN json_extract(c,'$.confidence') >= 0.35 AND json_extract(c,'$.worst') <= ? THEN 1 ELSE 2 END, "
        "COALESCE(checked_at, '') LIMIT ?", (bar, max(0, calls))).fetchall()
    now, gone, several, done = now_iso(), 0, 0, 0
    for r in rows:
        try:
            it = ebay.item(r["id"])
        except OutOfBudget:
            break
        except Exception as e:  # one bad lookup shouldn't stop the run
            print("Listing check skipped:", e)
            continue
        av = ((it or {}).get("estimatedAvailabilities") or [{}])[0]
        if it is None or av.get("estimatedAvailabilityStatus") == "OUT_OF_STOCK" or \
                (it.get("itemEndDate") and it["itemEndDate"] < now):
            store.db.execute("UPDATE listings SET active=0, checked_at=? WHERE id=?", (now, r["id"]))
            gone += 1
            continue
        m = None if r["auction"] else int(multi_copies(it))
        several += m or 0
        store.db.execute("UPDATE listings SET checked_at=?, last_seen=?, multi=COALESCE(?, multi) WHERE id=?",
                         (now, now, m, r["id"]))
        done += 1
    store.commit()
    print(f"Re-checked {done + gone} listings: {gone} sold or ended, {several} with several copies")


def publish(store, cfg, scanner, run_stats):
    photos, cons = cfg.get("photos", {}), cfg.get("consigners", {})
    rows = [dict(r) for r in store.db.execute("SELECT * FROM listings WHERE active=1")]
    for r in rows:
        r["c"] = json.loads(r["c"]) if r["c"] else None
    max_per = int(cons.get("max_listings_per_seller") or 0)
    per_seller = Counter(r["seller"] for r in rows)
    # The same seller listing the same card more than once = several copies for sale
    def same_card(r):
        if r["card"]:
            return (r["seller"], r["player"], r["year"], r["set_name"], r["card"], r["parallel"] or "")
        return (r["seller"], re.sub(r"[^a-z0-9]+", " ", (r["title"] or "").lower()).strip())
    copies = Counter(same_card(r) for r in rows) if cons.get("drop_multiple_copies", True) else Counter()
    shown, dropped = [], Counter()
    for r in rows:
        reason = seller_reason(r["feedback"] or 0, r.get("fb_pct"), cons) or (
            "blocked seller" if (r["seller"] or "").lower() in {x.lower() for x in cons.get("blocked_sellers") or []} else None)
        if reason:
            dropped[reason] += 1
        elif max_per and per_seller[r["seller"]] > max_per:
            dropped["high-volume seller (listing count)"] += 1
        elif cons.get("drop_multiple_copies", True) and (r.get("multi") == 1 or copies[same_card(r)] > 1):
            dropped["several copies for sale"] += 1
        elif r["c"] and off_center(r["c"], photos):
            dropped["clear photo, worse than PSA 10 centering"] += 1
        else:
            shown.append(r)

    # Typical asking price for the same card (same player, year, set, number, parallel)
    groups = defaultdict(list)
    for r in rows:
        if not r["auction"]:  # typical ask = Buy It Now asks only, not current bids
            # No card number in the title: compare only with other un-numbered listings of the
            # same player, year, set and parallel (approximate, shown with ≈)
            groups[(r["player"], r["year"], r["set_name"], r["card"] or "~", r["parallel"])].append(
                r["price"] + (r["ship"] or 0))
    typical = {k: statistics.median(v) for k, v in groups.items() if len(v) >= 3}

    cidx = load_card_rates()
    os.makedirs(os.path.join(SITE_DATA, "players"), exist_ok=True)
    by_player = defaultdict(list)
    for r in shown:
        c = r["c"] or {}
        t = typical.get((r["player"], r["year"], r["set_name"], r["card"] or "~", r["parallel"]))
        ratio = round((r["price"] + (r["ship"] or 0)) / t, 2) if t else None
        cr = card_rate(cidx, r)
        by_player[r["player"]].append({
            "id": r["id"], "t": r["title"], "u": r["url"], "i": r["img"], "n": r["n_img"],
            "p": r["price"], "b": r.get("bin"), "s": r["ship"], "a": r["auction"], "e": r["ends"], "l": r["listed"], "o": r["offers"],
            "f": r["first_seen"], "sl": r["seller"], "fb": r["feedback"], "y": r["year"],
            "st": r["set_name"], "g": r["gem"], "k": r["card"], "pr": r["parallel"],
            # x = vs the same card (alerts use it); xa = approximate, for titles without a card number
            "x": ratio if r["card"] else None, "xa": None if r["card"] else ratio,
            "cg": [cr["rate"], cr["graded"], cr["psa10"], cr["psa9"], cr["parallel"]] if cr else None,
            "c": {"lr": c.get("lr"), "tb": c.get("tb"), "w": c.get("worst"), "cf": c.get("confidence"),
                  "q": c.get("quad"), "note": c.get("note"), "sn": c.get("sn")} if r["c"] else None,
        })
    players = []
    for p in scanner.players:
        items = by_player.get(p["name"], [])
        fn = f"players/{slug(p['name'])}.json"
        json.dump(items, open(os.path.join(SITE_DATA, fn), "w"), separators=(",", ":"))
        players.append({"name": p["name"], "sport": p["sport"], "file": fn, "count": len(items), "jersey": p["jersey"]})

    units = [dict(u) for u in store.db.execute("SELECT * FROM units")]
    sweep_calls = sum(max(1, math.ceil(min(u["total"] or PAGE, MAX_RESULTS) / PAGE)) for u in units)
    daily_sweep = max(1, budget_for_day(cfg) - 24 * len(scanner.players))
    index = {
        "updated": now_iso(), "players": players, "min_photos": photos.get("min_photos", 2),
        "demo": bool(getattr(scanner.ebay, "is_demo", False)),
        "grading": cfg.get("grading", {}),
        "stats": {
            "active": len(rows), "shown": len(shown),
            "measured": sum(1 for r in rows if r["c"]), "dropped": dict(dropped),
            "consigners_skipped": run_stats.get("consigner", 0),
            "units": len(units), "units_done": sum(1 for u in units if u["finished"]),
            "sweep_days": round(sweep_calls / daily_sweep, 1),
            "listings_on_ebay": sum(u["total"] or 0 for u in units),
            "calls_today": store.calls_today(),
        },
    }
    json.dump(index, open(os.path.join(SITE_DATA, "index.json"), "w"), separators=(",", ":"))
    print(f"Published {len(shown)} of {len(rows)} active listings; full sweep ≈ {index['stats']['sweep_days']} days "
          f"({sweep_calls} calls); dropped {dict(dropped)}")
    return [x for items in by_player.values() for x in items]


# ── Budget ──────────────────────────────────────────────────────────────

def budget_for_day(cfg):
    return int(cfg.get("defaults", {}).get("daily_call_limit", 5000)) - 200  # headroom for reminders/manual runs


def budget_for_run(cfg, store):
    """Spread what's left of today's calls evenly over the hours left today (UTC)."""
    left = budget_for_day(cfg) - store.calls_today()
    hours_left = 24 - datetime.now(timezone.utc).hour
    return max(0, left // max(1, hours_left))


def recheck_calls(cfg):
    """eBay calls per run for re-checking listings (20 listings per call)."""
    return int(cfg.get("consigners", {}).get("recheck_calls_per_run",
                                              cfg.get("consigners", {}).get("copy_checks_per_run", 80)))


def set_output(**kv):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            for k, v in kv.items():
                f.write(f"{k}={v}\n")


def recheck_players(store, cfg):
    """When the player list or set rules change, hide stored listings that no longer qualify
    (e.g. college cards after a player is limited to pro cards)."""
    import hashlib
    key = hashlib.sha1(json.dumps([cfg.get("players"), cfg.get("gem_rate_min"), cfg.get("gem_rate_min_by_sport"),
                                   cfg.get("set_brands"), cfg.get("sets")], sort_keys=True, default=str).encode()).hexdigest()
    if store.get("players_key") == key:
        return
    m = Matcher(cfg)
    players = {p["name"]: p for p in players_from(cfg)}
    gone = []
    for r in store.db.execute("SELECT id, player, title FROM listings WHERE active=1"):
        p = players.get(r["player"])
        if p is None or m.match(r["title"], p) is None:
            gone.append((r["id"],))
    store.db.executemany("UPDATE listings SET active=0 WHERE id=?", gone)
    store.put("players_key", key)
    store.commit()
    print(f"Settings changed: {len(gone)} stored listings no longer qualify and were hidden")


def main():
    cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml")))
    demo = "--demo" in sys.argv or "--demo-scale" in sys.argv
    if not demo and not (os.environ.get("EBAY_CLIENT_ID") and os.environ.get("EBAY_CLIENT_SECRET")) \
            and os.environ.get("GITHUB_ACTIONS"):
        # No eBay keys yet: publish the sample dashboard instead of failing every run
        print("No eBay keys yet (README step 3), so publishing the demo dashboard.")
        demo = True
    # Demo runs use their own database so sample cards never mix with real ones
    store = Store(os.path.join(HERE, "cache", "demo.db" if demo else "listings.db"))

    a = cfg.get("alerts", {})

    if demo:
        from demo import DemoEbay
        ebay = DemoEbay(store, scale="--demo-scale" in sys.argv)
    else:
        cid, sec = os.environ.get("EBAY_CLIENT_ID"), os.environ.get("EBAY_CLIENT_SECRET")
        if not cid or not sec:
            sys.exit("Set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET (see README).")
        ebay = Ebay(cid, sec, store, budget=10 ** 9)
        # Shortlist auction reminders run on every wake-up (twice an hour)
        alerts.remind(a, ebay.item)

    now = datetime.now(timezone.utc)
    # Which schedule fired, not the clock: GitHub often starts scheduled runs late, and judging by
    # the minute made late full scans skip themselves
    # The :47 run is reminders only, unless no full scan has finished in the last 45 minutes
    # (GitHub sometimes drops or delays scheduled runs)
    last_full = store.get("last_full_scan")
    recent = last_full and (now - datetime.fromisoformat(last_full)).total_seconds() < 45 * 60
    reminder_only = (os.environ.get("GITHUB_EVENT_NAME") == "schedule"
                     and os.environ.get("SCHEDULE", "").startswith("47 ") and bool(recent))
    if reminder_only:
        print("Reminder check only; the full scan runs at the top of the hour.")
        set_output(ran="false")
        return

    if demo and reminder_only:
        set_output(ran="false")
        return
    recheck_players(store, cfg)
    # When the centering method improves, re-measure the readings it would change
    if store.get("centering_version", 1) < 3:
        # v3 finds the real card edge when the outline found was the border's frame line, and
        # measures each direction on its own: re-measure everything
        n = store.db.execute("UPDATE listings SET measured_at=NULL WHERE measured_at IS NOT NULL").rowcount
        store.put("centering_version", 3)
    if store.get("centering_version", 1) < 4:
        # v4 finds cards standing on a stand / mat by their straight edges: retry the unclear ones
        n = store.db.execute("UPDATE listings SET measured_at=NULL WHERE c IS NOT NULL AND "
                             "COALESCE(json_extract(c, '$.confidence'), 0) = 0").rowcount
        store.put("centering_version", 4)
    if store.get("centering_version", 1) < 5:
        # v5 finds the card outline far more reliably (inner frames, stands, slabs, cards filling
        # the photo), which also fixes the corner close-ups: re-measure everything
        n = store.db.execute("UPDATE listings SET measured_at=NULL WHERE measured_at IS NOT NULL").rowcount
        store.put("centering_version", 5)
        store.commit()
        print(f"Re-measuring {n} photos with the improved border check")
    scanner = Scanner(cfg, store, ebay)
    scanner.ensure_units()
    ebay.budget = int(os.environ.get("DEMO_BUDGET", 10 ** 9)) if demo else max(0, budget_for_run(cfg, store) - recheck_calls(cfg))
    print(f"{len(scanner.players)} players, {store.db.execute('SELECT COUNT(*) FROM units').fetchone()[0]} "
          f"player-year sweeps; {ebay.budget} eBay calls for this run ({store.calls_today()} used today)")
    if "--list" in sys.argv:
        for u in store.db.execute("SELECT * FROM units ORDER BY player, year"):
            print(f"  {u['player']} {u['year']}  ${u['lo']}-{u['hi']}  total={u['total']}")
        return
    try:
        scanner.new_listings()
        scanner.sweep()
    except OutOfBudget:
        pass
    store.commit()
    scanner.end_auctions()
    print(f"Used {ebay.used} eBay calls. {dict(scanner.stats)}")

    minutes = float(os.environ.get("MEASURE_MINUTES") or cfg.get("photos", {}).get("measure_minutes", 25))
    measure_new(store, minutes, demo, cfg.get("photos", {}).get("read_serials", True))
    # Right before publishing: drop sold / ended listings and fill in Buy It Now quantities
    ebay.budget = ebay.used + (10 ** 9 if demo else max(0, min(recheck_calls(cfg), budget_for_day(cfg) - store.calls_today())))
    refresh_listings(store, ebay, cfg, 50 if demo else ebay.budget - ebay.used)
    shown = publish(store, cfg, scanner, scanner.stats)
    store.put("last_full_scan", now_iso())
    store.commit()
    alerts.run(store, shown, a, demo=demo)
    store.commit()
    set_output(ran="true")


if __name__ == "__main__":
    main()
