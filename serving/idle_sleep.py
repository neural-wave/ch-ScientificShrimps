"""Sleep mode for an idle vLLM server (the GPUs are shared): poll /metrics and, after --minutes without requests,
put the engine to sleep (level 1: weights to CPU RAM, KV cache dropped). nl2q.translation.llm wakes it on the next call.
Needs VLLM_SERVER_DEV_MODE=1 and --enable-sleep-mode (serve_llm.sh does both and starts this watcher).
Standard library only.  python serving/idle_sleep.py http://localhost:8001 --minutes 15"""
import argparse, json, re, time, urllib.request


def get(url):
    return urllib.request.urlopen(url, timeout=10).read().decode()


def activity(base):  # finished + running requests: changes whenever anyone uses the server
    return sum(map(float, re.findall(r"^vllm:(?:request_success_total|num_requests_running)\{[^}]*\} (\S+)", get(base + "/metrics"), re.M)))


def main():
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("url")
    a.add_argument("--minutes", type=float, default=15)
    a = a.parse_args()
    base, seen, last, misses = a.url.rstrip("/"), None, time.time(), 0
    while misses < 40:  # server gone for 20 minutes: stop watching
        time.sleep(30)
        try:
            now, misses = activity(base), 0
        except OSError:
            misses += 1
            continue
        if now != seen:
            seen, last = now, time.time()
        elif time.time() - last > a.minutes * 60 and not json.loads(get(base + "/is_sleeping"))["is_sleeping"]:
            urllib.request.urlopen(urllib.request.Request(base + "/sleep?level=1", method="POST"), timeout=300).read()
            print(time.strftime("%H:%M:%S"), f"idle {a.minutes:g} min: engine asleep", flush=True)


if __name__ == "__main__":
    main()
