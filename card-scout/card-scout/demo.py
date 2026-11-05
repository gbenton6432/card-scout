"""Fake eBay results with generated card photos, so the dashboard can be previewed without keys."""
import os
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
rng = np.random.default_rng(7)


def _photo(l, r, t, b, angle, persp, path, border=(235, 238, 240), bg=(70, 95, 120)):
    cw, ch = 500, 700
    card = np.full((ch, cw, 3), border, np.uint8)
    art = cv2.GaussianBlur((rng.random((ch - t - b, cw - l - r, 3)) * 140 + 50).astype(np.uint8), (21, 21), 0)
    cv2.circle(art, (art.shape[1] // 2, art.shape[0] // 2), 120, (40, 60, 200), -1)
    card[t:ch - b, l:cw - r] = art
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


def demo_items():
    out = os.path.join(HERE, "site", "demo"); os.makedirs(out, exist_ok=True)
    specs = [  # borders l,r,t,b, angle, persp, price, seller, feedback, title
        ((30, 30, 31, 29), 1, 0, 89, "cardguy_tx", 412, "2018 Prizm Luka Doncic RC #280 Base Rookie"),
        ((33, 27, 30, 30), -3, 15, 74, "dad_collects", 96, "Luka Doncic 2019-20 Panini Prizm Base"),
        ((38, 22, 30, 30), 2, 0, 55, "hoops_attic", 1220, "2018 Prizm Luka Doncic #280 rookie"),
        ((30, 30, 40, 20), 0, 0, 61, "midwest_breaks", 3801, "Luka Doncic 2019 Prizm Base"),
        ((29, 31, 32, 28), 6, 20, 120, "sharpcorners22", 58, "2018-19 Prizm Luka Doncic RC Sharp!"),
        ((31, 29, 29, 31), -2, 0, 99, "bigvaultco", 184000, "Luka Doncic 2018 Prizm RC"),
        ((30, 30, 30, 30), 0, 0, 105, "someseller", 900, "Luka Doncic Prizm RC - CONSIGNMENT"),
        ((32, 28, 34, 26), 4, 10, 210, "pokepal", 640, "Pokemon 151 Charizard ex 199/165 SIR"),
        ((35, 25, 30, 30), -5, 0, 165, "binder_finds", 211, "Charizard ex Obsidian Flames 223/197 Special Illustration"),
        ((30, 30, 30, 30), 0, 0, 240, "estate_cards_oh", 77, "Pokemon 151 Charizard ex #006 Double Rare NM"),
        ((26, 34, 36, 24), 3, 0, 145, "tcg_kid_mo", 33, "Obsidian Flames Charizard ex 125/197 Holo"),
    ]
    items = []
    for i, (bd, ang, p, price, seller, fb, title) in enumerate(specs):
        name = f"card{i}.jpg"
        _photo(*bd, ang, p, os.path.join(out, name))
        imgs = [] if i == 3 else [{"imageUrl": f"demo/{name}"}]
        items.append(({
            "itemId": f"v1|demo{i}|0", "title": title,
            "itemWebUrl": "https://www.ebay.com/sch/i.html?_nkw=" + title.replace(" ", "+"),
            "image": {"imageUrl": f"demo/{name}"}, "additionalImages": imgs,
            "price": {"value": str(price), "currency": "USD"},
            "shippingOptions": [{"shippingCost": {"value": "4.99" if i % 3 else "0.00"}}],
            "buyingOptions": ["AUCTION"] if i in (2, 8) else ["FIXED_PRICE"],
            "itemEndDate": "2026-11-09T02:00:00.000Z" if i in (2, 8) else None,
            "seller": {"username": seller, "feedbackScore": fb, "feedbackPercentage": "99.8"},
            "condition": "Ungraded",
        }, ({"player": "Charizard", "year": "", "category": "tcg", "sets": [
                {"year": 2023, "set": "Pokemon 151", "gem_rate": 55},
                {"year": 2023, "set": "Obsidian Flames", "gem_rate": 48}]} if i >= 7
            else {"player": "Luka Doncic", "year": "2018" if "2018" in title else "2019",
                  "sets": [{"year": 2018 if "2018" in title else 2019, "set": "Prizm",
                            "gem_rate": 41 if "2018" in title else 44, "exclude": ["draft picks"]}]})))
    return items


def demo_comps():
    """Fake PSA 10 asking prices for the demo cards."""
    import profit
    c = {"groups": {"demo": {"at": "2099-01-01T00:00:00+00:00", "prices": {}}}}
    rows = [("luka doncic", "2018", "prizm", "280", "base", [395, 420, 450, 480, 520]),
            ("charizard", "2023", "pokemon 151", "6", "base", [140, 155, 160, 175]),
            ("charizard", "2023", "obsidian flames", "223", "base", [610, 640, 700]),
            ("charizard", "2023", "obsidian flames", "125", "base", [95, 110])]
    for *k, prices in rows:
        c["groups"]["demo"]["prices"]["|".join(k)] = prices
    return c
