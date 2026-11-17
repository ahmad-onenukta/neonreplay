import sqlite3, os, time
from .common import DATA

PATH = os.path.join(DATA, "neonreplay.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created REAL, channel TEXT, campaign TEXT, cpm_usd REAL,
  trigger TEXT, score REAL, transcript TEXT,
  raw_path TEXT, status TEXT DEFAULT 'new', error TEXT
);
CREATE TABLE IF NOT EXISTS variants(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  clip_id INTEGER, lang TEXT, hook TEXT, caption TEXT,
  video_path TEXT, status TEXT DEFAULT 'ready',
  scheduled_at REAL, posted_at REAL, post_ref TEXT, post_url TEXT,
  views INTEGER DEFAULT 0, earnings_usd REAL DEFAULT 0, error TEXT
);
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT, handle TEXT, lang TEXT,
  followers INTEGER DEFAULT 0, views_30d INTEGER DEFAULT 0, views_90d INTEGER DEFAULT 0,
  updated REAL
);
CREATE TABLE IF NOT EXISTS payouts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT, source TEXT, amount_eur REAL, note TEXT
);
"""

DEFAULT_ACCOUNTS = [
    ("tiktok", "@neonreplay.clips", "en"), ("tiktok", "@neonreplay.de", "de"), ("tiktok", "@neonreplay.urdu", "ur"),
    ("instagram", "@neonreplay.clips", "en"), ("instagram", "@neonreplay.de", "de"), ("instagram", "@neonreplay.urdu", "ur"),
    ("youtube", "@NeonReplayClips", "en"), ("youtube", "@NeonReplayDE", "de"), ("youtube", "@NeonReplayUrdu", "ur"),
]


def conn():
    c = sqlite3.connect(PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init():
    c = conn()
    c.executescript(SCHEMA)
    if not c.execute("SELECT 1 FROM accounts LIMIT 1").fetchone():
        c.executemany("INSERT INTO accounts(platform,handle,lang,updated) VALUES(?,?,?,?)",
                      [(*a, time.time()) for a in DEFAULT_ACCOUNTS])
    c.commit()
    return c
