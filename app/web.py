"""Dashboard: Clips, Status, Views, Einnahmen, Account-Fortschritt."""
import os, time, secrets, datetime
from fastapi import FastAPI, Depends, HTTPException, Form, Request
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from . import db
from .common import CLIPS, cfg, ROOT, settings, save_settings, setting
import hashlib

app = FastAPI(title="NeonReplay")
security = HTTPBasic()
tpl = Jinja2Templates(directory=os.path.join(ROOT, "app", "templates"))
FEE = 0.9  # ~10 % Plattformgebühr


def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()


class NeedSetup(Exception):
    pass


def auth(cred: HTTPBasicCredentials = Depends(HTTPBasic(auto_error=False))):
    s = settings()
    if not s.get("pw_hash") and not os.environ.get("DASHBOARD_PASSWORD"):
        raise NeedSetup()
    if cred is None:
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    ok_u = secrets.compare_digest(cred.username, s.get("user") or os.environ.get("DASHBOARD_USER", "noman"))
    if s.get("pw_hash"):
        ok_p = secrets.compare_digest(_hash(cred.password, s["pw_salt"]), s["pw_hash"])
    else:
        ok_p = secrets.compare_digest(cred.password, os.environ.get("DASHBOARD_PASSWORD", ""))
    if not (ok_u and ok_p):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    return cred.username


@app.exception_handler(NeedSetup)
def _need_setup(request, exc):
    return RedirectResponse("/setup", 303)


SECRET_KEYS = ["ANTHROPIC_API_KEY", "METRICOOL_TOKEN"]
PLAIN_KEYS = ["METRICOOL_USER_ID", "BLOG_ID_EN", "BLOG_ID_DE", "BLOG_ID_UR", "PUBLIC_BASE_URL", "POSTING_MODE"]


@app.get("/setup")
def setup_page(request: Request):
    if settings().get("pw_hash"):
        return RedirectResponse("/", 303)
    return tpl.TemplateResponse(request, "setup.html", {})


@app.post("/setup")
def setup_save(user: str = Form(...), password: str = Form(...)):
    if settings().get("pw_hash"):
        raise HTTPException(403)
    if len(password) < 10:
        raise HTTPException(400, "Passwort mindestens 10 Zeichen")
    salt = secrets.token_hex(8)
    save_settings({"user": user.strip(), "pw_salt": salt, "pw_hash": _hash(password, salt)})
    return RedirectResponse("/settings", 303)


@app.get("/settings")
def settings_page(request: Request, user=Depends(auth)):
    s = settings()
    conf = cfg()
    on = s.get("ENABLED_CHANNELS")
    chans = [dict(c, active=(c["name"] in on) if on is not None else bool(c.get("enabled"))) for c in conf["channels"]]
    return tpl.TemplateResponse(request, "settings.html", {
        "secrets": {k: bool(setting(k)) for k in SECRET_KEYS},
        "plain": {k: setting(k) for k in PLAIN_KEYS}, "channels": chans, "api_token": s.get("API_TOKEN", ""),
        "default_mode": conf["posting"]["mode"]})


@app.post("/settings")
async def settings_save(request: Request, user=Depends(auth)):
    form = await request.form()
    upd = {}
    for k in SECRET_KEYS:
        v = (form.get(k) or "").strip()
        if v:
            upd[k] = v
    for k in PLAIN_KEYS:
        if k in form:
            upd[k] = (form.get(k) or "").strip()
    upd["ENABLED_CHANNELS"] = form.getlist("channels")
    save_settings(upd)
    return RedirectResponse("/settings?ok=1", 303)


@app.on_event("startup")
def _init():
    db.init()
    if not settings().get("API_TOKEN"):
        save_settings({"API_TOKEN": secrets.token_urlsafe(24)})


# ---------- Posting-API für den Claude-Posting-Agenten (Metricool über MCP) ----------
def _check_token(token):
    if not token or not secrets.compare_digest(token, settings().get("API_TOKEN", "")):
        raise HTTPException(403)


@app.get("/api/queue")
def api_queue(token: str = "", limit: int = 3):
    """Freigegebene, noch nicht gepostete Clips (pro Sprache max. `limit`), inkl. Rate-Limit-Info."""
    _check_token(token)
    c = db.conn()
    conf = cfg()
    pconf = conf["posting"]
    auto = (setting("POSTING_MODE") or pconf["mode"]) == "auto"
    states = ("approved", "ready") if auto else ("approved",)
    base = setting("PUBLIC_BASE_URL").rstrip("/")
    now = time.time()
    chans = {ch["name"]: ch for ch in conf["channels"]}
    out = []
    for lang in conf["languages"]:
        blog = setting(f"BLOG_ID_{lang.upper()}")
        if not blog:
            continue
        posted_today = c.execute("SELECT COUNT(*) FROM variants WHERE lang=? AND status IN ('posted','posting') AND posted_at>?",
                                 (lang, now - 86400)).fetchone()[0]
        last = c.execute("SELECT MAX(posted_at) FROM variants WHERE lang=? AND status IN ('posted','posting')", (lang,)).fetchone()[0] or 0
        free = max(0, pconf["max_posts_per_account_per_day"] - posted_today)
        rows = c.execute(f"SELECT v.*, c.channel FROM variants v JOIN clips c ON c.id=v.clip_id WHERE v.lang=? AND v.status IN "
                         f"({','.join('?'*len(states))}) ORDER BY v.status='approved' DESC, v.id DESC LIMIT ?",
                         (lang, *states, min(limit, free))).fetchall()
        for i, r in enumerate(rows):
            ch = chans.get(r["channel"], {})
            out.append({"id": r["id"], "lang": lang, "blogId": blog, "hook": r["hook"], "caption": r["caption"],
                        "text": f"{r['hook']}\n\n{r['caption']}", "media_url": f"{base}/media/{os.path.basename(r['video_path'])}",
                        "streamer": ch.get("display", r["channel"]), "networks": pconf["brands"].get(lang, {}).get("networks", []),
                        "earliest_minutes_from_now": max(0, int((last + pconf["min_minutes_between_posts"] * 60 * (i + 1) - now) / 60))})
    return {"count": len(out), "items": out}


@app.get("/api/mark")
def api_mark(token: str = "", id: int = 0, status: str = "posted", ref: str = "", url: str = "", error: str = ""):
    _check_token(token)
    if status not in ("posted", "error", "approved"):
        raise HTTPException(400)
    c = db.conn()
    c.execute("UPDATE variants SET status=?, posted_at=CASE WHEN ?='posted' THEN ? ELSE posted_at END, "
              "post_ref=COALESCE(NULLIF(?,''),post_ref), post_url=COALESCE(NULLIF(?,''),post_url), error=NULLIF(?,'') WHERE id=?",
              (status, status, time.time(), ref, url, error, id))
    c.commit()
    return {"ok": True, "id": id, "status": status}


@app.get("/api/vod")
def api_vod(token: str = "", channel: str = "", url: str = "", n: int = 5, gta_only: int = 1):
    """VOD-Auftrag anlegen: alte Streams per Chat-Replay clippen."""
    _check_token(token)
    from . import vod
    if channel not in {c["name"] for c in cfg()["channels"]}:
        raise HTTPException(400, "unbekannter Kanal")
    p = vod.request(channel, url, max(1, min(n, 15)), bool(gta_only))
    return {"ok": True, "job": os.path.basename(p)}


@app.get("/api/status")
def api_status(token: str = "", limit: int = 20):
    """Letzte Clips + Varianten + VOD-Aufträge (für den Claude-Agenten)."""
    _check_token(token)
    import glob
    from . import vod
    c = db.conn()
    clips = [dict(r) for r in c.execute("SELECT id,created,channel,trigger,score,status,error,substr(transcript,1,200) t "
                                        "FROM clips ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    base = setting("PUBLIC_BASE_URL").rstrip("/")
    vars_ = [dict(r) | {"media_url": f"{base}/media/{os.path.basename(r['video_path'])}"} for r in
             c.execute("SELECT id,clip_id,lang,hook,caption,status,video_path FROM variants ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    jobs = []
    for p in sorted(glob.glob(os.path.join(vod.QDIR, "*")))[-10:]:
        e = ""
        if p.endswith(".error"):
            e = open(p).read()[-900:]
        jobs.append({"file": os.path.basename(p), "error": e})
    return {"clips": clips, "variants": vars_, "vod_jobs": jobs}


@app.get("/media/{name}")
def media(name: str):
    path = os.path.join(CLIPS, os.path.basename(name))
    if name.startswith("sheet_") and name.endswith(".jpg") and os.path.exists(path):
        return FileResponse(path, media_type="image/jpeg")
    if not name.endswith(".mp4") or "_raw" in name or not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/job")
def api_job(token: str = "", body: str = ""):
    """Eigene Reels/Kontaktabzüge (JSON im Parameter body, siehe app/reels.py)."""
    _check_token(token)
    import json as _j
    from . import vod
    body = _j.loads(body)
    if body.get("type") not in ("frames", "reel"):
        raise HTTPException(400)
    p = os.path.join(vod.QDIR, f"{int(time.time())}_{secrets.token_hex(3)}.json")
    with open(p, "w") as f:
        _j.dump(body, f)
    return {"ok": True, "job": os.path.basename(p)}


@app.get("/api/frames")
def api_frames(token: str = "", url: str = "", name: str = "", every: int = 5):
    _check_token(token)
    import json as _j
    from . import vod
    p = os.path.join(vod.QDIR, f"{int(time.time())}_{secrets.token_hex(3)}.json")
    with open(p, "w") as f:
        _j.dump({"type": "frames", "url": url, "name": name, "every": every}, f)
    return {"ok": True, "sheet": f"sheet_{name}.jpg"}


def progress(a):
    if a["platform"] == "tiktok":
        return [("Follower", a["followers"], 10_000), ("Views 30T", a["views_30d"], 100_000)]
    if a["platform"] == "youtube":
        return [("Abos", a["followers"], 1_000), ("Shorts-Views 90T", a["views_90d"], 10_000_000)]
    return [("Follower", a["followers"], 10_000)]


@app.get("/")
def home(request: Request, status: str = "", user=Depends(auth)):
    c = db.conn()
    day = time.time() - 86400
    q = ("SELECT v.*, c.channel, c.campaign, c.cpm_usd, c.trigger, c.score FROM variants v JOIN clips c ON c.id=v.clip_id "
         + ("WHERE v.status=? " if status else "") + "ORDER BY v.id DESC LIMIT 150")
    rows = c.execute(q, (status,) if status else ()).fetchall()
    tot = c.execute("SELECT COALESCE(SUM(v.views),0) views, COALESCE(SUM(v.views/1000.0*c.cpm_usd),0) est, "
                    "COALESCE(SUM(v.earnings_usd),0) earned FROM variants v JOIN clips c ON c.id=v.clip_id").fetchone()
    kpi = {
        "clips_today": c.execute("SELECT COUNT(*) FROM clips WHERE created>?", (day,)).fetchone()[0],
        "ready": c.execute("SELECT COUNT(*) FROM variants WHERE status IN ('ready','approved')").fetchone()[0],
        "posted_today": c.execute("SELECT COUNT(*) FROM variants WHERE status='posted' AND posted_at>?", (day,)).fetchone()[0],
        "posted_total": c.execute("SELECT COUNT(*) FROM variants WHERE status='posted'").fetchone()[0],
        "views": tot["views"], "est_usd": tot["est"] * FEE, "earned_usd": tot["earned"],
        "payouts_eur": c.execute("SELECT COALESCE(SUM(amount_eur),0) FROM payouts").fetchone()[0],
        "errors": c.execute("SELECT COUNT(*) FROM clips WHERE status='error' AND created>?", (day,)).fetchone()[0],
    }
    by_lang = c.execute("SELECT lang, COUNT(*) n, COALESCE(SUM(views),0) views FROM variants WHERE status='posted' GROUP BY lang").fetchall()
    by_channel = c.execute("SELECT c.channel, c.campaign, COUNT(v.id) n, COALESCE(SUM(v.views),0) views, "
                           "COALESCE(SUM(v.views/1000.0*c.cpm_usd),0)*? est FROM clips c LEFT JOIN variants v ON v.clip_id=c.id "
                           "AND v.status='posted' GROUP BY c.channel ORDER BY est DESC", (FEE,)).fetchall()
    top = c.execute("SELECT hook, lang, views FROM variants WHERE views>0 ORDER BY views DESC LIMIT 8").fetchall()
    accounts = [dict(a, prog=progress(a)) for a in c.execute("SELECT * FROM accounts ORDER BY lang, platform").fetchall()]
    payouts = c.execute("SELECT * FROM payouts ORDER BY date DESC LIMIT 20").fetchall()
    conf = cfg()
    return tpl.TemplateResponse(request, "index.html", {
        "rows": rows, "kpi": kpi, "by_lang": by_lang, "by_channel": by_channel, "top": top,
        "accounts": accounts, "payouts": payouts, "channels": conf["channels"], "mode": setting("POSTING_MODE") or conf["posting"]["mode"],
        "status": status, "fmt": lambda ts: datetime.datetime.fromtimestamp(ts).strftime("%d.%m. %H:%M") if ts else "–"})


@app.post("/variant/{vid}")
def variant(vid: int, action: str = Form(...), views: int = Form(0), url: str = Form(""),
            earnings: float = Form(0.0), user=Depends(auth)):
    c = db.conn()
    if action == "approve":
        c.execute("UPDATE variants SET status='approved' WHERE id=?", (vid,))
    elif action == "reject":
        c.execute("UPDATE variants SET status='rejected' WHERE id=?", (vid,))
    elif action == "retry":
        c.execute("UPDATE variants SET status='approved', error=NULL WHERE id=?", (vid,))
    elif action == "stats":
        c.execute("UPDATE variants SET views=?, post_url=COALESCE(NULLIF(?,''),post_url), earnings_usd=? WHERE id=?",
                  (views, url, earnings, vid))
    c.commit()
    return RedirectResponse("/", 303)


@app.post("/account/{aid}")
def account(aid: int, followers: int = Form(0), views_30d: int = Form(0), views_90d: int = Form(0), user=Depends(auth)):
    c = db.conn()
    c.execute("UPDATE accounts SET followers=?, views_30d=?, views_90d=?, updated=? WHERE id=?",
              (followers, views_30d, views_90d, time.time(), aid))
    c.commit()
    return RedirectResponse("/#accounts", 303)


@app.post("/payout")
def payout(date: str = Form(...), source: str = Form(...), amount_eur: float = Form(...), note: str = Form(""),
           user=Depends(auth)):
    c = db.conn()
    c.execute("INSERT INTO payouts(date,source,amount_eur,note) VALUES(?,?,?,?)", (date, source, amount_eur, note))
    c.commit()
    return RedirectResponse("/#payouts", 303)


def _version():
    try:
        g = os.path.join(ROOT, ".git")
        head = open(os.path.join(g, "HEAD")).read().strip()
        if head.startswith("ref:"):
            ref = head[5:]
            p = os.path.join(g, ref)
            if os.path.exists(p):
                return open(p).read().strip()[:7]
            for ln in open(os.path.join(g, "packed-refs")):
                if ln.strip().endswith(ref):
                    return ln[:7]
        return head[:7]
    except Exception:
        return "?"


@app.get("/api/selftest")
def api_selftest(token: str = ""):
    """Prüft Claude-Key (Hook-Erzeugung) + Tools, ohne etwas zu posten."""
    _check_token(token)
    import shutil
    from . import hooks
    h = hooks.generate("Lacy", "bro the cops are chasing me in Vice City GTA 6 this is insane",
                       ["[stream title: GTA 6 day one]", "LMAOOO", "GTA 6 IS CRAZY"], ["en"])
    upd = ""
    try:
        upd = open(os.path.join(os.path.dirname(CLIPS), "update.log")).read()[-600:]
    except Exception:
        pass
    return {"hook_ok": not h.get("_error"), "error": h.get("_error"), "game": h.get("game"),
            "hook": h["variants"]["en"]["hook"], "ffmpeg": bool(shutil.which("ffmpeg")),
            "yt_dlp": bool(shutil.which("yt-dlp")), "streamlink": bool(shutil.which("streamlink")),
            "version": _version(), "update_log": upd}


@app.get("/health")
def health():
    return {"ok": True, "version": _version()}
