import json, os, glob, html, queue, subprocess, sys, threading, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from faster_whisper import WhisperModel

DB, Q, lock = "songs.json", queue.Queue(), threading.Lock()
whisper = WhisperModel("base", compute_type="int8")
os.makedirs("tmp", exist_ok=True)

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
        subprocess.run([sys.executable, "-m", "yt_dlp", "-f", "bestaudio/best", "-o", f"tmp/{i}.%(ext)s", url],
                       check=True, capture_output=True)
        segs, _ = whisper.transcribe(glob.glob(f"tmp/{i}.*")[0], vad_filter=True)
        text = " ".join(s.text for s in segs).strip()
        song = None
        if text:
            try:
                song = lookup(ask(f"Lyrics heard:\n{text}\n\nReply with ONLY the single most distinctive lyric line, nothing else."))
            except Exception:
                pass
        update(i, status="done", song=song, heard=text[:200])
    except Exception as e:
        update(i, status="failed", heard=str(e)[:200])
    finally:
        for f in glob.glob(f"tmp/{i}.*"): os.remove(f)

def worker():
    while True:
        work(*Q.get())

def page():
    rows = ""
    for d in reversed(load()):
        if d["status"] == "done" and d["song"]:
            s = f'<a href="https://www.youtube.com/results?search_query={urllib.parse.quote(d["song"])}">{html.escape(d["song"])}</a>'
        else:
            s = {"processing": "processing...", "failed": "failed", "done": "unidentified"}[d["status"]]
        rows += f'<div class=c><b>{s}</b><br><small>{html.escape(d["heard"][:80])}</small><br><small><a href="{html.escape(d["url"])}">reel</a></small></div>'
    return ('<meta name=viewport content="width=device-width"><meta http-equiv=refresh content=5>'
            '<style>body{font:16px sans-serif;background:#111;color:#eee;padding:12px}a{color:#7cf}'
            '.c{background:#222;padding:10px;margin:8px 0;border-radius:8px}</style>'
            f'<h2>Songs I shared</h2>{rows or "Nothing yet."}')

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/add":
            url = urllib.parse.parse_qs(u.query).get("url", [""])[0].strip()
            if url:
                with lock:
                    db = load()
                    db.append({"url": url, "status": "processing", "song": None, "heard": ""})
                    save(db)
                    i = len(db) - 1
                Q.put((i, url))
            body, ctype = "queued", "text/plain"
        else:
            body, ctype = page(), "text/html"
        b = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

threading.Thread(target=worker, daemon=True).start()
print("Open http://<laptop-ip>:8000 on your phone")
ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()