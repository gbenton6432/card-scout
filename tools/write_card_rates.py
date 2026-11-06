"""
Saves card-level gem rates collected by tools/gemrate_cards.js as card_rates.json.

Input: either a JSON file of rows, or one or more text files that together hold the
gzip+base64 export (the browser bridge limits how much text comes back in one piece).
Usage: python tools/write_card_rates.py rows.json
       python tools/write_card_rates.py part1.txt part2.txt part3.txt
"""
import base64, gzip, json, os, sys
from datetime import date

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(paths):
    if len(paths) == 1 and paths[0].endswith(".json"):
        rows = json.load(open(paths[0]))
    else:
        b64 = "".join(open(p).read().strip().strip('"') for p in paths)
        rows = json.loads(gzip.decompress(base64.b64decode(b64)))
    json.dump({"updated": date.today().isoformat(), "source": "gemrate.com player pages",
               "fields": ["player", "year", "set", "card_number", "parallel", "graded", "psa10", "psa9"],
               "cards": rows}, open(os.path.join(HERE, "card_rates.json"), "w"), separators=(",", ":"))
    print(f"Wrote {len(rows)} cards for {len({r[0] for r in rows})} players")


if __name__ == "__main__":
    main(sys.argv[1:])
