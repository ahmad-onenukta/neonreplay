"""Orchestrator: pro Kanal Capture + Trigger, Job-Queue für Schnitt/Render, Poster-Thread."""
import os, time, queue, threading, logging, secrets
from . import db, media, hooks, poster
from .common import cfg, CLIPS
from .capture import Capture
from .triggers import TwitchChat, AudioWatch

log = logging.getLogger("worker")
jobs = queue.Queue()


class Channel:
    def __init__(self, ch, conf):
        self.ch, self.conf = ch, conf
        self.cap = Capture(ch, conf["buffer"])
        self.last_fire = 0
        self.pending = None

    def start(self):
        self.cap.start()
        t = self.conf["trigger"]
        if self.ch["platform"] == "twitch":
            login = self.ch["url"].rstrip("/").split("/")[-1]
            TwitchChat(login, t, self.spike).start()
        AudioWatch(self.cap, t, self.spike).start()

    def spike(self, kind, score, chat):
        now = time.time()
        if not self.cap.live:
            return
        if self.pending:  # bestehenden Trigger verstärken
            self.pending["score"] += score
            self.pending["kinds"].add(kind)
            self.pending["chat"] += chat
            return
        if now - self.last_fire < self.conf["trigger"]["cooldown_seconds"]:
            return
        self.last_fire = now
        self.pending = {"t": now, "score": score, "kinds": {kind}, "chat": chat}
        threading.Timer(self.conf["trigger"]["post_seconds"], self.fire).start()

    def fire(self):
        p, self.pending = self.pending, None
        t = self.conf["trigger"]
        # Nur Audio ohne Chat braucht höheren Score (sonst zu viele Fehlalarme)
        if p["kinds"] == {"audio"} and p["score"] < 2.5:
            return
        segs = self.cap.segments(t["pre_seconds"] + t["post_seconds"])
        if len(segs) < 2:
            return
        jobs.put({"channel": self.ch, "segments": list(segs), "trigger": "+".join(sorted(p["kinds"])),
                  "score": round(p["score"], 2), "chat": p["chat"]})
        log.info("%s: Trigger %s score=%.1f", self.ch["name"], p["kinds"], p["score"])


def process(job, conf):
    c = db.conn()
    ch = job["channel"]
    stamp = int(time.time())
    raw = os.path.join(CLIPS, f"{ch['name']}_{stamp}_{secrets.token_hex(5)}_raw.mp4")
    cur = c.execute("INSERT INTO clips(created,channel,campaign,cpm_usd,trigger,score,raw_path,status) "
                    "VALUES(?,?,?,?,?,?,?, 'processing')",
                    (stamp, ch["name"], ch.get("campaign", ""), ch.get("cpm_usd", 1.0), job["trigger"], job["score"], raw))
    clip_id = cur.lastrowid
    c.commit()
    try:
        # Segmente sofort kopieren, bevor der Ringpuffer sie überschreibt
        media.concat(job["segments"], raw)
        dur = media.duration(raw)
        text, lang, words = ("", "en", [])
        if conf["transcribe"]["engine"] != "none":
            text, lang, words = media.transcribe(raw, conf["transcribe"]["model"])
        h = hooks.generate(ch["name"], text, job["chat"], conf["languages"])
        spoiler = h.get("spoiler") or any(k in text.lower() for k in conf["spoiler_keywords"])
        status = "spoiler" if spoiler else ("lowq" if h.get("quality", 6) < 5 else "done")
        c.execute("UPDATE clips SET transcript=?, status=?, score=score*? WHERE id=?",
                  (text, status, h.get("quality", 6) / 6, clip_id))
        c.commit()
        if status != "done":
            log.info("clip %s verworfen: %s", clip_id, status)
            return
        for l in conf["languages"]:
            v = h["variants"][l]
            ass = raw.replace("_raw.mp4", f"_{l}.ass")
            out = raw.replace("_raw.mp4", f"_{l}.mp4")
            # Wort-Untertitel nur, wenn Clip-Sprache = Zielsprache (sonst nur Hook)
            media.build_ass(ass, v["hook"], words, dur, conf["render"]["accent_color"], show_subs=(l == lang))
            media.render_vertical(raw, ass, out, conf["render"]["width"], conf["render"]["height"])
            c.execute("INSERT INTO variants(clip_id,lang,hook,caption,video_path) VALUES(?,?,?,?,?)",
                      (clip_id, l, v["hook"], f"{v['caption']}\n\n{v.get('hashtags','')}", out))
            c.commit()
        log.info("clip %s fertig (%s Varianten)", clip_id, len(conf["languages"]))
    except Exception as e:
        c.execute("UPDATE clips SET status='error', error=? WHERE id=?", (str(e)[:500], clip_id))
        c.commit()
        log.exception("clip %s fehler", clip_id)


def renderer(conf):
    while True:
        process(jobs.get(), conf)


def main():
    db.init()
    conf = cfg()
    for ch in conf["channels"]:
        if ch.get("enabled"):
            Channel(ch, conf).start()
            log.info("Kanal aktiv: %s", ch["name"])
    for _ in range(int(os.environ.get("RENDER_WORKERS", "2"))):
        threading.Thread(target=renderer, args=(conf,), daemon=True).start()
    threading.Thread(target=poster.loop, daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
