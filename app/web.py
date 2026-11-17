"""Dashboard: Clips, Status, Views, Einnahmen, Account-Fortschritt."""
import os, time, secrets, datetime
from fastapi import FastAPI, Depends, HTTPException, Form, Request
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from . import db
from .common import CLIPS, cfg, ROOT

app = FastAPI(title="NeonReplay")
security = HTTPBasic()
tpl = Jinja2Templates(directory=os.path.join(ROOT, "app", "templates"))
FEE = 0.9  # ~10 % Plattformgebühr


def auth(cred: HTTPBasicCredentials = Depends(security)):
    ok_u = secrets.compare_digest(cred.username, os.environ.get("DASHBOARD_USER", "noman"))
    ok_p = secrets.compare_digest(cred.password, os.environ.get("DASHBOARD_PASSWORD", "bitte-aendern"))
    if not (ok_u and ok_p):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    return cred.username


@app.on_event("startup")
def _init():
    db.init()


@app.get("/media/{name}")
def media(name: str):
    path = os.path.join(CLIPS, os.path.basename(name))
    if not name.endswith(".mp4") or "_raw" in name or not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4")


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
        "accounts": accounts, "payouts": payouts, "channels": conf["channels"], "mode": conf["posting"]["mode"],
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


@app.get("/health")
def health():
    return {"ok": True}
