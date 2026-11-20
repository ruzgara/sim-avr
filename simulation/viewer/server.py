#!/usr/bin/env python3
"""Tiny web server for the circuit viewer (Python 3 standard library only).

    python3 server.py --port 8000 --diagram PATH -- <launcher argv...>

It starts the launcher as a child process, reads the event lines the launcher
prints on stdout ("io B 0f 05 05", "state running", "exit done") and forwards
them to every browser connected to /events as Server-Sent Events.

    GET /              -> index.html (other paths: static files next to this script)
    GET /diagram.json  -> the circuit file given with --diagram
    GET /events        -> live event stream
"""
import argparse
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

VIEWER_DIR = os.path.dirname(os.path.abspath(__file__))

lock = threading.Lock()   # protects everything below
latest_io = {}            # port letter -> latest "io ..." line
latest_state = None       # latest "state ..." line
clients = set()           # one queue.Queue per connected browser


def broadcast(line):
    """Remember the line if it is state worth replaying, then send it to all browsers."""
    global latest_state
    parts = line.split()
    with lock:
        if parts[0] == "io" and len(parts) >= 2:
            latest_io[parts[1]] = line
        elif parts[0] == "state":
            latest_state = line
        for q in clients:
            q.put(line)


def pump_launcher(proc, httpd):
    """Thread: forward launcher stdout lines; when it ends, announce that and stop the server."""
    saw_exit = False
    for raw in proc.stdout:
        line = raw.strip()
        if not line:
            continue
        saw_exit = saw_exit or line.startswith("exit")
        broadcast(line)
    rc = proc.wait()
    if not saw_exit:                       # launcher died without saying goodbye
        broadcast("exit crashed" if rc else "exit done")
    time.sleep(0.3)                        # let the SSE threads flush the last line
    httpd.shutdown()


class Handler(SimpleHTTPRequestHandler):
    diagram_path = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=VIEWER_DIR, **kwargs)

    def log_message(self, fmt, *args):     # keep the terminal quiet
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/events":
            self.serve_events()
        elif path == "/diagram.json":
            self.serve_diagram()
        else:
            if path == "/":
                self.path = "/index.html"
            super().do_GET()

    def serve_diagram(self):
        try:
            with open(self.diagram_path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404, "diagram not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def serve_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        q = queue.Queue()
        with lock:                         # snapshot + register atomically: no gaps, no dupes
            first = [latest_io[p] for p in sorted(latest_io)]
            if latest_state:
                first.append(latest_state)
            clients.add(q)
        try:
            for line in first:
                self.wfile.write(f"data: {line}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    line = q.get(timeout=15)
                    self.wfile.write(f"data: {line}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")   # detects closed browsers
                self.wfile.flush()
        except OSError:                    # browser went away
            pass
        finally:
            with lock:
                clients.discard(q)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--diagram", required=True)
    ap.add_argument("launcher", nargs=argparse.REMAINDER, help="-- then the launcher command line")
    args = ap.parse_args()
    cmd = args.launcher[1:] if args.launcher[:1] == ["--"] else args.launcher
    if not cmd:
        ap.error("launcher command missing (put it after --)")

    Handler.diagram_path = args.diagram
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)

    # stderr is inherited so launcher messages show up in this terminal.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, bufsize=1)

    def stop(signum, frame):
        # shutdown() blocks until serve_forever() returns, so run it off-thread.
        threading.Thread(target=httpd.shutdown, daemon=True).start()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    threading.Thread(target=pump_launcher, args=(proc, httpd), daemon=True).start()
    print(f"viewer: http://127.0.0.1:{args.port}/", file=sys.stderr)
    try:
        httpd.serve_forever()
    finally:
        if proc.poll() is None:            # make sure the launcher never outlives us
            proc.terminate()
            try:
                proc.wait(2)
            except subprocess.TimeoutExpired:
                proc.kill()
        httpd.server_close()


if __name__ == "__main__":
    main()
