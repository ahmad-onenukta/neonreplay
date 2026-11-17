"""Posting über die Metricool-API mit Rate-Limits pro Sprache/Brand."""
import os, time, logging, datetime
import requests
from . import db
from .common import cfg

log = logging.getLogger("poster")
API = "https://app.metricool.com/api"


def _params(blog_id):
    return {"userId": os.environ.get("METRICOOL_USER_ID", ""), "blogId": blog_id}


def _headers():
    return {"X-Mc-Auth": os.environ.get("METRICOOL_TOKEN", ""), "Content-Type": "application/json"}


def schedule(variant, brand):
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    media_url = f"{base}/media/{os.path.basename(variant['video_path'])}"
    p = _params(brand["blog_id"])
    norm = requests.get(f"{API}/actions/normalize/image/url", params={**p, "url": media_url},
                        headers=_headers(), timeout=60)
    norm.raise_for_status()
    media_ref = norm.text.strip().strip('"')
    when = datetime.datetime.now() + datetime.timedelta(minutes=3)
    title = variant["hook"][:95]
    body = {
        "publicationDate": {"dateTime": when.strftime("%Y-%m-%dT%H:%M:%S"), "timezone": "Europe/Berlin"},
        "text": f"{variant['hook']}\n\n{variant['caption']}",
        "draft": False, "autoPublish": True,
        "providers": [{"network": n} for n in brand["networks"]],
        "media": [media_ref],
        "youtubeData": {"title": title, "type": "short", "privacy": "public", "madeForKids": False},
        "tiktokData": {"privacyOption": "PUBLIC_TO_EVERYONE"},
        "instagramData": {"type": "REEL", "showReelOnFeed": True},
    }
    r = requests.post(f"{API}/v2/scheduler/posts", params=p, json=body, headers=_headers(), timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"{r.status_code}: {r.text[:400]}")
    data = r.json() if r.text else {}
    return str(data.get("data", {}).get("id") or data.get("id") or "ok")


def loop():
    c = db.conn()
    while True:
        conf = cfg()["posting"]
        auto = conf["mode"] == "auto"
        states = ("approved", "ready") if auto else ("approved",)
        now = time.time()
        for lang, brand in conf["brands"].items():
            if not brand.get("blog_id") or not os.environ.get("METRICOOL_TOKEN"):
                continue
            last = c.execute("SELECT MAX(posted_at) m FROM variants WHERE lang=? AND status='posted'", (lang,)).fetchone()["m"] or 0
            today = c.execute("SELECT COUNT(*) n FROM variants WHERE lang=? AND status='posted' AND posted_at>?",
                              (lang, now - 86400)).fetchone()["n"]
            if now - last < conf["min_minutes_between_posts"] * 60 or today >= conf["max_posts_per_account_per_day"]:
                continue
            v = c.execute(f"SELECT * FROM variants WHERE lang=? AND status IN ({','.join('?'*len(states))}) "
                          "ORDER BY status='approved' DESC, id DESC LIMIT 1", (lang, *states)).fetchone()
            if not v:
                continue
            try:
                ref = schedule(dict(v), brand)
                c.execute("UPDATE variants SET status='posted', posted_at=?, post_ref=? WHERE id=?", (now, ref, v["id"]))
                log.info("gepostet variant %s (%s)", v["id"], lang)
            except Exception as e:
                c.execute("UPDATE variants SET status='error', error=? WHERE id=?", (str(e)[:500], v["id"]))
                log.error("post fehler %s: %s", v["id"], e)
            c.commit()
        time.sleep(60)
