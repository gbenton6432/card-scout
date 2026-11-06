"""Fake eBay results with generated card photos, so the dashboard can be previewed without keys."""
import os
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
rng = np.random.default_rng(7)


SERIALS = {2: "099/199", 8: "10/25", 3: "05/50", 9: "1/1"}  # demo cards with a stamped serial


def _photo(l, r, t, b, angle, persp, path, border=(235, 238, 240), bg=(70, 95, 120), serial=None):
    cw, ch = 500, 700
    card = np.full((ch, cw, 3), border, np.uint8)
    art = cv2.GaussianBlur((rng.random((ch - t - b, cw - l - r, 3)) * 140 + 50).astype(np.uint8), (21, 21), 0)
    cv2.circle(art, (art.shape[1] // 2, art.shape[0] // 2), 120, (40, 60, 200), -1)
    card[t:ch - b, l:cw - r] = art
    if serial:
        cv2.putText(card, serial, (cw - r - 170, ch - b - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (40, 170, 215), 2, cv2.LINE_AA)
    canvas = (np.full((1100, 900, 3), bg, np.float32) + rng.normal(0, 7, (1100, 900, 3))).clip(0, 255).astype(np.uint8)
    src = np.float32([[0, 0], [cw, 0], [cw, ch], [0, ch]])
    a = np.deg2rad(angle)
    R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    dst = (src - [cw / 2, ch / 2]) @ R.T + [450, 550]
    dst[0] += [persp, persp / 2]; dst[1] += [-persp, persp / 2]
    M = cv2.getPerspectiveTransform(src, np.float32(dst))
    mask = cv2.warpPerspective(np.full((ch, cw), 255, np.uint8), M, (900, 1100))
    warped = cv2.warpPerspective(card, M, (900, 1100))
    canvas[mask > 0] = warped[mask > 0]
    cv2.imwrite(path, canvas, [cv2.IMWRITE_JPEG_QUALITY, 82])


# (borders l,r,t,b), angle, perspective, price, seller, feedback, auction, title, player, year, set, gem rate, PSA 10 asks
SPECS = [
    ((30, 30, 31, 29), 1, 0, 64, "cardguy_tx", 412, False, "2018 Topps Chrome Update Shohei Ohtani #HMT32 Rookie RC",
     "Shohei Ohtani", 2018, "Topps Chrome Update", 81.7, [215, 230, 240, 260, 275]),
    ((33, 27, 30, 30), -3, 15, 38, "dad_collects", 96, True, "2018 Topps Heritage Shohei Ohtani #600 RC",
     "Shohei Ohtani", 2018, "Topps Heritage", 61.5, [120, 135, 150]),
    ((31, 29, 32, 28), 2, 0, 48, "hoops_attic", 1220, False, "2017 Topps Chrome Update Aaron Judge #HMT40 Rookie",
     "Aaron Judge", 2017, "Topps Chrome Update", 78.5, [190, 210, 225, 240]),
    ((30, 30, 40, 20), 0, 0, 72, "midwest_breaks", 3801, False, "2017 Panini Select Patrick Mahomes II #6 Rookie",
     "Patrick Mahomes", 2017, "Panini Select", 53.4, [260, 280, 300]),
    ((29, 31, 32, 28), 5, 20, 22, "sharpcorners22", 58, False, "2018 Donruss Optic Josh Allen #154 Rated Rookie",
     "Josh Allen", 2018, "Panini Donruss Optic", 51, [95, 110, 120, 140]),
    ((32, 28, 29, 31), -2, 0, 17, "binder_finds", 211, True, "2020-21 Panini Mosaic Stephen Curry #175",
     "Stephen Curry", 2020, "Panini Mosaic", 60.2, [55, 60, 70]),
    ((30, 30, 30, 30), 0, 0, 85, "estate_cards_oh", 77, False, "2012-13 Panini Past & Present LeBron James #6",
     "LeBron James", 2012, "Panini Past & Present", 71.8, []),
    ((28, 32, 33, 27), 3, 0, 29, "tcg_kid_mo", 33, False, "2024 Topps Now Caleb Williams #D1 Draft Night",
     "Caleb Williams", 2024, "Topps Now", 84.6, [60, 65, 72]),
    ((34, 26, 30, 30), -4, 10, 140, "futbol_finds", 640, True, "2017-18 Topps Chrome UEFA Champions League Kylian Mbappe #41 RC",
     "Kylian Mbappe", 2017, "Topps Chrome UEFA Champions League", 60, [520, 560, 600]),
    ((30, 30, 31, 29), 1, 0, 26, "nwsl_cards", 145, False, "2023 Topps Finest MLS Trinity Rodman #75",
     "Trinity Rodman", 2023, "Topps Finest MLS", 71.8, []),
    ((31, 29, 30, 30), 0, 0, 34, "philly_pulls", 312, False, "2024 Topps Now MLS Cavan Sullivan #107 Debut",
     "Cavan Sullivan", 2024, "Topps Now MLS", 68.7, [110, 125]),
    ((30, 30, 30, 30), 0, 0, 55, "someseller", 900, False, "2018 Topps Chrome Update Ohtani HMT1 - CONSIGNMENT",
     "Shohei Ohtani", 2018, "Topps Chrome Update", 81.7, []),
]


class DemoEbay:
    """Stands in for eBay. Small mode: the cards in SPECS. Scale mode (--demo-scale): tens of
    thousands of made-up listings, including one player-year over eBay's 10,000-result cap."""

    def __init__(self, store, scale=False):
        import random
        from datetime import datetime, timezone, timedelta
        from ebay import PAGE
        self.store, self.used, self.budget, self.is_demo = store, 0, 10 ** 9, True
        out = os.path.join(HERE, "site", "demo"); os.makedirs(out, exist_ok=True)
        now = datetime.now(timezone.utc)
        self.items = []
        for i, (bd, ang, p, price, seller, fb, auction, title, player, year, set_name, gem, _) in enumerate(SPECS):
            name = f"card{i}.jpg"
            _photo(*bd, ang, p, os.path.join(out, name), serial=SERIALS.get(i))
            self.items.append(self._item(f"v1|demo{i}|0", title, price, seller, fb, auction, f"demo/{name}",
                                         now - timedelta(hours=i), now + timedelta(hours=3 + 20 * i), i))
        if scale:
            rnd = random.Random(1)
            import yaml
            sets = yaml.safe_load(open(os.path.join(HERE, "gem_rates.yaml")))["sets"]
            cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml")))
            for pl in cfg["players"]:
                lo = int(str(pl.get("years", "2010-")).split("-")[0])
                for y in range(max(lo, 2010), 2026):
                    ys = [x for x in sets if x["sport"] == pl["sport"] and x["year"] == y and x["graded"] > 2000]
                    if not ys:
                        continue
                    n = 15000 if (pl["name"], y) == ("Shohei Ohtani", 2018) else rnd.randint(50, 900)
                    for k in range(n):
                        st = rnd.choice(ys)
                        self.items.append(self._item(
                            f"v1|s{pl['name'][:3]}{y}{k}|0", f"{y} {st['set']} {pl['name']} #{rnd.randint(1, 350)}",
                            round(rnd.uniform(10, 1000), 2), f"seller{rnd.randint(1, 4000)}", rnd.randint(1, 30000),
                            rnd.random() < 0.3, f"demo/card{rnd.randint(0, len(SPECS) - 1)}.jpg",
                            now - timedelta(minutes=rnd.randint(1, 60 * 24 * 90)),
                            now + timedelta(hours=rnd.randint(1, 240)), k))
        self.items.sort(key=lambda it: it["itemCreationDate"], reverse=True)
        self.PAGE = PAGE

    @staticmethod
    def _item(iid, title, price, seller, fb, auction, img, listed, ends, i):
        return {"itemId": iid, "title": title,
                "itemWebUrl": "https://www.ebay.com/sch/i.html?_nkw=" + title.replace(" ", "+").replace("#", ""),
                "image": {"imageUrl": img}, "additionalImages": [] if i % 9 == 7 else [{"imageUrl": img}],
                "price": {"value": str(price), "currency": "USD"},
                "shippingOptions": [{"shippingCost": {"value": "4.99" if i % 3 else "0.00"}}],
                "buyingOptions": ["AUCTION"] if auction else ["FIXED_PRICE"],
                "itemEndDate": ends.isoformat() if auction else None, "itemCreationDate": listed.isoformat(),
                "seller": {"username": seller, "feedbackScore": fb}, "condition": "Ungraded"}

    def search(self, q, category, d, cons, lo=None, hi=None, offset=0, newest=True):
        from ebay import OutOfBudget, MAX_RESULTS
        if self.used >= self.budget:
            raise OutOfBudget()
        self.used += 1
        self.store.count_call()
        words = [w for w in q.split() if not w.startswith("-") and not w.startswith('"')]
        year = words[0] if words and words[0].isdigit() else None
        name = " ".join(words[1:] if year else words).lower()
        hits = [it for it in self.items
                if name in it["title"].lower() and (not year or year in it["title"])
                and (lo is None or float(it["price"]["value"]) >= lo)
                and (hi is None or float(it["price"]["value"]) <= hi)]
        return hits[offset:offset + self.PAGE] if offset < MAX_RESULTS else [], len(hits)

    def item(self, iid):
        return None
