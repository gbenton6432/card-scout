"""
Shows what the scanner does with one player's eBay results: which titles match a set, which are
skipped (and why), so missing cards can be tracked down.

  python tools/probe.py "Trinity Rodman" [pages]
"""
import os, sys
from collections import Counter

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import yaml  # noqa: E402
import scout  # noqa: E402
from ebay import Ebay, PAGE  # noqa: E402
from tools.check_item import _NoStore  # noqa: E402


def main():
    name, pages = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 5
    cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml")))
    m = scout.Matcher(cfg)
    p = next(x for x in scout.players_from(cfg) if x["name"].lower() == name.lower())
    d, cons = cfg["defaults"], cfg.get("consigners", {})
    ebay = Ebay(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"], _NoStore(), budget=50)
    lo, hi = p.get("min_price", d.get("min_price")), p.get("max_price", d.get("max_price"))
    sets, skipped, cons_n, total = Counter(), [], 0, 0
    for page in range(pages):
        items, n = ebay.search(f"{p['search']} {scout.neg_words(d)}", p["category"], d, cons, lo, hi, offset=page * PAGE)
        total = n
        for it in items:
            t = it.get("title", "")
            if scout.consigner_reason(it, cons):
                cons_n += 1
                continue
            r = m.match(t, p)
            if r:
                sets[f"{r[0]} {r[1]['set']}"] += 1
            else:
                skipped.append(t)
        if len(items) < PAGE:
            break
    print(f"{p['name']}: {total} results on eBay; matched {sum(sets.values())}, skipped {len(skipped)}, consigner {cons_n}")
    print("\nMATCHED SETS:")
    for s, n in sets.most_common():
        print(f"  {n:4d}  {s}")
    print("\nSKIPPED TITLES:")
    for t in skipped:
        print("  ", t)


if __name__ == "__main__":
    main()
