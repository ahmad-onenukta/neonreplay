"""Orchestrator: pro Kanal Capture + Trigger, Job-Queue für Schnitt/Render, Poster-Thread."""
import os, time, queue, threading, logging, secrets
from . import db, media, hooks, poster, vod
from .common import cfg, CLIPS, settings
from .capture import Capture
from .triggers import make_chat, AudioWatch

log = logging.getLogger("worker")
jobs = queue.Queue()
_GAME_CACHE = {}


def stream_info(name):
    """Aktuelle Twitch-Kategorie + Titel (60 s Cache). Leer, wenn offline/unbekannt."""
    import requests
    now = time.time()
    c = _GAME_CACHE.get(name)
    if c and now - c[0] < 60:
        return c[1]
    info = {"game": "", "title": ""}
    try:
        r = requests.post("https://gql.twitch.tv/gql", timeout=10,
                          headers={"Client-Id": "kimne78kx3ncx6brgo4mv6wki5h1ko"},
                          json={"query": 'query{user(login:"%s"){stream{title game{name}}}}' % name})
        st = (((r.json() or {}).get("data") or {}).get("user") or {}).get("stream") or {}
        info = {"game": ((st.get("game") or {}).get("name") or ""), "title": st.get("title") or ""}
    except Exception as e:
        log.warning("Twitch-Info %s: %s", name, e)
    if not info["game"]:
        try:
            g = requests.get(f"https://decapi.me/twitch/game/{name}", timeout=10).text.strip()
            if g and "offline" not in g.lower() and "error" not in g.lower():
                info["game"] = g
        except Exception:
            pass
    _GAME_CACHE[name] = (now, info)
    return info


def is_gta(text):
    t = (text or "").lower()
    return "grand theft auto" in t or "gta" in t


class Channel:
    def __init__(self, ch, conf):
        self.ch, self.conf = ch, conf
        self.cap = Capture(ch, conf["buffer"])
        self.last_fire = 0
        self.pending = None

    def start(self):
        self.cap.start()
        t = self.conf["trigger"]
        chat = make_chat(self.ch, t, self.spike)
        if chat:
            chat.start()
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
        for f in job.get("cleanup", []):
            try:
                os.remove(f)
            except OSError:
                pass
        dur = media.duration(raw)
        info = stream_info(ch["name"]) if ch.get("platform", "twitch") == "twitch" and not job["trigger"].startswith("vod") else {"game": "", "title": ""}
        chat = list(job["chat"])
        if info["game"]:
            chat = [f"[stream category: {info['game']} | stream title: {info['title']}]"] + chat
            if not is_gta(info["game"]):
                c.execute("UPDATE clips SET transcript=?, status='offtopic', error=? WHERE id=?",
                          ("", f"category: {info['game']}", clip_id))
                c.commit()
                log.info("clip %s verworfen: Kategorie %s", clip_id, info["game"])
                return
        text, lang, words = ("", "en", [])
        if conf["transcribe"]["engine"] != "none":
            text, lang, words = media.transcribe(raw, conf["transcribe"]["model"])
        langs = [l for l in (ch.get("langs") or conf["languages"]) if l in conf["languages"]] or ["en"]
        h = hooks.generate(ch.get("display", ch["name"]), text, chat, langs)
        spoiler = h.get("spoiler") or any(k in text.lower() for k in conf["spoiler_keywords"])
        game = (h.get("game") or "").lower()
        if is_gta(info["game"]):
            game = "gta"  # Twitch-Kategorie ist verlässlicher als die Modell-Schätzung
        if h.get("_error"):
            status = "nohook"  # ohne echten Hook nicht rendern
        elif spoiler:
            status = "spoiler"
        elif not is_gta(game):
            status = "offtopic"  # nur GTA-Inhalte
        else:
            status = "lowq" if h.get("quality", 6) < conf.get("min_quality", 7) else "done"
        c.execute("UPDATE clips SET transcript=?, status=?, score=score*?, error=? WHERE id=?",
                  (text, status, h.get("quality", 6) / 6, h.get("_error") or (f"game: {h.get('game')}" if h.get("game") else None), clip_id))
        c.commit()
        if status != "done":
            log.info("clip %s verworfen: %s", clip_id, status)
            return
        cam = None
        try:
            cam = None if ch.get("full_frame") else media.find_facecam(raw)  # Facecam oben, Spiel unten (Standard viraler Streamer-Clips)
        except Exception as e:
            log.warning("facecam %s: %s", clip_id, e)
        src = raw
        if conf.get("music_filter", True):
            clean = raw.replace("_raw.mp4", "_clean_raw.mp4")
            if media.remove_music(raw, clean):  # GTA-Radiomusik raus (YouTube Content ID), Stimme bleibt
                src = clean
                log.info("clip %s: Musik entfernt", clip_id)
        for l in langs:
            v = h["variants"][l]
            ass = raw.replace("_raw.mp4", f"_{l}.ass")
            out = raw.replace("_raw.mp4", f"_{l}.mp4")
            # Wort-Untertitel nur, wenn Clip-Sprache = Zielsprache (sonst nur Hook)
            media.build_ass(ass, v["hook"], words, dur, conf["render"]["accent_color"], show_subs=(l == lang),
                            brand=not ch.get("campaign"), hook_y=640 if cam else 230)
            media.render_vertical(src, ass, out, conf["render"]["width"], conf["render"]["height"], cam=cam,
                                 full=bool(ch.get("full_frame")))
            try:
                media.cover(out, out.replace(".mp4", ".jpg"))
            except Exception as e:
                log.warning("cover %s: %s", clip_id, e)
            c.execute("INSERT INTO variants(clip_id,lang,hook,caption,video_path) VALUES(?,?,?,?,?)",
                      (clip_id, l, v["hook"], (f"{v['caption']}" + (f"\n{ch['caption_suffix']}" if ch.get("caption_suffix") else "")
                       + f"\n\n{v.get('hashtags','')} {ch.get('hashtags','')}").strip(), out))
            c.commit()
        log.info("clip %s fertig (%s Varianten)", clip_id, len(langs))
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
    for _ in range(int(os.environ.get("RENDER_WORKERS", "2"))):
        threading.Thread(target=renderer, args=(conf,), daemon=True).start()
    threading.Thread(target=poster.loop, daemon=True).start()
    threading.Thread(target=vod.loop, args=(conf, jobs.put), daemon=True).start()
    threading.Thread(target=vod.loop, args=(conf, jobs.put, True), daemon=True).start()
    running = set()
    while True:
        # Kanäle können im Dashboard ein-/ausgeschaltet werden (ohne Neustart einschalten)
        on = settings().get("ENABLED_CHANNELS")
        for ch in conf["channels"]:
            active = ((ch["name"] in on) if on is not None else False) or bool(ch.get("enabled"))
            if ch.get("blocked"):
                active = False
            if active and ch["name"] not in running:
                Channel(ch, conf).start()
                running.add(ch["name"])
                log.info("Kanal aktiv: %s", ch["name"])
        time.sleep(30)


if __name__ == "__main__":
    main()
