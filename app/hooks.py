"""Hook + Caption + Hashtags je Sprache über die Claude API."""
import os, json, logging, re
import requests
from .common import setting

log = logging.getLogger("hooks")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
LANG_NAMES = {"en": "English", "de": "German", "ur": "Urdu (Urdu script)"}

PROMPT = """You are a top short-form editor for streamer clip pages (TikTok, Reels, Shorts). Your hooks get millions of views.
Streamer: {channel}
Chat right before the moment: {chat}
Transcript of the clip: {transcript}

Return ONLY JSON:
{{"quality": 1-10 (be very strict: 8+ only if a stranger who doesn't know {channel} would watch to the end; funny, shocking, rage, clutch or wholesome moments score high; talking/loading/menus score low),
  "spoiler": true/false (major story spoiler like ending or final mission),
  "game": "detected game or 'just chatting' or 'unknown'",
  "variants": {{ {langs} }} }}
Each language value: {{
 "hook": on-screen text, 3-7 words, creates an open loop or tension in the first second (e.g. "He did NOT expect this", "Chat warned him...", "This is why you never trust Lucia"), concrete to THIS clip, no clickbait lies, no emojis, no hashtags,
 "caption": 1-2 short lines: a curiosity line + a question that invites comments (e.g. "Would you have done the same?"), MUST name {channel} naturally, end with "🎥 {channel}", never negative, mocking or insulting about {channel},
 "hashtags": exactly 4 hashtags: #gta6 (only if GTA), one streamer tag, two niche tags like #gta6clips #streamerclips — no generic spam like #fyp #viral}}
Write each variant natively in its language."""


def generate(channel, transcript, chat, langs):
    # unsichtbare/kopierte Sonderzeichen (z. B. Zero-Width) entfernen
    key = "".join(ch for ch in (setting("ANTHROPIC_API_KEY") or "") if 32 < ord(ch) < 127)
    if not key:
        return _fallback(channel, langs)
    lang_spec = ", ".join(f'"{l}": {{...{LANG_NAMES.get(l, l)}...}}' for l in langs)
    body = {"model": MODEL, "max_tokens": 3000, "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": PROMPT.format(
                channel=channel, chat=" | ".join(chat[:1] + chat[-20:] if chat and chat[0].startswith("[stream") else chat[-20:])[:1500] or "-",
                transcript=transcript[:2500] or "-", langs=lang_spec)}]}
    r = None
    try:
        hdr = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        r = requests.post("https://api.anthropic.com/v1/messages", json=body, timeout=60, headers=hdr)
        if r.status_code == 400 and "thinking" in r.text:  # Modell kennt den Parameter nicht -> ohne
            body.pop("thinking", None)
            r = requests.post("https://api.anthropic.com/v1/messages", json=body, timeout=60, headers=hdr)
        r.raise_for_status()
        # Antwort kann Denk-Blöcke enthalten -> nur Text-Blöcke nehmen
        txt = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        data = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
        for l in langs:
            data["variants"].setdefault(l, _fallback(channel, [l])["variants"][l])
            v = data["variants"][l]
            if isinstance(v.get("hashtags"), list):
                v["hashtags"] = " ".join(v["hashtags"])
        return data
    except Exception as e:
        detail = ""
        try:
            detail = r.text[:300]
        except Exception:
            pass
        log.warning("hook API Fehler: %s %s", e, detail)
        fb = _fallback(channel, langs)
        fb["_error"] = f"hook API: {e} {detail}"[:480]
        return fb


def _fallback(channel, langs):
    base = {"en": ("Chat went crazy 💀", "This moment is insane"),
            "de": ("Chat ist komplett eskaliert 💀", "Dieser Moment ist krank"),
            "ur": ("چیٹ پاگل ہو گئی 💀", "یہ لمحہ کمال ہے")}
    return {"quality": 6, "spoiler": False, "variants": {
        l: {"hook": base.get(l, base["en"])[0], "caption": f"{base.get(l, base['en'])[1]} 🎥 {channel}",
            "hashtags": "#gaming #streamer #twitch #clips"} for l in langs}}
