"""Reads card number and parallel from a listing title, to group listings of the same card."""
import re

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


def card_key(title, player, set_name, tcg=False):
    """(card number, parallel) parsed from a title. Pokémon-style '223/197' is a card number;
    sports-style '/99' is a numbered parallel."""
    t = _norm(title)
    if tcg:
        m = re.search(r"\b(\d{1,3})\s?/\s?\d{2,3}\b", title) or re.search(r"#\s*(\d{1,3})", title)
        t = re.sub(r"\d{1,3}\s?/\s?\d{2,3}", " ", t)
        found = [p.replace(" ", "") for p in _PAR if f" {p} " in t and p not in _norm(set_name)]
        return (str(int(m.group(1))) if m else ""), "+".join(sorted(set(found))) or "base"
    for w in _norm(set_name).split() + _norm(player).split():
        t = t.replace(f" {w} ", " ")  # don't read "Prizm" or a player's name as a parallel
    num = re.search(r"#\s*([a-z]{0,5}\s?-?\s?\d{1,4}[a-z]?)", title.lower())
    number = re.sub(r"[\s-]", "", num.group(1)).upper() if num else ""
    serial = re.search(r"/\s?(\d{1,4})\b", t)
    found, rest = [], t
    for p in _PAR:
        if f" {p} " in rest:
            found.append(p.replace(" ", ""))
            rest = rest.replace(f" {p} ", " ")
    if serial:
        found.append(f"/{serial.group(1)}")
    return number, "+".join(sorted(found)) or "base"
