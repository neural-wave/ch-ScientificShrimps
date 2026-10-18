"""What the generator's server did during a run: prefix-cache hit rate and prefill time from vLLM's /metrics, read before
and after a command.
  python -m nl2q.tools.llm_metrics -- python -m nl2q.tools.evaluate     # the 26 bench requests through the pipeline
The counters are server-wide: another client of the same server during the run is counted too."""
import re, subprocess, sys, time
from urllib.request import urlopen
from .. import settings as S

COUNTERS = ("prefix_cache_hits_total", "prefix_cache_queries_total", "request_prefill_time_seconds_sum",
            "request_prefill_time_seconds_count", "time_to_first_token_seconds_sum", "time_to_first_token_seconds_count",
            "prompt_tokens_total", "generation_tokens_total")


def snapshot(url=None):
    root = re.sub(r"/v1/?$", "", url or S.LLM_URL)
    text = urlopen(root + "/metrics", timeout=10).read().decode()
    out = {}
    for name in COUNTERS:  # summed over label sets (engines, models)
        out[name] = sum(float(v) for v in re.findall(rf"^vllm:{name}(?:{{[^}}]*}})? ([0-9.e+-]+)$", text, re.M))
    return out


def report(a, b, seconds):
    d = {k: b[k] - a[k] for k in COUNTERS}
    n = d["request_prefill_time_seconds_count"] or 1
    lines = [f"requests          {d['request_prefill_time_seconds_count']:.0f} in {seconds:.0f} s",
             f"prefix cache      {d['prefix_cache_hits_total']:.0f}/{d['prefix_cache_queries_total']:.0f} prompt tokens "
             f"from the cache ({d['prefix_cache_hits_total'] / max(d['prefix_cache_queries_total'], 1):.1%})",
             f"prefill           {d['request_prefill_time_seconds_sum'] / n:.3f} s per request",
             f"first token       {d['time_to_first_token_seconds_sum'] / max(d['time_to_first_token_seconds_count'], 1):.3f} s per request",
             f"tokens            {d['prompt_tokens_total'] / n:.0f} prompt, {d['generation_tokens_total'] / n:.0f} generated per request"]
    return "\n".join(lines)


def main():
    cmd = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not cmd:
        sys.exit(__doc__)
    a, t0 = snapshot(), time.time()
    rc = subprocess.call(cmd)
    print(report(a, snapshot(), time.time() - t0))
    sys.exit(rc)


if __name__ == "__main__":
    main()
