"""Schnitt, Transkription, Untertitel/Hook (ASS, Urdu-fähig) und 9:16-Render."""
import os, subprocess, tempfile, logging, shutil, sys, threading

log = logging.getLogger("media")
_model = None


def run(cmd, binary=False):
    r = subprocess.run(cmd, capture_output=True, text=not binary)
    if r.returncode != 0:
        err = r.stderr if not binary else r.stderr.decode(errors="ignore")
        raise RuntimeError(err[-800:])
    return r


def concat(segments, out):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for s in segments:
            f.write(f"file '{os.path.abspath(s)}'\n")
        lst = f.name
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", out])
    os.remove(lst)
    return out


def duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
    return float(r.stdout.strip() or 0)


def transcribe(path, model="small"):
    """Liefert (text, sprache, wörter[(start,end,wort)])."""
    global _model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return "", "en", []
    if _model is None:
        _model = WhisperModel(model, device="auto", compute_type="int8")
    # Audio selbst per ffmpeg dekodieren (umgeht PyAV-Versionskonflikte)
    import numpy as np
    pcm = run(["ffmpeg", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"],
              binary=True).stdout
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    segs, info = _model.transcribe(audio, word_timestamps=True, vad_filter=True)
    words, text = [], []
    for s in segs:
        text.append(s.text.strip())
        for w in (s.words or []):
            words.append((w.start, w.end, w.word.strip()))
    return " ".join(text), info.language, words


def _ts(t):
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


_EMOJI = __import__("re").compile("[\U0001F000-\U0001FAFF☀-➿️‍]+")


def _esc(t):
    # libass kann keine Farb-Emojis -> im Video entfernen (Caption behält sie)
    t = _EMOJI.sub("", t).strip()
    return t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(path, hook, words, dur, accent="#FF2E88", show_subs=True, brand=False, hook_y=230):
    """ASS mit Hook oben (ganzer Clip) + Wort-Untertiteln in 3er-Gruppen, aktives Wort in Cyan."""
    r, g, b = accent[1:3], accent[3:5], accent[5:7]
    acc = f"&H00{b}{g}{r}".upper()
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,Noto Sans,78,&H00FFFFFF,&H00FFFFFF,{acc},&H00000000,1,0,0,0,100,100,0,0,3,6,0,8,60,60,{hook_y},1
Style: Brand,Noto Sans,54,{acc},&H00FFFFFF,&H00FFE500,&H00000000,1,1,0,0,100,100,6,0,1,3,0,2,60,60,90,1
Style: Sub,Noto Sans,70,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,6,2,2,60,60,420,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = [f"Dialogue: 1,{_ts(0)},{_ts(dur)},Hook,,0,0,0,,{_esc(hook)}"]
    if brand:  # nur bei Videos OHNE Kampagne (Kampagnen verbieten Logos/Wasserzeichen)
        ev.append(f"Dialogue: 2,{_ts(0)},{_ts(dur)},Brand,,0,0,0,,NEONREPLAY")
    if show_subs:
        for i in range(0, len(words), 3):
            grp = words[i:i + 3]
            for j, (st, en, _) in enumerate(grp):
                end = grp[j + 1][0] if j + 1 < len(grp) else en
                txt = " ".join(("{\\c&H00FFE500&}" + _esc(w) + "{\\c&H00FFFFFF&}") if k == j else _esc(w)
                               for k, (_, _, w) in enumerate(grp))
                ev.append(f"Dialogue: 0,{_ts(st)},{_ts(max(end, st + 0.05))},Sub,,0,0,0,,{txt}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(ev) + "\n")
    return path


def _frames(src, n=6, width=640):
    """n Standbilder (Graustufen, numpy) gleichmäßig über den Clip verteilt."""
    import numpy as np
    dur = duration(src) or 1
    out = []
    for i in range(n):
        t = dur * (i + 1) / (n + 1)
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1",
                            "-vf", f"scale={width}:-2,format=gray", "-f", "rawvideo", "pipe:1"], capture_output=True)
        if r.returncode == 0 and r.stdout:
            h = len(r.stdout) // width
            out.append(np.frombuffer(r.stdout[:h * width], dtype=np.uint8).reshape(h, width))
    return out


def find_facecam(src):
    """Sucht eine STATISCHE Facecam (Gesicht an gleicher Stelle in >=3 von 6 Frames).
    Liefert (x, y, w, h) relativ 0..1 für den Facecam-Ausschnitt oder None."""
    try:
        import cv2
    except ImportError:
        return None
    frames = _frames(src)
    if len(frames) < 3:
        return None
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    H, W = frames[0].shape
    dets = []
    for f in frames:
        faces = casc.detectMultiScale(f, scaleFactor=1.1, minNeighbors=6, minSize=(int(W * 0.03), int(W * 0.03)))
        dets.append([(x + w / 2, y + h / 2, w, h) for (x, y, w, h) in faces])
    best = None
    for fi, fl in enumerate(dets):
        for (cx, cy, w, h) in fl:
            close = lambda d: abs(d[0] - cx) < W * 0.04 and abs(d[1] - cy) < H * 0.04
            hits = [d for other in dets for d in other if close(d)]
            n = sum(1 for other in dets if any(close(d) for d in other))
            score = (n, w * h)
            if n >= 3 and (best is None or score > best[0]):
                best = (score, cx, cy, sum(d[2] for d in hits) / len(hits), sum(d[3] for d in hits) / len(hits))
    if not best:
        return None
    _, cx, cy, fw, fh = best
    ratio = 1080 / 700                      # Seitenverhältnis des oberen Panels
    bh = min(H, fh * 2.8)
    bw = min(W, max(fw * 3.2, bh * ratio))
    bh = min(H, bw / ratio)
    x = min(max(0, cx - bw / 2), W - bw)
    y = min(max(0, cy - bh * 0.45), H - bh)
    return (x / W, y / H, bw / W, bh / H)


def render_vertical(src, ass, out, w=1080, h=1920, cam=None):
    """cam=None: Spielszene mittig über unscharfem Hintergrund.
    cam=(x,y,w,h): Facecam oben (1080x700), Spielszene unten (1080x1220) – Standard viraler Streamer-Clips."""
    ass_esc = ass.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    if cam:
        cx, cy, cw, ch = cam
        top_h, bot_h = 700, h - 700
        vf = (f"[0:v]split[a][b];"
              f"[a]crop=iw*{cw:.4f}:ih*{ch:.4f}:iw*{cx:.4f}:ih*{cy:.4f},scale={w}:{top_h}:force_original_aspect_ratio=increase,crop={w}:{top_h}[cam];"
              f"[b]crop=ih*{w / bot_h:.4f}:ih,scale={w}:{bot_h}[game];"
              f"[cam][game]vstack=inputs=2,subtitles='{ass_esc}'[v]")
    else:
        vf = (f"[0:v]crop=ih:ih,split[a][b];"
              f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=25:2,eq=brightness=-0.15[bg];"
              f"[b]scale={w}:{w}[fg];[bg][fg]overlay=0:(H-h)/2-60,subtitles='{ass_esc}'[v]")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", "30", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", out])
    return out


def cover(video, jpg, t=1.2):
    """Cover-Bild = Frame mit eingebranntem Hook (für IG/YouTube-Thumbnail)."""
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t}", "-i", video, "-frames:v", "1", "-q:v", "3", jpg])
    return jpg


_SEP_LOCK = threading.Lock()  # Demucs braucht viel RAM/CPU -> immer nur ein Lauf gleichzeitig


def _music_share(stems, win=1.0, sr=44100):
    """Anteil der Sekunden, in denen Schlagzeug+Bass einen großen Teil der Lautstärke ausmachen (= Song läuft).
    Spiel-Effekte (Schüsse, Explosionen) sind kurz, Radiomusik ist durchgehend."""
    import numpy as np
    n = int(win * sr)
    mono = {k: (v.mean(axis=1) if v.ndim > 1 else v) for k, v in stems.items()}
    L = min(len(x) for x in mono.values())
    mix = sum(mono[k][:L] for k in mono)
    rhythm = mono["drums"][:L] + mono["bass"][:L]
    hits = total = 0
    for i in range(0, L - n + 1, n):
        m = float(np.sqrt(np.mean(mix[i:i + n] ** 2)))
        if m < 0.01:  # Stille
            continue
        total += 1
        r = float(np.sqrt(np.mean(rhythm[i:i + n] ** 2)))
        if r > 0.35 * m:
            hits += 1
    return hits / total if total else 0.0


def remove_music(src, out, threshold=0.4):
    """Trennt den Ton per Demucs. Läuft lizenzierte Musik (z. B. GTA-Radio), wird nur die Stimmspur
    behalten (Streamer-Stimme). Gibt True zurück, wenn `out` mit gefiltertem Ton erzeugt wurde.
    Fehlt Demucs oder schlägt etwas fehl -> False (Originalton bleibt)."""
    try:
        import soundfile as sf
        import demucs  # noqa: F401
    except ImportError:
        return False
    tmp = tempfile.mkdtemp(prefix="sep_")
    try:
        wav = os.path.join(tmp, "a.wav")
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vn", "-ac", "2", "-ar", "44100", wav])
        with _SEP_LOCK:
            run([sys.executable, "-m", "demucs", "-n", "htdemucs", "-d", "cpu", "-j", "2", "-o", tmp, wav])
        d = os.path.join(tmp, "htdemucs", "a")
        stems = {k: sf.read(os.path.join(d, f"{k}.wav"))[0] for k in ("vocals", "drums", "bass", "other")}
        share = _music_share(stems)
        log.info("Musik-Anteil %.0f%% in %s", share * 100, os.path.basename(src))
        if share < threshold:
            return False
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-i", os.path.join(d, "vocals.wav"),
             "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", out])
        return True
    except Exception as e:
        log.warning("Musik-Filter fehlgeschlagen (%s): %s", os.path.basename(src), str(e)[-300:])
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
