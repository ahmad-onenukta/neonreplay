"""VOD-Modus: alte Streams (Twitch-VODs) per Chat-Replay-Spikes clippen.

Auftrag: data/vod_queue/*.json  {"channel": "lacy", "url": "<vod-url oder leer>", "n": 5, "gta_only": true}
Leere url = neuestes VOD des Kanals (bevorzugt mit GTA im Titel).
"""
import os, json, glob, time, logging, subprocess, statistics, secrets, traceback
from .common import DATA, CLIPS

log = logging.getLogger("vod")
QDIR = os.path.join(DATA, "vod_queue")
os.makedirs(QDIR, exist_ok=True)


def request(channel, url="", n=5, gta_only=True):
    p = os.path.join(QDIR, f"{int(time.time())}_{secrets.token_hex(3)}.json")
    with open(p, "w") as f:
        json.dump({"channel": channel, "url": url, "n": n, "gta_only": gta_only}, f)
    return p


def list_vods(name, limit=15):
    ents = []
    for q in ("?filter=archives&sort=time", "?filter=all&sort=time", ""):
        r = subprocess.run(["yt-dlp", "--flat-playlist", "-J", "--playlist-end", str(limit),
                            f"https://www.twitch.tv/{name}/videos{q}"], capture_output=True, text=True, timeout=180)
        if r.returncode == 0:
            ents = json.loads(r.stdout).get("entries") or []
            if ents:
                break
        else:
            log.warning("yt-dlp Liste %s%s: %s", name, q, r.stderr[-300:])
    return [{"url": e.get("url") or f"https://www.twitch.tv/videos/{e['id']}", "title": e.get("title") or ""}
            for e in ents]


def pick_vod(name, gta_only):
    vods = list_vods(name)
    if gta_only:
        g = [v for v in vods if "gta" in v["title"].lower() or "rp" in v["title"].lower().split()]
        if g:
            return g[0]
    return vods[0] if vods else None


def chat_times(url):
    from chat_downloader import ChatDownloader
    out = []
    for m in ChatDownloader().get_chat(url, message_groups=["messages"]):
        t = m.get("time_in_seconds")
        if t is not None and t >= 0:
            out.append((float(t), m.get("message") or ""))
    return out


def find_peaks(msgs, n, bin_s=5, win=4, min_gap=150):
    if not msgs:
        return []
    end = int(msgs[-1][0]) + 1
    bins = [0] * (end // bin_s + 1)
    for t, _ in msgs:
        bins[int(t) // bin_s] += 1
    roll = [sum(bins[i:i + win]) for i in range(len(bins) - win)]
    if not roll:
        return []
    base = max(statistics.median(roll), 2)
    cand = sorted(range(len(roll)), key=lambda i: roll[i], reverse=True)
    peaks = []
    for i in cand:
        if roll[i] < base * 3:
            break
        t = i * bin_s
        if all(abs(t - p[0]) > min_gap for p in peaks):
            peaks.append((t, roll[i] / base))
        if len(peaks) >= n:
            break
    return sorted(peaks)


def cut(url, start, end, out):
    tmpl = out.replace(".mp4", ".%(ext)s")
    r = subprocess.run(["yt-dlp", "-q", "--no-part", "-f", "best[height<=1080]/best",
                        "--download-sections", f"*{max(0, start)}-{end}", "--force-keyframes-at-cuts",
                        "--remux-video", "mp4", "-o", tmpl, url], capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError((r.stderr or "download fehlgeschlagen")[-500:])
    return out


def handle(req, conf, put_job):
    ch = next((c for c in conf["channels"] if c["name"] == req["channel"]), None)
    if not ch:
        raise RuntimeError("unbekannter Kanal " + req["channel"])
    url = req.get("url")
    title = ""
    if not url:
        v = pick_vod(ch["name"], req.get("gta_only", True))
        if not v:
            raise RuntimeError("kein VOD gefunden")
        url, title = v["url"], v["title"]
    log.info("VOD %s: %s %s – Chat wird geladen", ch["name"], url, title)
    msgs = chat_times(url)
    t = conf["trigger"]
    peaks = find_peaks(msgs, int(req.get("n", 5)))
    log.info("VOD %s: %d Nachrichten, %d Peaks", ch["name"], len(msgs), len(peaks))
    for pt, score in peaks:
        s, e = pt - t["pre_seconds"] + 10, pt + t["post_seconds"] + 10
        f = os.path.join(CLIPS, f"vod_{ch['name']}_{int(pt)}_{secrets.token_hex(3)}_src_raw.mp4")
        try:
            cut(url, s, e, f)
        except Exception as ex:
            log.warning("VOD-Schnitt %s fehlgeschlagen: %s", pt, ex)
            continue
        chat = [m for tt, m in msgs if s <= tt <= e][:200]
        put_job({"channel": ch, "segments": [f], "trigger": "vod-chat", "score": round(score, 2),
                 "chat": chat, "cleanup": [f]})


def loop(conf, put_job):
    while True:
        for p in sorted(glob.glob(os.path.join(QDIR, "*.json"))):
            try:
                with open(p) as f:
                    req = json.load(f)
                os.rename(p, p + ".running")
                handle(req, conf, put_job)
                os.rename(p + ".running", p + ".done")
            except Exception as e:
                log.exception("VOD-Auftrag %s fehlgeschlagen", p)
                try:
                    with open(p + ".error", "w") as f:
                        f.write(traceback.format_exc()[-3000:])
                    if os.path.exists(p + ".running"):
                        os.remove(p + ".running")
                except Exception:
                    pass
        time.sleep(20)
