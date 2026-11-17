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
