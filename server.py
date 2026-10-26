import json, os, glob, html, queue, subprocess, sys, threading, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from faster_whisper import WhisperModel

DB, Q, lock = "songs.json", queue.Queue(), threading.Lock()
whisper = WhisperModel("base", compute_type="int8")  # try "small" for Hindi
os.makedirs("audio", exist_ok=True)

def load(): return json.load(open(DB)) if os.path.exists(DB) else []
def save(db): json.dump(db, open(DB, "w"), indent=2, ensure_ascii=False)

def update(i, **kw):
    with lock:
        db = load(); db[i].update(kw); save(db)

def ask(prompt):
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        json.dumps({"model": "gemma3:1b", "prompt": prompt, "stream": False}).encode(),
        {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))["response"].strip().strip('"')

def lookup(q):
    r = json.load(urllib.request.urlopen("https://lrclib.net/api/search?q=" + urllib.parse.quote(q)))
    return f"{r[0]['trackName']} - {r[0]['artistName']}" if r else None

def work(i, url):
    try:
        r = subprocess.run([sys.executable, "-m", "yt_dlp", "-f", "bestaudio/best", "-o", f"audio/{i}.%(ext)s",
                            "--print", "%(track)s|%(artist)s|%(title)s", "--no-simulate", url],
                           check=True, capture_output=True, text=True)
        track, artist, title = r.stdout.strip().splitlines()[-1].split("|", 2)
        song = (track if artist == "NA" else f"{track} - {artist}") if track != "NA" else None
        heard = ""
        if not song:
            segs, _ = whisper.transcribe(glob.glob(f"audio/{i}.*")[0])
            heard = " ".join(s.text for s in segs).strip()
            if heard:
                try:
                    line = ask(f"Lyrics heard:\n{heard}\n\nReply with ONLY the single most distinctive lyric line, nothing else.")
                    song = lookup(line) or lookup(heard[:60])
                except Exception:
                    pass
        update(i, status="done", song=song, title=title, heard=heard[:200])
    except Exception as e:
        update(i, status="failed", heard=str(e)[:200])

def worker():
    while True:
        work(*Q.get())

def page():
    db, rows = load(), ""
    for i, d in reversed(list(enumerate(db))):
        s, heard = d.get("song"), d.get("heard", "")
        q = lambda t: "https://www.youtube.com/results?search_query=" + urllib.parse.quote(t)
        name = (f'<a href="{q(s)}">{html.escape(s)}</a>' if s else
                html.escape(d.get("title") or {"processing": "processing...", "failed": "failed"}.get(d["status"], "unidentified")))
        find = f' · <a href="{q(heard[:80])}">search lyrics</a>' if heard and not s and d["status"] == "done" else ""
        player = f'<audio controls preload=none src="/audio/{i}" style="width:100%"></audio>' if d["status"] == "done" else ""
        rows += (f'<div class=c><b>{name}</b><br><small>{html.escape(heard[:80])}</small><br>{player}'
                 f'<small><a href="{html.escape(d["url"])}">reel</a>{find}</small></div>')
    refresh = "<meta http-equiv=refresh content=5>" if any(d["status"] == "processing" for d in db) else ""
    return ('<meta name=viewport content="width=device-width">' + refresh +
            '<style>body{font:16px sans-serif;background:#111;color:#eee;padding:12px}a{color:#7cf}'
            '.c{background:#222;padding:10px;margin:8px 0;border-radius:8px}</style>'
            f'<h2>Songs I shared</h2>{rows or "Nothing yet."}')

class H(BaseHTTPRequestHandler):
    def send(self, body, ctype, code=200, hdr={}):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in hdr.items(): self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/audio/"):
            f = glob.glob(f"audio/{u.path[7:]}.*") if u.path[7:].isdigit() else []
            if not f: return self.send(b"", "text/plain", 404)
            data, hdr, code = open(f[0], "rb").read(), {"Accept-Ranges": "bytes"}, 200
            if self.headers.get("Range"):
                a, b = self.headers["Range"].replace("bytes=", "").split("-")
                a, b = int(a or 0), int(b) if b else len(data) - 1
                hdr["Content-Range"], code = f"bytes {a}-{b}/{len(data)}", 206
                data = data[a:b + 1]
            ct = {".m4a": "audio/mp4", ".webm": "audio/webm"}.get(os.path.splitext(f[0])[1], "audio/mp4")
            return self.send(data, ct, code, hdr)
        if u.path == "/add":
            url = urllib.parse.parse_qs(u.query).get("url", [""])[0].strip()
            if url and not url.startswith("http"): url = "https://" + url
            if url:
                with lock:
                    db = load()
                    db.append({"url": url, "status": "processing", "song": None, "heard": ""})
                    save(db)
                    i = len(db) - 1
                Q.put((i, url))
            return self.send(b"queued", "text/plain")
        self.send(page().encode(), "text/html; charset=utf-8")

threading.Thread(target=worker, daemon=True).start()
print("Open http://<laptop-ip>:8000 on your phone")
ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()