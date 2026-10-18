"""Decision model for the d3 A/B (NL2Q_CHECKER=d3): vllm-sr/d3 (System One API, a probability for every option) on its own GPU, with a
sleep mode for shared GPUs. A small supervisor on --port forwards requests to a child process running the
package's own d3_server; after --idle-minutes without requests it stops the child (all GPU memory released,
CUDA graphs included) and the next request starts it again (~30 s, weights from the OS file cache).
Runs in d3's own venv, not the project's; serving/serve_decision.sh starts it on GPU 1."""
import argparse, json, re, subprocess, sys, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def child(package, port):
    """Load d3 (package check kept, except for the README the Hub edited after the manifest) and serve it."""
    sys.path.insert(0, package)
    import d3_server
    from d3_runtime import D3
    try:
        model = D3.from_pretrained(package, device="cuda:0", verify="fast")
    except ValueError as e:
        if not re.fullmatch(r"package verification failed: README\.md: [^;]*", str(e)):
            raise
        model = D3.from_pretrained(package, device="cuda:0", verify="none")
    model.warmup()
    d3_server.serve(model, "127.0.0.1", port).serve_forever()


class Supervisor:
    def __init__(self, a):
        self.a, self.proc, self.last, self.busy, self.lock = a, None, time.time(), 0, threading.Lock()
        self.inner = f"http://127.0.0.1:{a.port + 10000}"
        threading.Thread(target=self.watch, daemon=True).start()

    def awake(self):  # under self.lock: start the child if needed and wait until it answers
        if self.proc is None or self.proc.poll() is not None:
            print(time.strftime("%H:%M:%S"), "starting d3", flush=True)
            self.proc = subprocess.Popen([sys.executable, "-I", __file__, "--child", "--package", self.a.package,
                                          "--port", str(self.a.port + 10000)])
            while True:
                try:
                    urllib.request.urlopen(self.inner + "/health", timeout=5).read()
                    return
                except OSError:
                    if self.proc.poll() is not None:
                        raise RuntimeError("d3 failed to start")
                    time.sleep(2)

    def forward(self, method, path, body, headers, touch=True):  # health probes do not count as activity
        with self.lock:
            self.awake()
            self.busy += 1
        try:
            req = urllib.request.Request(self.inner + path, data=body, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()
        finally:
            with self.lock:
                self.busy -= 1
                if touch:
                    self.last = time.time()

    def watch(self):
        while True:
            time.sleep(30)
            with self.lock:
                idle = self.a.idle_minutes > 0 and time.time() - self.last > self.a.idle_minutes * 60  # 0 = never
                if idle and not self.busy and self.proc is not None and self.proc.poll() is None:
                    self.proc.terminate()
                    self.proc.wait()
                    print(time.strftime("%H:%M:%S"), f"idle {self.a.idle_minutes:g} min: d3 stopped, GPU released", flush=True)


def main():
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("--package", required=True, help="d3 snapshot directory (code + weights)")
    a.add_argument("--port", type=int, default=8002)
    a.add_argument("--idle-minutes", type=float, default=15)
    a.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    a = a.parse_args()
    if a.child:
        return child(a.package, a.port)
    sup = Supervisor(a)
    with sup.lock:  # load at start; the idle timer takes over from here
        sup.awake()

    class Handler(BaseHTTPRequestHandler):
        def relay(self, method):
            if self.path == "/health" and (sup.proc is None or sup.proc.poll() is not None):
                status, body = 200, json.dumps({"status": "sleeping"}).encode()
            else:
                n = int(self.headers.get("content-length") or 0)
                status, body = sup.forward(method, self.path, self.rfile.read(n) if n else None,
                                           {k: v for k, v in self.headers.items() if k.lower() in ("content-type", "authorization")},
                                           touch=self.path != "/health")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.relay("GET")

        def do_POST(self):
            self.relay("POST")

        def log_message(self, *args):
            pass

    print(f"d3 supervisor on http://127.0.0.1:{a.port}/v1/systemone (child sleeps after {a.idle_minutes:g} idle min)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
