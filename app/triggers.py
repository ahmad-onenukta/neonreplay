"""Highlight-Erkennung: Chat-Spitzen (Twitch / Kick / YouTube Live) + Audio-Peaks."""
import socket, threading, time, collections, re, subprocess, os, glob, json, logging

log = logging.getLogger("trigger")
HYPE = re.compile(r"\b(KEKW|LUL|OMEGALUL|PogChamp|Pog|W+|LMAO+|NO WAY|WTF|OMG|💀|😂)\b", re.I)


class RateSpike:
    """Gemeinsame Spike-Logik: Nachrichten/Sek im Fenster vs. gleitender Durchschnitt + Hype-Anteil."""

    def __init__(self, tcfg, on_spike, kind="chat"):
        self.t, self.on_spike, self.kind = tcfg, on_spike, kind
        self.win = collections.deque()
        self.base = 1.0
        self.recent = collections.deque(maxlen=80)
        self.lock = threading.Lock()

    def add(self, msg, now=None):
        now = now or time.time()
        with self.lock:
            self.win.append((now, bool(HYPE.search(msg or ""))))
            self.recent.append(msg or "")

    def evaluate(self, now=None):
        now = now or time.time()
        with self.lock:
            w = self.t["window_seconds"]
            while self.win and self.win[0][0] < now - w:
                self.win.popleft()
            rate = len(self.win) / w
            hype = sum(h for _, h in self.win) / max(1, len(self.win))
            fire = rate > self.t["spike_factor"] * self.base and rate >= self.t["min_msgs_per_sec"]
            score = rate / max(self.base, 0.1) * (1 + hype)
            recent = list(self.recent)[-30:]
            self.base = 0.995 * self.base + 0.005 * max(rate, 0.1)
        if fire:
            self.on_spike(self.kind, score, recent)


class _ChatThread(threading.Thread):
    """Basis: reconnect-Schleife + Ticker, damit evaluate() auch bei Stille läuft."""

    def __init__(self, name, tcfg, on_spike):
        super().__init__(daemon=True)
        self.name_ = name
        self.rs = RateSpike(tcfg, on_spike)
        threading.Thread(target=self._tick, daemon=True).start()

    def _tick(self):
        while True:
            time.sleep(1)
            self.rs.evaluate()

    def run(self):
        while True:
            try:
                self._loop()
            except Exception as e:
                log.warning("chat %s: %s – reconnect in 15 s", self.name_, e)
            time.sleep(15)

    def _loop(self):
        raise NotImplementedError


class TwitchChat(_ChatThread):
    """Anonymer Twitch-IRC-Listener."""

    def __init__(self, channel, tcfg, on_spike):
        super().__init__(f"twitch:{channel}", tcfg, on_spike)
        self.channel = channel.lower()

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
            parts = buf.split("\r\n")
            lines, buf = parts[:-1], parts[-1]
            for ln in lines:
                if ln.startswith("PING"):
                    s.send(b"PONG :tmi.twitch.tv\r\n")
                elif "PRIVMSG" in ln:
                    self.rs.add(ln.split(" :", 1)[-1] if " :" in ln else "")


KICK_WS = "wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679?protocol=7&client=js&version=8.4.0&flash=false"
KICK_EVENT = "App\\Events\\ChatMessageEvent"


def kick_chatroom_id(slug):
    """chatroom.id über die Kick-API (Cloudflare kann blocken -> dann `chatroom_id` in config.yaml setzen)."""
    import requests
    r = requests.get(f"https://kick.com/api/v2/channels/{slug}", timeout=20, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
        "Accept": "application/json"})
    r.raise_for_status()
    return int(r.json()["chatroom"]["id"])


class KickChat(_ChatThread):
    """Kick-Chat über den öffentlichen Pusher-Websocket (chatrooms.<id>.v2)."""

    def __init__(self, slug, tcfg, on_spike, chatroom_id=None):
        super().__init__(f"kick:{slug}", tcfg, on_spike)
        self.slug, self.chatroom_id = slug, chatroom_id

    def _loop(self):
        import websocket  # websocket-client
        if not self.chatroom_id:
            self.chatroom_id = kick_chatroom_id(self.slug)
        ws = websocket.create_connection(KICK_WS, timeout=120)
        try:
            while True:
                raw = ws.recv()
                if not raw:
                    raise ConnectionError("closed")
                msg = json.loads(raw)
                ev = msg.get("event")
                if ev == "pusher:connection_established":
                    ws.send(json.dumps({"event": "pusher:subscribe",
                                        "data": {"auth": "", "channel": f"chatrooms.{self.chatroom_id}.v2"}}))
                elif ev == "pusher:ping":
                    ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
                elif ev == KICK_EVENT:
                    data = msg.get("data")
                    data = json.loads(data) if isinstance(data, str) else (data or {})
                    self.rs.add(str(data.get("content", "")))
        finally:
            ws.close()


class YouTubeChat(_ChatThread):
    """YouTube-Live-Chat über chat-downloader (ohne API-Key, blockiert solange der Stream live ist)."""

    def __init__(self, url, tcfg, on_spike):
        super().__init__(f"youtube:{url}", tcfg, on_spike)
        self.url = url

    def _loop(self):
        from chat_downloader import ChatDownloader
        chat = ChatDownloader().get_chat(self.url, message_groups=["messages"])
        for m in chat:
            self.rs.add(str(m.get("message", "")))
        raise ConnectionError("chat beendet (offline?)")


def make_chat(ch, tcfg, on_spike):
    """Passenden Chat-Listener für einen Kanal aus config.yaml bauen (oder None)."""
    p, url = ch.get("platform"), ch.get("url", "").rstrip("/")
    if p == "twitch":
        return TwitchChat(url.split("/")[-1], tcfg, on_spike)
    if p == "kick":
        return KickChat(url.split("/")[-1], tcfg, on_spike, ch.get("chatroom_id"))
    if p == "youtube":
        return YouTubeChat(ch["url"], tcfg, on_spike)
    return None


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
