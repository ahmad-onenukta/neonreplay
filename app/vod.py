"""VOD-Modus: alte Streams (Twitch-VODs) per Chat-Replay-Spikes clippen.

Auftrag: data/vod_queue/*.json  {"channel": "lacy", "url": "<vod-url oder leer>", "n": 5, "gta_only": true}
Leere url = neuestes GTA-VOD des Kanals (nur GTA!).
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
    vods = list_vods(name, 60)
    if gta_only:
        g = [v for v in vods if "gta" in v["title"].lower() or "grand theft" in v["title"].lower()]
        return g[0] if g else None  # nur GTA - sonst nichts
    return vods[0] if vods else None


def chat_times(url):
    from chat_downloader import ChatDownloader
    out = []
    try:
        for m in ChatDownloader().get_chat(url, message_groups=["messages"], max_attempts=3,
                                           interruptible_retry=False, retry_timeout=5):
            t = m.get("time_in_seconds")
            if t is not None and t >= 0:
                out.append((float(t), m.get("message") or ""))
    except Exception as e:
        log.warning("Chat-Replay nicht verfuegbar (%s) - nutze Audio", str(e)[:200])
        return []
    return out


def audio_peaks(url, n, max_hours=5, bin_s=5, min_gap=150):
    """Fallback ohne Chat: lauteste Momente (Schreien/Lachen/Action) im VOD-Audio."""
    import numpy as np
    sr = 8000
    cmd = ["yt-dlp", "-q", "-f", "Audio_Only/audio_only/bestaudio/worst", "--download-sections", f"*0-{max_hours*3600}",
           "-o", "-", url]
    dl = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(sr), "-f", "s16le", "pipe:1"],
                          stdin=dl.stdout, stdout=subprocess.PIPE)
    dl.stdout.close()
    rms, chunk = [], sr * bin_s * 2
    while True:
        b = ff.stdout.read(chunk)
        if not b:
            break
        a = np.frombuffer(b[:len(b) // 2 * 2], dtype=np.int16).astype(np.float32)
        rms.append(float(np.sqrt(np.mean(a * a))) if a.size else 0.0)
    ff.wait()
    if len(rms) < 20:
        raise RuntimeError("Audio-Download fehlgeschlagen")
    r = np.convolve(np.array(rms), np.ones(3) / 3, mode="same")
    base = max(float(np.median(r)), 1.0)
    order = np.argsort(-r)
    peaks = []
    for i in order:
        if r[i] < base * 1.8:
            break
        t = int(i) * bin_s
        if all(abs(t - p[0]) > min_gap for p in peaks):
            peaks.append((t, round(float(r[i] / base), 2)))
        if len(peaks) >= n:
            break
    return sorted(peaks)


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
            raise RuntimeError("kein GTA-VOD in den letzten 60 VODs gefunden")
        url, title = v["url"], v["title"]
    log.info("VOD %s: %s %s – Chat wird geladen", ch["name"], url, title)
    msgs = chat_times(url)
    t = conf["trigger"]
    n = int(req.get("n", 5))
    peaks = find_peaks(msgs, n)
    trig = "vod-chat"
    if len(peaks) < max(1, n // 2):
        peaks, trig = audio_peaks(url, n), "vod-audio"
    log.info("VOD %s: %d Nachrichten, %d Peaks (%s)", ch["name"], len(msgs), len(peaks), trig)
    for pt, score in peaks:
        s, e = pt - t["pre_seconds"] + 10, pt + t["post_seconds"] + 10
        f = os.path.join(CLIPS, f"vod_{ch['name']}_{int(pt)}_{secrets.token_hex(3)}_src_raw.mp4")
        try:
            cut(url, s, e, f)
        except Exception as ex:
            log.warning("VOD-Schnitt %s fehlgeschlagen: %s", pt, ex)
            continue
        chat = ([f"[stream title: {title}]"] if title else []) + [m for tt, m in msgs if s <= tt <= e][:200]
        put_job({"channel": ch, "segments": [f], "trigger": trig, "score": round(score, 2),
                 "chat": chat, "cleanup": [f]})


def loop(conf, put_job, only_frames=False):
    """Zwei Threads: einer nur für Kontaktabzüge (lange Downloads), einer für Reels/VODs."""
    while True:
        for p in sorted(glob.glob(os.path.join(QDIR, "*.json"))):
            try:
                with open(p) as f:
                    req = json.load(f)
                if (req.get("type") == "frames") != only_frames:
                    continue
                os.rename(p, p + ".running")
                if req.get("type") in ("frames", "reel"):
                    from . import reels
                    getattr(reels, req["type"])(req)
                elif req.get("type") == "livetest":
                    from . import reels
                    reels.livetest(req, put_job)
                else:
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
