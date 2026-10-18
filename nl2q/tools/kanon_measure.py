"""Measure k-anonymity (compliance/kanon.py) on Virtuoso, outside pytest: needs SPARQL_ENDPOINT, no model.
For every compliant labelled query (tests/bench/*.jsonl and tests/compliance/mutations.jsonl): the patient variable at
the level that aggregates (or `unsupported`), the size in distinct patients of each group the query releases, and
whether k = 10 and k = 5 would hit it. For the compliant bench queries also the execution time of the original against
the rewrite (HAVING ... >= k), interleaved runs, mean. Prints counts, timings and error messages only, never rows.
  python -m nl2q.tools.kanon_measure --out DIR [--runs 3] [--timeout 300] [--k 10]"""
import argparse, json, statistics, time
from pathlib import Path
import requests
from .. import settings as S
from ..compliance.kanon import group_sizes, patient, rewrite

ROOT = Path(__file__).parents[2] / "tests"
FILES = sorted((ROOT / "bench").glob("*.jsonl")) + [ROOT / "compliance" / "mutations.jsonl"]


def run(query, timeout=300):
    """(columns, rows) like store.run, but a Virtuoso error keeps its message."""
    r = requests.post(S.SPARQL_ENDPOINT, data={"query": query}, headers={"Accept": "application/sparql-results+json"}, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {' '.join(r.text.split())[:240]}")
    j = r.json()
    cols = j["head"]["vars"]
    return cols, [tuple(b[c]["value"] if c in b else None for c in cols) for b in j["results"]["bindings"]]


def timed(query, timeout):
    t0 = time.perf_counter()
    try:
        rows = len(run(query, timeout)[1])
        return time.perf_counter() - t0, rows, None
    except Exception as e:
        return time.perf_counter() - t0, None, f"{type(e).__name__}: {str(e)[:240]}"


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--out", required=True, help="folder for sizes.jsonl and timing.jsonl")
    a.add_argument("--runs", type=int, default=3)
    a.add_argument("--timeout", type=float, default=300)
    a.add_argument("--k", type=int, default=S.K)
    a.add_argument("--only", nargs="*", help="ids to measure (default: all)")
    a.add_argument("--skip-timing", action="store_true")
    a = a.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tests = [dict(json.loads(l), file=str(f.relative_to(ROOT.parent))) for f in FILES for l in open(f) if l.strip()]
    tests = [t for t in tests if t.get("compliant") and t.get("sparql") and (not a.only or t["id"] in a.only)]
    print(f"{len(tests)} compliant labelled queries, endpoint {S.SPARQL_ENDPOINT}, k = {a.k} and 5", flush=True)

    with open(out / "sizes.jsonl", "w") as f:
        for t in tests:
            pv, why = patient(t["sparql"])
            r = {"id": t["id"], "file": t["file"], "patient": f"?{pv}" if pv else None, "unsupported": why}
            if pv:
                t0 = time.perf_counter()
                try:
                    sizes = group_sizes(t["sparql"], run, a.timeout)
                    r |= {"groups": len(sizes), "min": min(sizes, default=None), "zero": sum(n == 0 for n in sizes),
                          f"under_{a.k}": sum(0 < n < a.k for n in sizes), "under_5": sum(0 < n < 5 for n in sizes),
                          "sizes": sorted(sizes)[:20], "max": max(sizes, default=None)}
                except Exception as e:
                    r["error"] = f"{type(e).__name__}: {str(e)[:240]}"
                r["seconds"] = round(time.perf_counter() - t0, 3)
            f.write(json.dumps(r) + "\n")
            f.flush()
            print(json.dumps({k: v for k, v in r.items() if k not in ("sizes", "file")}), flush=True)

    if a.skip_timing:
        return
    bench = [t for t in tests if t["file"].startswith("tests/bench/")]
    with open(out / "timing.jsonl", "w") as f:
        for t in bench:
            new, why = rewrite(t["sparql"], a.k)
            r = {"id": t["id"], "rewrite": why or "ok", "original": [], "rewritten": []}
            for _ in range(a.runs):  # interleaved, so a warm cache helps both alike
                r["original"].append(timed(t["sparql"], a.timeout))
                if not why:
                    r["rewritten"].append(timed(new, a.timeout))
            for side in ("original", "rewritten"):
                ok = [s for s, _, e in r[side] if e is None]
                r[f"{side}_mean"] = round(statistics.mean(ok), 3) if ok else None
                r[f"{side}_rows"] = next((n for _, n, e in r[side] if e is None), None)
                r[f"{side}_errors"] = sorted({e for _, _, e in r[side] if e})
                r[side] = [round(s, 3) for s, _, _ in r[side]]
            f.write(json.dumps(r) + "\n")
            f.flush()
            print(json.dumps({k: r[k] for k in ("id", "rewrite", "original_mean", "rewritten_mean", "original_rows",
                                                 "rewritten_rows", "original_errors", "rewritten_errors")}), flush=True)


if __name__ == "__main__":
    main()
