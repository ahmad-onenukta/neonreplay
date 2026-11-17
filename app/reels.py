"""Eigene Reels ohne Kampagne (mit NEONREPLAY-Branding):
- frames:  Kontaktabzug (jpg) eines Videos zum Auswählen von Szenen
- reel:    Ausschnitt (oder Vergleich oben/unten) + Hook + Branding -> Variante 'ready'
Auftrag als JSON in data/vod_queue mit "type": "frames" | "reel".
"""
import os, time, secrets, subprocess, logging
from . import db, media
from .common import CLIPS, cfg

log = logging.getLogger("reels")


def _dl(url, start, end, out):
    r = subprocess.run(["yt-dlp", "-q", "--no-part", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
                        "--download-sections", f"*{start}-{end}", "--force-keyframes-at-cuts",
                        "--merge-output-format", "mp4", "-o", out, url], capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError((r.stderr or "download fehlgeschlagen")[-600:])
    return out


def frames(req):
    """Alle `every` Sekunden ein Bild mit Zeitstempel -> eine jpg-Kachel."""
    every = int(req.get("every", 5))
    name = req.get("name") or secrets.token_hex(3)
    src = os.path.join(CLIPS, f"src_{name}_raw.mp4")
    if not os.path.exists(src):
        r = subprocess.run(["yt-dlp", "-q", "--no-part", "-f", "b[height<=480]/bv*[height<=480]+ba/w",
                            "--merge-output-format", "mp4", "-o", src, req["url"]], capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "")[-600:])
    out = os.path.join(CLIPS, f"sheet_{name}.jpg")
    media.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf",
               f"setpts=PTS,fps=1/{every},scale=320:-2,drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
               f"text='%{{eif\\:n*{every}\\:d}}s':x=6:y=6:fontsize=22:fontcolor=yellow:box=1:boxcolor=black@0.6,tile=6x10",
               "-frames:v", "1", "-q:v", "4", out])
    log.info("sheet fertig: %s", out)


def _label_ass(path, hook, labels, dur, accent):
    media.build_ass(path, hook, [], dur, accent, show_subs=False, brand=True)
    if labels:
        with open(path, "a", encoding="utf-8") as f:
            # Labels links oben in jeder Hälfte (oben: y~420, unten: y~1020)
            for txt, y in zip(labels, (440, 1040)):
                f.write(f"Dialogue: 3,{media._ts(0)},{media._ts(dur)},Hook,,0,0,0,,{{\\an7\\pos(40,{y})\\fs56}}{media._esc(txt)}\n")


def reel(req):
    conf = cfg()
    w, h = conf["render"]["width"], conf["render"]["height"]
    stamp = int(time.time())
    base = os.path.join(CLIPS, f"reel_{stamp}_{secrets.token_hex(4)}")
    top = _dl(req["top"]["url"], req["top"]["start"], req["top"]["end"], base + "_top_raw.mp4")
    bottom = None
    if req.get("bottom"):
        bottom = _dl(req["bottom"]["url"], req["bottom"]["start"], req["bottom"]["end"], base + "_bot_raw.mp4")
    durs = [media.duration(top)] + ([media.duration(bottom)] if bottom else [])
    dur = min(durs)
    ass = base + ".ass"
    out = base + "_en.mp4"
    _label_ass(ass, req["hook"], req.get("labels") if bottom else None, dur, conf["render"]["accent_color"])
    ass_esc = ass.replace(":", "\\:").replace("'", "\\'")
    if bottom:
        # Vergleich: zwei 16:9-Bilder (1080x608) übereinander, Ton vom unteren (GTA 6)
        vf = (f"[0:v]scale={w}:-2,crop={w}:608[a];[1:v]scale={w}:-2,crop={w}:608[b];"
              f"color=c=0x0B0B12:s={w}x{h}:d={dur}[bg];[bg][a]overlay=0:400[t1];[t1][b]overlay=0:1010,"
              f"subtitles='{ass_esc}'[v]")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", top, "-i", bottom, "-filter_complex", vf,
               "-map", "[v]", "-map", "1:a?", "-t", str(dur)]
    else:
        vf = (f"[0:v]crop=ih*4/5:ih,split[a][b];"
              f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=25:2,eq=brightness=-0.15[bg];"
              f"[b]scale={w}:-2[fg];[bg][fg]overlay=0:(H-h)/2-40,subtitles='{ass_esc}'[v]")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", top, "-filter_complex", vf, "-map", "[v]", "-map", "0:a?"]
    media.run(cmd + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", "30", "-c:a", "aac", "-b:a", "160k",
                     "-movflags", "+faststart", out])
    c = db.conn()
    cur = c.execute("INSERT INTO clips(created,channel,campaign,cpm_usd,trigger,score,raw_path,status,transcript) "
                    "VALUES(?,?,?,?,?,?,?, 'done', ?)",
                    (stamp, "neonreplay", "", 0, "reel", 1, top, req.get("note", "")))
    caption = f"{req['caption']}\n\n{req.get('hashtags', '#gta6 #gta5 #gta #rockstargames #neonreplay')}"
    c.execute("INSERT INTO variants(clip_id,lang,hook,caption,video_path) VALUES(?,?,?,?,?)",
              (cur.lastrowid, "en", req["hook"], caption, out))
    c.commit()
    for f in (top, bottom):
        if f and os.path.exists(f):
            os.remove(f)
    log.info("reel fertig: %s", out)
