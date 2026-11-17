"""Live-Capture: streamlink -> ffmpeg Ringpuffer (10-Sek-Segmente)."""
import os, subprocess, threading, time, glob, logging
from .common import BUF

log = logging.getLogger("capture")


class Capture(threading.Thread):
    def __init__(self, ch, bcfg):
        super().__init__(daemon=True)
        self.ch, self.b = ch, bcfg
        self.dir = os.path.join(BUF, ch["name"])
        os.makedirs(self.dir, exist_ok=True)
        self.live = False

    def run(self):
        while True:
            for f in glob.glob(os.path.join(self.dir, "*.ts")):
                os.remove(f)
            sl = subprocess.Popen(
                ["streamlink", "--stdout", "--twitch-disable-ads", "--retry-open", "3",
                 self.ch["url"], self.b["quality"]],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            ff = subprocess.Popen(
                ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-c", "copy", "-f", "segment",
                 "-segment_time", str(self.b["segment_seconds"]),
                 "-segment_wrap", str(self.b["segments_kept"]), "-reset_timestamps", "1",
                 os.path.join(self.dir, "seg_%03d.ts")],
                stdin=sl.stdout)
            sl.stdout.close()
            self.live = True
            log.info("%s: capture läuft", self.ch["name"])
            ff.wait(); sl.kill()
            self.live = False
            log.info("%s: offline, neuer Versuch in 60 s", self.ch["name"])
            time.sleep(60)

    def segments(self, seconds):
        """Fertige Segmente der letzten `seconds` Sekunden (älteste zuerst, ohne das gerade geschriebene)."""
        segs = sorted(glob.glob(os.path.join(self.dir, "*.ts")), key=os.path.getmtime)[:-1]
        n = max(1, int(seconds / self.b["segment_seconds"]) + 1)
        return segs[-n:]
