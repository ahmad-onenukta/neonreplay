import os, yaml, logging

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("DATA_DIR", os.path.join(ROOT, "data"))
BUF = os.path.join(DATA, "buffer")
CLIPS = os.path.join(DATA, "clips")
for d in (DATA, BUF, CLIPS):
    os.makedirs(d, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def cfg():
    with open(os.environ.get("CONFIG", os.path.join(ROOT, "config.yaml"))) as f:
        return yaml.safe_load(f)


SETTINGS = os.path.join(DATA, "settings.json")


def settings():
    import json
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(upd):
    import json
    s = settings()
    s.update({k: v for k, v in upd.items() if v is not None})
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, SETTINGS)
    return s


def setting(key, default=""):
    """Wert aus dem Dashboard (data/settings.json), sonst aus .env."""
    v = settings().get(key)
    return v if v not in (None, "") else os.environ.get(key, default)
