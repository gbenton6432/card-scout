"""SQLite store: every listing seen, sweep progress, and eBay calls used per day."""
import json, os, sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
  id TEXT PRIMARY KEY, player TEXT, sport TEXT, year TEXT, set_name TEXT, gem REAL,
  title TEXT, url TEXT, img TEXT, n_img INTEGER, price REAL, ship REAL, auction INTEGER,
  ends TEXT, listed TEXT, seller TEXT, feedback INTEGER, card TEXT, parallel TEXT, offers INTEGER, fb_pct REAL, bin REAL,
  first_seen TEXT, last_seen TEXT, active INTEGER DEFAULT 1, c TEXT, measured_at TEXT, alerted INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_player ON listings(player, active);
CREATE INDEX IF NOT EXISTS ix_measure ON listings(active, measured_at);
CREATE TABLE IF NOT EXISTS units (
  key TEXT PRIMARY KEY, player TEXT, year TEXT, lo REAL, hi REAL, total INTEGER,
  next_offset INTEGER DEFAULT 0, started TEXT, finished TEXT);
CREATE TABLE IF NOT EXISTS calls (day TEXT PRIMARY KEY, n INTEGER);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(listings)")}
        for col, typ in (("offers", "INTEGER"), ("fb_pct", "REAL"), ("bin", "REAL")):  # columns added after launch
            if col not in cols:
                self.db.execute(f"ALTER TABLE listings ADD COLUMN {col} {typ}")

    # ── eBay call budget ──
    def today(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def calls_today(self):
        r = self.db.execute("SELECT n FROM calls WHERE day=?", (self.today(),)).fetchone()
        return r["n"] if r else 0

    def count_call(self):
        self.db.execute("INSERT INTO calls(day,n) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET n=n+1", (self.today(),))

    # ── key/value ──
    def get(self, k, default=None):
        r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def put(self, k, v):
        self.db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))

    # ── listings ──
    def known(self, iid):
        return self.db.execute("SELECT 1 FROM listings WHERE id=?", (iid,)).fetchone() is not None

    def upsert(self, l):
        """Insert a new listing or refresh price/end/last_seen on a known one. Returns True if new."""
        t = now_iso()
        cur = self.db.execute(
            "UPDATE listings SET price=?, bin=?, ship=?, ends=?, offers=?, feedback=?, fb_pct=?, last_seen=?, active=1 WHERE id=?",
            (l["price"], l["bin"], l["ship"], l["ends"], l["offers"], l["feedback"], l["fb_pct"], t, l["id"]))
        if cur.rowcount:
            return False
        cols = ["id", "player", "sport", "year", "set_name", "gem", "title", "url", "img", "n_img", "price",
                "ship", "auction", "ends", "listed", "seller", "feedback", "card", "parallel", "offers", "fb_pct", "bin"]
        self.db.execute(
            f"INSERT INTO listings({','.join(cols)},first_seen,last_seen,active) VALUES({','.join('?' * len(cols))},?,?,1)",
            [l[c] for c in cols] + [t, t])
        return True

    def commit(self):
        self.db.commit()
