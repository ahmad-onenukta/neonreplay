"""Schnitt, Transkription, Untertitel/Hook (ASS, Urdu-fähig) und 9:16-Render."""
import os, subprocess, tempfile, logging

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


def _esc(t):
    return t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(path, hook, words, dur, accent="#FF2E88", show_subs=True):
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
Style: Hook,Noto Sans,78,&H00FFFFFF,&H00FFFFFF,{acc},&H00000000,1,0,0,0,100,100,0,0,3,6,0,8,60,60,230,1
Style: Sub,Noto Sans,70,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,6,2,2,60,60,420,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = [f"Dialogue: 1,{_ts(0)},{_ts(dur)},Hook,,0,0,0,,{_esc(hook)}"]
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


def render_vertical(src, ass, out, w=1080, h=1920):
    """16:9 mittig über unscharfem, gezoomtem Hintergrund + eingebrannte ASS-Ebene."""
    ass_esc = ass.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    vf = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=25:2,eq=brightness=-0.08[bg];"
          f"[0:v]scale={w}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2-80,subtitles='{ass_esc}'[v]")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", "30", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", out])
    return out
