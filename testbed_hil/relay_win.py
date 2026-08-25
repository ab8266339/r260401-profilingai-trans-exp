#!/usr/bin/env python3
"""MEC-side relay for the hardware-in-the-loop testbed (Windows host).

Receives UL payloads from emulated UEs (arriving through the tc-shaped
WSL uplink), sinks sensing payloads, forwards agent prompts to the local
Ollama server (RTX 5090), and reports per-request inference time.
Stdlib only; no external dependencies.

Usage:  python relay_win.py [port]      (default 18090)
"""
import json
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OLLAMA = "http://127.0.0.1:11434/api/generate"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # keep stdout clean
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        t0 = time.perf_counter()
        body = self.rfile.read(n)  # bytes arrive through the shaped link
        t_rx = time.perf_counter() - t0
        if self.path == "/sense":
            self._reply({"rx_s": round(t_rx, 4)})
            return
        # /agent: first line = JSON header, remainder = sensing padding
        try:
            head, _, _ = body.partition(b"\n")
            req = json.loads(head)
            payload = json.dumps({
                "model": req["model"],
                "prompt": req["prompt"],
                "stream": False,
                "keep_alive": "30m",
                "options": {"num_predict": int(req.get("num_predict", 120)),
                            "num_ctx": 4096},
            }).encode()
            t1 = time.perf_counter()
            r = urllib.request.urlopen(
                urllib.request.Request(
                    OLLAMA, data=payload,
                    headers={"Content-Type": "application/json"}),
                timeout=180)
            out = json.loads(r.read())
            t_inf = time.perf_counter() - t1
            self._reply({
                "rx_s": round(t_rx, 4),
                "infer_s": round(t_inf, 4),
                "eval_count": out.get("eval_count", 0),
            })
        except Exception as e:  # report, do not crash the soak
            self._reply({"error": str(e)}, code=500)

    def _reply(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18090
    print(f"relay listening on 0.0.0.0:{port}, forwarding to {OLLAMA}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
