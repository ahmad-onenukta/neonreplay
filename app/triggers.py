"""Highlight-Erkennung: Twitch-Chat-Spitzen + Audio-Peaks."""
import socket, threading, time, collections, re, subprocess, os, glob, logging

log = logging.getLogger("trigger")
HYPE = re.compile(r"\b(KEKW|LUL|OMEGALUL|PogChamp|Pog|W+|LMAO+|NO WAY|WTF|OMG|💀|😂)\b", re.I)


class TwitchChat(threading.Thread):
    """Anonymer IRC-Listener, berechnet Nachrichten/Sek + Hype-Anteil."""

    def __init__(self, channel, tcfg, on_spike):
        super().__init__(daemon=True)
        self.channel, self.t, self.on_spike = channel.lower(), tcfg, on_spike
        self.win = collections.deque()
        self.base = 1.0
        self.recent = collections.deque(maxlen=80)

    def run(self):
        while True:
            try:
                self._loop()
            except Exception as e:
                log.warning("chat %s: %s – reconnect", self.channel, e)
                time.sleep(10)

    def _loop(self):
        s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=300)
        s.send(b"NICK justinfan%d\r\n" % (int(time.time()) % 100000))
        s.send(f"JOIN #{self.channel}\r\n".encode())
        buf = ""
        while True:
            data = s.recv(4096).decode(errors="ignore")
            if not data:
                raise ConnectionError("closed")
            buf += data
            lines, buf = buf.split("\r\n")[:-1], buf.split("\r\n")[-1]
            now = time.time()
            for ln in lines:
                if ln.startswith("PING"):
                    s.send(b"PONG :tmi.twitch.tv\r\n")
                elif "PRIVMSG" in ln:
                    msg = ln.split(" :", 1)[-1] if " :" in ln else ""
                    self.win.append((now, bool(HYPE.search(msg))))
                    self.recent.append(msg)
            self._evaluate(now)

    def _evaluate(self, now):
        w = self.t["window_seconds"]
        while self.win and self.win[0][0] < now - w:
            self.win.popleft()
        rate = len(self.win) / w
        hype = sum(h for _, h in self.win) / max(1, len(self.win))
        if rate > self.t["spike_factor"] * self.base and rate >= self.t["min_msgs_per_sec"]:
            score = rate / max(self.base, 0.1) * (1 + hype)
            self.on_spike("chat", score, list(self.recent)[-30:])
        self.base = 0.995 * self.base + 0.005 * max(rate, 0.1)


def segment_loudness(path):
    """(mean_db, max_db) eines Segments via ffmpeg volumedetect."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-vn", "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    mean = re.search(r"mean_volume: (-?[\d.]+)", r.stderr)
    mx = re.search(r"max_volume: (-?[\d.]+)", r.stderr)
    return (float(mean.group(1)) if mean else -90.0, float(mx.group(1)) if mx else -90.0)


class AudioWatch(threading.Thread):
    """Prüft jedes neue Segment; Peak = Lautstärke deutlich über dem Durchschnitt."""

    def __init__(self, capture, tcfg, on_spike):
        super().__init__(daemon=True)
        self.cap, self.t, self.on_spike = capture, tcfg, on_spike
        self.seen, self.base = set(), None

    def run(self):
        while True:
            time.sleep(3)
            segs = sorted(glob.glob(os.path.join(self.cap.dir, "*.ts")), key=os.path.getmtime)[:-1]
            if not segs:
                continue
            last = segs[-1]
            key = (last, os.path.getmtime(last))
            if key in self.seen:
                continue
            self.seen.add(key)
            mean, mx = segment_loudness(last)
            if self.base is None:
                self.base = mean
                continue
            if mean > self.base + 6 and mx >= self.t["audio_peak_db"]:
                self.on_spike("audio", (mean - self.base) / 3, [])
            self.base = 0.95 * self.base + 0.05 * mean
