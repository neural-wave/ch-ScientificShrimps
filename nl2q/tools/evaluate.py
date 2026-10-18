"""Score the system like the jury: verdict accuracy (both error directions), time per request and tokens; with
--virtuoso also the execution time of each compliant query (mean of 10 runs) and whether its rows match the reference.
  python -m nl2q.tools.evaluate --oracle                      # the checker alone, on the reference queries (no model)
  python -m nl2q.tools.evaluate                               # end to end through pipeline.translate (needs the LLM)
  python -m nl2q.tools.evaluate --url http://localhost:8080   # through the REST API, as the jury calls it
  NL2Q_PRESCREEN=1 python -m nl2q.tools.evaluate              # A/B: d3 pre-screen on the request (needs d3 on :8002)
Tests: every tests/bench/*.jsonl, lines of {id, question, compliant, sparql}. Traces: nl2q_data/eval_trace.jsonl."""
import argparse, json, statistics, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .. import settings as S
from ..compliance import judge

BENCH = sorted((Path(__file__).parents[2] / "tests" / "bench").glob("*.jsonl"))  # one file per owner + official


def oracle(t):
    reason = judge(t["sparql"])
    return {"sparql_query": t["sparql"], "is_compliant": reason is None, "compliance_reason": reason, "tokens_consumed": 0}


def via_api(url, t):
    import requests
    t0 = time.perf_counter()
    r = requests.post(url.rstrip("/") + "/nl-to-sparql", json={"text": t["question"]}, timeout=600).json()
    return {**r, "seconds": time.perf_counter() - t0}


def run_one(t, mode, url, virtuoso):
    if mode == "oracle":
        r = oracle(t)
    elif url:
        r = via_api(url, t)
    else:
        from ..pipeline import translate
        r = translate(t["question"])
    r = {"id": t["id"], "expected": t["compliant"], **r}
    if virtuoso and r.get("is_compliant") and (r.get("sparql_query") or "").strip() and t["compliant"] and t.get("sparql"):
        from ..store import run, timed
        try:
            got, ref = run(r["sparql_query"])[1], run(t["sparql"])[1]
            r["rows_match"] = sorted(map(sorted, map(lambda x: map(str, x), got))) == sorted(map(sorted, map(lambda x: map(str, x), ref)))
            r["exec_seconds"] = timed(r["sparql_query"])
        except Exception as e:
            r["exec_error"] = str(e)[:200]
    return r


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--oracle", action="store_true", help="judge the reference queries with the checker alone")
    a.add_argument("--url", help="call the REST API instead of the pipeline in-process")
    a.add_argument("--tests", nargs="+", default=BENCH, help="JSONL files (default: tests/bench/*.jsonl)")
    a.add_argument("--workers", type=int, default=1, help="parallel requests (1 keeps the timings honest)")
    a.add_argument("--virtuoso", action="store_true", help=f"run compliant queries on {S.SPARQL_ENDPOINT}")
    a.add_argument("--no-holdout", action="store_true", help="let a test question see its own example in the bank")
    a = a.parse_args()
    S.HOLDOUT = not a.no_holdout  # in-process only: a server behind --url keeps its own setting
    tests = [json.loads(l) for f in a.tests for l in open(f) if l.strip()]
    with ThreadPoolExecutor(a.workers) as ex:
        res = list(ex.map(lambda t: run_one(t, "oracle" if a.oracle else "llm", a.url, a.virtuoso), tests))
    S.DATA.mkdir(exist_ok=True)
    with open(S.DATA / "eval_trace.jsonl", "w") as f:
        for r in res:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    false_ok = [r["id"] for r in res if r["is_compliant"] and not r["expected"]]
    false_no = [r["id"] for r in res if not r["is_compliant"] and r["expected"]]
    print(f"verdicts        {len(res) - len(false_ok) - len(false_no)}/{len(res)} right")
    print(f"false compliant {len(false_ok)} {false_ok}\nfalse refusal   {len(false_no)} {false_no}")
    if not a.oracle:
        secs, toks = [r.get("seconds", 0) for r in res], [r.get("tokens_consumed", 0) for r in res]
        print(f"seconds         mean {statistics.mean(secs):.2f}, max {max(secs):.2f}\ntokens          mean {statistics.mean(toks):.0f}, max {max(toks)}")
        tries = [len(r.get("attempts", [])) + 1 for r in res]
        print(f"attempts        mean {statistics.mean(tries):.2f}, max {max(tries)}")
        if blocked := [r["id"] for r in res if r.get("checker") == "d3"]:
            print(f"d3 blocked      {len(blocked)} {blocked}")
    if a.virtuoso:
        ex_t = [r["exec_seconds"] for r in res if "exec_seconds" in r]
        print(f"rows match      {sum(r.get('rows_match', False) for r in res)}/{sum('rows_match' in r for r in res)}"
              + (f"\nexec seconds    mean {statistics.mean(ex_t):.3f}" if ex_t else "")
              + f"\nexec errors     {[r['id'] for r in res if 'exec_error' in r]}")


if __name__ == "__main__":
    main()
