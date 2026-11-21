"""
Market value from sales the scanner saw happen.

Sales are recorded when a tracked auction ends with bids (its final bid) or a tracked Buy It Now
listing sells out. Each price includes shipping, like the asking prices it is compared with.

A listing's value comes from the most specific level that has data:
  A  the same card (player, year, set, card number, parallel)          "3 sales"
  C  the same card's base version x the set's usual premium for this   "est. base x parallel"
     parallel (learned from every player's sales in the set)
Different card numbers are never mixed: in one set they can be base cards, inserts, relics or
autographs worth very different amounts.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

DAYS = 120  # sales older than this are ignored

SCHEMA = """
CREATE TABLE IF NOT EXISTS sales (
  id TEXT PRIMARY KEY, player TEXT, year TEXT, set_name TEXT, card TEXT, parallel TEXT,
  price REAL, kind TEXT, sold_at TEXT, title TEXT);
CREATE INDEX IF NOT EXISTS ix_sales ON sales(player, year, set_name);
"""


def setup(store):
    store.db.executescript(SCHEMA)


def record(store, row, price, kind, when=None):
    """Save one sale. row: a listings row (dict or sqlite Row); price: total incl. shipping."""
    if not price or price <= 0:
        return
    store.db.execute(
        "INSERT OR REPLACE INTO sales(id, player, year, set_name, card, parallel, price, kind, sold_at, title) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (row["id"], row["player"], row["year"], row["set_name"], row["card"], row["parallel"] or "",
         round(float(price), 2), kind, when or datetime.now(timezone.utc).isoformat(timespec="seconds"), row["title"]))


def _is_base(parallel):
    return (parallel or "base") == "base"


class Valuer:
    def __init__(self, store):
        since = (datetime.now(timezone.utc) - timedelta(days=DAYS)).isoformat()
        # Buy It Now sales that allowed offers may have sold for less than the list price: not used
        rows = store.db.execute("SELECT * FROM sales WHERE sold_at >= ? AND kind != 'bin_offer'", (since,)).fetchall()
        self.n_sales = len(rows)
        self.card = defaultdict(list)       # A
        self.base = defaultdict(list)       # base version of one card
        self.set_par = defaultdict(list)    # every player's sales of a parallel in a set
        self.set_base = defaultdict(list)   # every player's base sales in a set
        for s in rows:
            par = s["parallel"] or "base"
            if s["card"]:
                self.card[(s["player"], s["year"], s["set_name"], s["card"], par)].append(s["price"])
            if _is_base(par):
                if s["card"]:
                    self.base[(s["player"], s["year"], s["set_name"], s["card"])].append(s["price"])
                self.set_base[(s["year"], s["set_name"])].append(s["price"])
            else:
                self.set_par[(s["year"], s["set_name"], par)].append(s["price"])

    def value(self, r):
        """(value, number of sales behind it, level 'A'/'B'/'C', label) or None."""
        par = r["parallel"] or "base"
        if r["card"]:
            v = self.card.get((r["player"], r["year"], r["set_name"], r["card"], par))
            if v:
                return round(median(v), 2), len(v), "A", f"{len(v)} sale{'s' if len(v) > 1 else ''}"
        if not _is_base(par) and r["card"]:
            b = self.base.get((r["player"], r["year"], r["set_name"], r["card"]))
            sp, sb = self.set_par.get((r["year"], r["set_name"], par)), self.set_base.get((r["year"], r["set_name"]))
            if b and len(b) >= 2 and sp and len(sp) >= 3 and sb and len(sb) >= 3:
                mult = median(sp) / median(sb)
                n = len(b) + len(sp)
                return round(median(b) * mult, 2), n, "C", f"est. base #{r['card']} × {mult:.1f} for this parallel"
        return None
