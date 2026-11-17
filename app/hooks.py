"""Hook + Caption + Hashtags je Sprache über die Claude API."""
import os, json, logging, re
import requests

log = logging.getLogger("hooks")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
LANG_NAMES = {"en": "English", "de": "German", "ur": "Urdu (Urdu script)"}

PROMPT = """You write viral short-form hooks for GTA 6 stream clips (TikTok, Reels, Shorts).
Streamer: {channel}
Chat right before the moment: {chat}
Transcript of the clip: {transcript}

Return ONLY JSON:
{{"quality": 1-10 (how clip-worthy, be strict),
  "spoiler": true/false (major story spoiler like ending or final mission),
  "variants": {{ {langs} }} }}
Each language value: {{"hook": max 6 words, punchy, curiosity, no clickbait lies, 1 emoji max,
 "caption": 1 short sentence that names {channel} + credit "🎥 {channel}", never negative about {channel}, "hashtags": 4-5 hashtags incl. #gta6}}
Write each variant natively in its language."""


def generate(channel, transcript, chat, langs):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return _fallback(channel, langs)
    lang_spec = ", ".join(f'"{l}": {{...{LANG_NAMES.get(l, l)}...}}' for l in langs)
    body = {"model": MODEL, "max_tokens": 900,
            "messages": [{"role": "user", "content": PROMPT.format(
                channel=channel, chat=" | ".join(chat[-20:])[:1500] or "-",
                transcript=transcript[:2500] or "-", langs=lang_spec)}]}
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", json=body, timeout=60,
                          headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        r.raise_for_status()
        txt = r.json()["content"][0]["text"]
        data = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
        for l in langs:
            data["variants"].setdefault(l, _fallback(channel, [l])["variants"][l])
        return data
    except Exception as e:
        log.warning("hook API Fehler: %s", e)
        return _fallback(channel, langs)


def _fallback(channel, langs):
    base = {"en": ("Chat went crazy 💀", "This GTA 6 moment is insane"),
            "de": ("Chat ist komplett eskaliert 💀", "Dieser GTA 6 Moment ist krank"),
            "ur": ("چیٹ پاگل ہو گئی 💀", "یہ GTA 6 لمحہ کمال ہے")}
    return {"quality": 6, "spoiler": False, "variants": {
        l: {"hook": base.get(l, base["en"])[0], "caption": f"{base.get(l, base['en'])[1]} 🎥 {channel}",
            "hashtags": "#gta6 #gta #gaming #streamer"} for l in langs}}
