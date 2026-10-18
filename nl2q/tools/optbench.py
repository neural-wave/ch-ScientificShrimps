"""Measure the optimizer (nl2q/optimize) on the 100k-patient Virtuoso: does a pass change a query, are the rows the
same, and how much faster does Virtuoso run it.
  python -m nl2q.tools.optbench stats                    # nl2q/optimize/cardinality.json from the full data scan
  python -m nl2q.tools.optbench corpus                   # nl2q_data/optbench/corpus.jsonl from the four sources
  python -m nl2q.tools.optbench profile [--quick]        # time the corpus as it is (pragma as finish() emits it)
  python -m nl2q.tools.optbench compare --passes reorder # original against optimized, on the queries the passes change
  python -m nl2q.tools.optbench pragma                   # optimized query with against without the "order" pragma
  python -m nl2q.tools.optbench report                   # full: each pass alone, all passes; writes the report
Only read-only SELECTs, one at a time, under a lock, after an idle check, with Virtuoso's server-side timeout. Rows are
compared by hash and never printed. Timings are cached by query text and endpoint (nl2q_data/optbench/timings.jsonl)."""
import argparse, fcntl, glob, hashlib, json, math, re, statistics, subprocess, sys, time
from datetime import datetime
from pathlib import Path
from .. import settings as S
from ..compliance import judge
from ..optimize import PASSES, optimize
from ..optimize.sparql import Unsupported, groups, read

ROOT = Path(__file__).parents[2]
STATS = Path("/data/scratch/nw-data/stats")
SHACL = Path("/data/scratch/lorenzo.varese@supsi.ch/sphn_dl/shacl_2026-1.ttl")  # untrusted copy of the 2026.1 shapes
NIGHT = "/data/scratch/lorenzo.varese@supsi.ch/night/results"
PAPER = ROOT / "docs" / "official" / "sphn-sparql-optimization"
LOCK = "/data/scratch/lorenzo.varese@supsi.ch/virtuoso-8990.lock"
OUT = S.DATA / "optbench"
REPORT = ROOT / "docs" / "report" / "sparql-optimization.md"
JURY, SMALL = "http://127.0.0.1:8990/sparql", "http://127.0.0.1:8890/sparql"
CONTAINER = {JURY: "nl2q-jury-virtuoso", SMALL: "hackathon-virtuoso"}
PRAGMA = 'DEFINE sql:select-option "order"'
BEGIN, END = "<!-- optbench report: generated, do not edit -->", "<!-- end of optbench report -->"
NOISE = 0.010  # seconds: pairs where both sides are faster than this are noise


# ---------------------------------------------------------------- stats and corpus
def build_stats():
    """cardinality.json: instances per class and code, triples per predicate and (predicate, individual)."""
    pre = dict(re.findall(r"@prefix ([\w-]*): <([^>]*)>", (STATS / "prefixes.ttl").read_text()))
    full = lambda c: c[1:-1] if c.startswith("<") else pre[c.split(":", 1)[0]] + c.split(":", 1)[1]
    rows = lambda f: [l.rstrip("\n").split("\t") for l in list(open(STATS / f))[1:] if l.strip()]
    d = {"source": "nw-data/stats, all 100,000 mock patients (built 2026-10-16)",
         "patients": 100000, "triples": int(re.search(r"triples: (\d+)", (STATS / "overview.txt").read_text())[1]),
         "classes": {full(r[0]): [int(r[1]), int(r[2])] for r in rows("classes.tsv")},  # [instances, patients]
         "predicates": {full(r[0]): [int(r[1]), int(r[2])] for r in rows("predicates.tsv")},  # [triples, patients]
         "codes": {full(r[2]): [int(r[3]), int(r[4])] for r in rows("codes.tsv")}, "individuals": {}}
    for r in rows("individuals.tsv"):
        k = f"{full(r[1])} {full(r[2])}"
        d["individuals"][k] = [a + int(b) for a, b in zip(d["individuals"].get(k, [0, 0]), r[3:5])]  # patients: upper bound
    # rows per bound subject (fan-out) and per bound object (fan-in): triples over the instances of the classes at that
    # end (edges.tsv); a literal, code or individual object counts as distinct per triple, per patient graph for individuals
    subj, obj = {}, {}
    d["edges"] = {}  # "class predicate": [triples, patients], every (subject class, predicate) pair in the data
    for s, p, o, n, pats, *_ in rows("edges.tsv"):
        if s.startswith("sphn:"):
            k = f"{full(s)} {full(p)}"
            d["edges"][k] = [a + int(b) for a, b in zip(d["edges"].get(k, [0, 0]), (n, pats))]
        p = full(p)
        subj.setdefault(p, set()).add(s)
        obj.setdefault(p, {})[o] = int(n)
    inst = lambda c: d["classes"].get(full(c), [None])[0] if c.startswith("sphn:") else None
    d["fanout"], d["fanin"] = {}, {}
    for p, (n, _) in d["predicates"].items():
        if p in subj:
            d["fanout"][p] = round(n / max(1, sum(inst(c) or 0 for c in subj[p])), 3)
            d["fanin"][p] = round(n / max(1, sum(inst(o) or (d["patients"] if o.startswith("sphn-individual:") else t)
                                                  for o, t in obj[p].items())), 3)
    # metadata v2: "class predicate": [data min, data max, SHACL min, SHACL max] (None: unknown or unbounded). The data's
    # come from the file scan (metadata_scan: min 1 when every instance has it), SHACL's from the 2026.1 shapes
    d["card"], scanned = {}, (STATS / "subject_predicates.tsv").exists()
    if scanned:
        for c, p, n, most in rows("subject_predicates.tsv"):
            d["card"][f"{full(c)} {full(p)}"] = [int(int(n) == d["classes"].get(full(c), [0])[0]), int(most), None, None]
        d["class_max"] = {full(c): int(n) for c, n in rows("class_max.tsv")}  # most instances in one patient
    for (c, p), (lo, hi) in shacl().items():
        absent = [0, 0] if scanned and c in d["classes"] else [None, None]  # a class of the data without the property
        d["card"].setdefault(f"{c} {p}", absent + [None, None])[2:] = [lo, hi]
    d["disagree"] = {k: "max" if hi is not None and dhi is not None and dhi > hi else "min"
                     for k, (dlo, dhi, lo, hi) in d["card"].items()
                     if (hi is not None and dhi is not None and dhi > hi) or (lo and dlo is not None and dlo < lo)}
    S.OPT_SHACL.write_text(json.dumps({"source": f"SHACL 2026.1 ({SHACL.name})", "card": {f"{c} {p}": v for (c, p), v in
                                                                                        sorted(shacl().items())}},
                                      separators=(",", ":")) + "\n")  # the static optimizer's only input
    S.OPT_STATS.write_text(json.dumps(d, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"{S.OPT_STATS}: {len(d['classes'])} classes, {len(d['predicates'])} predicates, {len(d['codes'])} codes, "
          f"{len(d['card'])} cardinalities ({'data and ' if scanned else ''}SHACL), {len(d['disagree'])} disagreements, "
          f"{S.OPT_STATS.stat().st_size / 1e3:.0f} KB")


def shacl():
    """(class IRI, property IRI) -> (sh:minCount, sh:maxCount) of SHACL 2026.1, the tightest when several shapes say it.
    The file is an untrusted download: build the stats under python -I."""
    from rdflib import Graph, URIRef
    out = {}
    for c, p, lo, hi in Graph().parse(SHACL).query("""PREFIX sh: <http://www.w3.org/ns/shacl#>
            SELECT ?c ?p ?lo ?hi WHERE { ?s sh:targetClass ?c ; sh:property ?ps . ?ps sh:path ?p .
            OPTIONAL { ?ps sh:minCount ?lo } OPTIONAL { ?ps sh:maxCount ?hi } }"""):
        if isinstance(p, URIRef):
            a, b = out.get((str(c), str(p)), (None, None))
            lo, hi = (None if v is None else int(v) for v in (lo, hi))
            out[(str(c), str(p))] = (max((x for x in (a, lo) if x is not None), default=None),
                                     min((x for x in (b, hi) if x is not None), default=None))
    return out


def build_corpus():
    from ..translation.examples import load
    from ..translation.prompt import finish
    items = []
    for f in sorted(glob.glob(f"{NIGHT}/**/*trace*.jsonl", recursive=True)):
        items += [("generated", r["id"], r["sparql_query"]) for r in map(json.loads, filter(str.strip, open(f)))
                  if r.get("is_compliant") and r.get("sparql_query")]
    for f in sorted((ROOT / "tests" / "bench").glob("*.jsonl")):
        items += [("reference", t["id"], t["sparql"] if "sql:select-option" in t["sparql"] else f"{PRAGMA}\n{t['sparql']}")
                  for t in map(json.loads, filter(str.strip, open(f))) if t["compliant"] and t.get("sparql")]
    items += [("example", e["id"], finish(e["sparql"])) for e in load() if e["compliant"] and e["sparql"]]
    items += [("paper", f.stem, f.read_text()) for f in sorted(PAPER.rglob("*.rq"))]  # as the paper wrote them
    out, seen, n = [], set(), {}
    for source, id_, q in items:
        key = " ".join(q.split())
        if key in seen or judge(q) is not None:
            continue
        seen.add(key)
        n[id_] = n.get(id_, 0) + 1
        out.append({"key": f"{source[:3]}:{id_}" + (f"#{n[id_]}" if source == "generated" else ""), "source": source,
                    "id": id_, "sparql": q})
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "corpus.jsonl", "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in out)
    by = {s: sum(r["source"] == s for r in out) for s in ("generated", "reference", "example", "paper")}
    print(f"corpus: {len(out)} queries {by} (of {len(items)} read; compliant, parsed, deduplicated)")


def corpus():
    return [json.loads(l) for l in open(OUT / "corpus.jsonl")]


# ---------------------------------------------------------------- Virtuoso
def norm(b):
    """A binding as compared: floats rounded to 9 significant digits."""
    v = b.get("value")
    if b.get("datatype", "").endswith(("#double", "#float", "#decimal")):
        try:
            v = format(float(v), ".9g")
        except ValueError:
            pass
    return f"{b.get('type')}|{v}"


def execute(q, endpoint, cap):
    """{s, rows, hash, partial, error}: one run with the server-side timeout `cap` seconds. Rows only as a hash."""
    import requests
    t0 = time.perf_counter()
    try:
        r = requests.post(endpoint, data={"query": q, "timeout": int(cap * 1000)},
                          headers={"Accept": "application/sparql-results+json"}, timeout=cap + 60)
    except requests.RequestException as e:
        return {"s": time.perf_counter() - t0, "error": f"client: {type(e).__name__}"}
    s = time.perf_counter() - t0
    if r.status_code not in (200, 206):  # Virtuoso's message only (its code and text), never the body
        m = re.search(r"(\w+ Error [A-Z0-9]+: [^\n]*?)(?:SPARQL query:|\n|$)", r.text)
        return {"s": s, "error": f"HTTP {r.status_code}: {m[1].strip()[:80] if m else ''}"}
    j = r.json()
    cols = j["head"]["vars"]
    rows = sorted(hashlib.sha256("\x1f".join(norm(b.get(c, {})) for c in cols).encode()).hexdigest()
                  for b in j["results"]["bindings"])
    return {"s": s, "rows": len(rows), "hash": hashlib.sha256("".join(rows).encode()).hexdigest()[:16],
            "partial": r.status_code == 206 or r.headers.get("X-SQL-State") == "S1TAT"}  # 206: the anytime timeout


def cpu(endpoint):
    out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", CONTAINER[endpoint]],
                         capture_output=True, text=True).stdout.strip().rstrip("%")
    return float(out or 0)


def wait_idle(endpoint, limit=5.0, patience=900):
    t0 = time.time()
    while (c := cpu(endpoint)) >= limit:
        if time.time() - t0 > patience:
            raise TimeoutError(f"{CONTAINER[endpoint]} still busy ({c:.0f}% CPU) after {patience} s")
        print(f"  waiting: {CONTAINER[endpoint]} at {c:.0f}% CPU", flush=True)
        time.sleep(15)
    return c


class Bench:
    """Timed runs with a cache; pairs interleaved (A B A B), the side that goes first alternating across pairs."""

    def __init__(self, endpoint, cap, warm):
        self.endpoint, self.cap, self.warm, self.flip, self.since_check = endpoint, cap, warm, False, 0
        OUT.mkdir(parents=True, exist_ok=True)
        self.path = OUT / "timings.jsonl"
        self.cache = {}
        if self.path.exists():
            for l in open(self.path):
                r = json.loads(l)
                self.cache[r["key"]] = r
        self.lock = open(LOCK, "a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"waiting for the lock {LOCK}", flush=True)
            fcntl.flock(self.lock, fcntl.LOCK_EX)
        self.idle = wait_idle(endpoint)

    def key(self, q):
        return hashlib.sha256(f"{self.endpoint}\n{q}".encode()).hexdigest()

    def done(self, r):
        if r is None or (r.get("partial") or "error" in r) and r["cap"] < self.cap:
            return False
        return r.get("partial") or "error" in r or r["first"] > 30 or len(r["warm"]) >= self.warm

    def run(self, q):
        r = execute(q, self.endpoint, self.cap)
        self.since_check += 1
        if r.get("partial") or "error" in r or r["s"] > 30 or self.since_check >= 25:
            wait_idle(self.endpoint)  # a capped query may still be winding down
            self.since_check = 0
        return r

    def time(self, *qs):
        """Records for the query texts, timing only those not cached."""
        todo = [q for q in dict.fromkeys(qs) if not self.done(self.cache.get(self.key(q)))]
        if self.flip:
            todo.reverse()
        self.flip = not self.flip
        for q in todo:
            r = self.run(q)
            self.cache[self.key(q)] = {"key": self.key(q), "endpoint": self.endpoint, "cap": self.cap, "first": r["s"],
                                       "warm": [], **{k: r[k] for k in ("rows", "hash", "partial", "error") if k in r},
                                       "at": datetime.now().isoformat(timespec="seconds")}
        for _ in range(self.warm):
            for q in todo:
                c = self.cache[self.key(q)]
                if not self.done(c):
                    r = self.run(q)
                    c["warm"].append(r["s"])
                    if r.get("hash") != c.get("hash"):
                        c["unstable"] = True  # rows changed between runs
        with open(self.path, "a") as f:
            for q in todo:
                f.write(json.dumps(self.cache[self.key(q)]) + "\n")
        return [self.cache[self.key(q)] for q in qs]


def mean(r):
    return statistics.mean(r["warm"]) if r["warm"] else r["first"]


def capped(r):
    return bool(r.get("partial") or "error" in r)


def same(a, b):
    """The same rows, or the same database error on both sides."""
    if "error" in a or "error" in b:
        return a.get("error", "").split(":")[:2] == b.get("error", "").split(":")[:2] and a["error"].startswith("HTTP 500")
    return not capped(a) and not capped(b) and (a.get("rows"), a.get("hash")) == (b.get("rows"), b.get("hash"))


# ---------------------------------------------------------------- query shapes
def flags(q):
    """Shapes the paper's rules warn about."""
    from ..optimize import Context
    from ..optimize import stats as ST
    out = []
    if re.search(r"\bOPTIONAL\b", q, re.I):
        out.append("optional")
    try:
        root, comments = read(q)
        ctx = Context(q, root, (), comments)
        triples = [(t, g) for g in groups(root) for e in g.elements for t in e.triples]
        code = lambda t: ctx.iri(t.p).endswith("#type") and t.o.kind in ("iri", "pname") and ST.family(ctx.iri(t.o))
        if any(t.p.kind == "path" for t, _ in triples):
            out.append("path")
        if not any(code(t) for t, _ in triples if t.p.kind != "var"):
            out.append("no-code")
        for g in groups(root):
            run = [t for e in g.elements if e.kind == "triples" for t in e.triples]
            if run and any(code(t) for t in run[1:] if t.p.kind != "var") and not code(run[0]):
                out.append("code-not-first")
                break
    except (Unsupported, StopIteration):
        out.append("unread")
    if re.search(r"\bDISTINCT\b", q, re.I) and "no-code" in out:
        out.append("distinct-unanchored")
    return out


# ---------------------------------------------------------------- commands
def fmt(s):
    return f"{s:.3f}" if s < 10 else f"{s:.1f}"


def profile(a):
    b, cs = Bench(a.endpoint, a.cap, a.warm), corpus()
    print(f"profile: {len(cs)} queries on {a.endpoint}, cap {a.cap:.0f} s, warm {a.warm}, idle {b.idle:.1f}% CPU", flush=True)
    res = []
    for k, c in enumerate(cs, 1):
        r = b.time(c["sparql"])[0]
        res.append((c, r))
        if r["first"] > 5 or capped(r):
            print(f"  [{k}/{len(cs)}] {c['key']}: {fmt(mean(r))} s" + (" CAPPED" if capped(r) else ""), flush=True)
    print(f"{'source':10} {'n':>4} {'median':>8} {'mean':>8} {'p90':>8} {'max':>8} {'>1s':>4} {'capped':>6}")
    for s in ("generated", "reference", "example", "paper"):
        ts = sorted(mean(r) for c, r in res if c["source"] == s)
        if ts:
            print(f"{s:10} {len(ts):4} {fmt(statistics.median(ts)):>8} {fmt(statistics.mean(ts)):>8} "
                  f"{fmt(ts[int(0.9 * (len(ts) - 1))]):>8} {fmt(ts[-1]):>8} {sum(t > 1 for t in ts):4} "
                  f"{sum(capped(r) for c, r in res if c['source'] == s):6}")
    print("slowest 15:")
    for c, r in sorted(res, key=lambda x: -mean(x[1]))[:15]:
        ch = optimize(c["sparql"], PASSES)[1]
        print(f"  {c['key']:16} {fmt(mean(r)):>8} s{' CAPPED' if capped(r) else ''}  {','.join(flags(c['sparql']))}"
              f"  passes: {','.join(ch) or '-'}")


def compare(a, passes, b=None, quiet=False):
    """Original against optimized on the queries the passes change. Returns per-query results."""
    b, out = b or Bench(a.endpoint, a.cap, a.warm), []
    cs = corpus()
    changed = [(c, *optimize(c["sparql"], passes)) for c in cs]
    changed = [(c, q, p) for c, q, p in changed if q != c["sparql"]]
    if not quiet:
        print(f"compare {'+'.join(passes)}: {len(changed)}/{len(cs)} changed, cap {a.cap:.0f} s, warm {a.warm}", flush=True)
    for c, q, p in changed:
        ra, rb = b.time(c["sparql"], q)
        eq, where = same(ra, rb), "100k"
        if capped(ra) and not capped(rb) and a.endpoint == JURY:  # check equality on the 5k store
            small = Bench.__new__(Bench)
            small.__dict__.update(b.__dict__, endpoint=SMALL, cap=a.cap)
            try:
                sa, sb = small.time(c["sparql"], q)
                eq, where = same(sa, sb), "5k"
                if capped(sa):  # the original times out on 5k too: its partial rows prove nothing (ref:O06)
                    where = "5k, original capped there too: unchecked"
            except TimeoutError:
                where = "5k busy, unchecked"
        out.append({"key": c["key"], "source": c["source"], "id": c["id"], "passes": p, "before": mean(ra),
                    "after": mean(rb), "capped_before": capped(ra), "capped_after": capped(rb), "same": eq, "where": where,
                    "expected": not eq and "flag" in p})  # flag fixes a double count: its row changes are intended
        if not quiet:
            x = out[-1]
            print(f"  {x['key']:16} {fmt(x['before']):>8} -> {fmt(x['after']):>8} s  "
                  f"{'same' if eq else 'changed: double count fixed (flag)' if out[-1]['expected'] else 'DIFFERENT' if not (capped(ra) and capped(rb)) else 'both capped'} ({where})"
                  + (" capped-before" if x["capped_before"] else ""), flush=True)
    return out


def summary(res, n_corpus):
    lines = [f"changed {len(res)}/{n_corpus}; identical rows {sum(r['same'] for r in res)}/{len(res)}; "
             f"double count fixed by flag (expected) {[r['key'] for r in res if r.get('expected')]}; "
             f"failures (different rows or newly capped) "
             f"{[r['key'] for r in res if not r['same'] and not r.get('expected') and 'unchecked' not in r['where'] and not (r['capped_before'] and r['capped_after'])]}", f"unchecked {[r['key'] for r in res if 'unchecked' in r['where']]}"]
    for s in ("generated", "reference", "example", "paper", None):
        rs = [r for r in res if s is None or r["source"] == s]
        if not rs:
            continue
        sig = [r for r in rs if max(r["before"], r["after"]) >= NOISE]
        sp = [r["before"] / max(r["after"], 1e-4) for r in sig]
        gm = math.exp(statistics.mean(map(math.log, sp))) if sp else 1
        lines.append(f"{s or 'all':10} n={len(rs):3} before {sum(r['before'] for r in rs):8.2f} s  after "
                     f"{sum(r['after'] for r in rs):8.2f} s  geomean x{gm:.2f}  median x{statistics.median(sp) if sp else 1:.2f}"
                     f"  best x{max(sp, default=1):.1f}  improved {sum(x > 1.2 for x in sp)}  regressed {sum(x < 1 / 1.2 for x in sp)}"
                     f"  capped {sum(r['capped_before'] for r in rs)}->{sum(r['capped_after'] for r in rs)}")
    for r in res:
        if r["capped_before"] or r["capped_after"]:
            lines.append(f"timeout   {r['key']}: {fmt(r['before'])}{' (cap)' if r['capped_before'] else ''} -> "
                         f"{fmt(r['after'])}{' (cap)' if r['capped_after'] else ''} s"
                         + (f", >x{r['before'] / max(r['after'], 1e-3):.0f}" if r["capped_before"] and not r["capped_after"] else ""))
    return lines


def references():
    """The compliant bench references, as the corpus writes them (with the pragma)."""
    return [t["sparql"] if "sql:select-option" in t["sparql"] else f"{PRAGMA}\n{t['sparql']}"
            for f in sorted((ROOT / "tests" / "bench").glob("*.jsonl"))
            for t in map(json.loads, filter(str.strip, open(f))) if t["compliant"] and t.get("sparql")]


def overhead(passes):
    ts = []
    for c in corpus():
        for _ in range(20):
            t0 = time.perf_counter()
            optimize(c["sparql"], passes)
            ts.append((time.perf_counter() - t0) * 1000)
    ts.sort()
    return ts[len(ts) // 2], ts[int(len(ts) * 0.99)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["stats", "corpus", "profile", "compare", "pragma", "report", "calibrate", "slowpath"])
    ap.add_argument("--timings", help="calibrate: a timings file (default nl2q_data/optbench/timings.jsonl)")
    ap.add_argument("--endpoint", default=JURY)
    ap.add_argument("--quick", action="store_true", help="first run only, cap 60 s")
    ap.add_argument("--cap", type=float, help="server-side timeout in seconds (default 120, quick 60)")
    ap.add_argument("--warm", type=int, help="timed runs after the first (default 4, quick 0)")
    ap.add_argument("--passes", default=",".join(PASSES))
    ap.add_argument("--stats", choices=["schema", "data"], help="the optimizer's knowledge (NL2Q_OPT_STATS)")
    a = ap.parse_args()
    a.cap = a.cap or (60 if a.quick else 120)
    S.OPT_SOURCE = a.stats or S.OPT_SOURCE
    a.warm = a.warm if a.warm is not None else (0 if a.quick else 4)
    if a.command == "stats":
        return build_stats()
    if a.command == "corpus":
        return build_corpus()
    if a.command == "profile":
        return profile(a)
    passes = tuple(p for p in a.passes.split(",") if p)
    if a.command == "compare":
        res = compare(a, passes)
        print("\n".join(summary(res, len(corpus()))))
        p50, p99 = overhead(passes)
        print(f"optimizer overhead p50 {p50:.2f} ms, p99 {p99:.2f} ms")
    elif a.command == "pragma":
        pragma(a, passes)
    elif a.command == "report":
        report(a)
    elif a.command == "calibrate":
        calibrate(a)
    elif a.command == "slowpath":
        slowpath_run(a)


def calibrate(a):
    """C1: the cost model (optimize/cost.py) against the cached timings: Spearman rho, and the threshold T where
    "cost > T" finds the slow queries (> 10 s or capped) with recall >= 0.8 at the best precision."""
    from ..optimize.cost import cost
    path = Path(a.timings) if a.timings else OUT / "timings.jsonl"
    recs = {}
    for l in open(path):
        r = json.loads(l)
        recs[r["key"]] = r
    key = lambda q: hashlib.sha256(f"{a.endpoint}\n{q}".encode()).hexdigest()
    texts = {}
    for c in corpus():  # every text optbench times: originals, each pass and all passes, with and without the pragma
        for q in [c["sparql"]] + [optimize(c["sparql"], p)[0] for p in [PASSES, ("paths", "reorder")] + [(p,) for p in PASSES]]:
            for v in (q, strip_pragma(q)):
                texts[key(v)] = v
    pts = []
    for k, r in recs.items():
        if k in texts and "error" not in r and (x := cost(texts[k])) is not None:
            pts.append((x, mean(r) if not capped(r) else r["cap"], capped(r) or mean(r) > 10))
    rank = lambda xs: {v: i for i, v in enumerate(sorted(set(xs)))}
    rx, ry = rank([p[0] for p in pts]), rank([p[1] for p in pts])
    n = len(pts)
    dx = [rx[p[0]] for p in pts]
    dy = [ry[p[1]] for p in pts]
    mx, my = statistics.mean(dx), statistics.mean(dy)
    rho = sum((x - mx) * (y - my) for x, y in zip(dx, dy)) / math.sqrt(sum((x - mx) ** 2 for x in dx) * sum((y - my) ** 2 for y in dy))
    slow = sum(p[2] for p in pts)
    best = None
    for t in sorted({p[0] for p in pts}):
        hit = [p for p in pts if p[0] > t]
        tp = sum(p[2] for p in hit)
        rec, prec = tp / max(slow, 1), tp / max(len(hit), 1)
        if rec >= 0.8 and (best is None or prec > best[2]):
            best = (t, rec, prec, len(hit))
    print(f"calibration on {path.name}: {n} timed texts ({len(recs)} records), {slow} slow (> 10 s or capped)")
    print(f"Spearman rho {rho:.2f}")
    if best:
        t, rec, prec, k = best
        print(f"T = {t:.3g} rows: recall {rec:.2f}, precision {prec:.2f} ({k} predicted slow); "
              f"gate (recall >= 0.8 at precision >= 0.5) {'PASSED' if prec >= 0.5 else 'FAILED'}")
    return pts


def slowpath_run(a):
    """D2 offline: the slow path on every generated corpus query (after the passes), with its bench question and the
    real generator; writes nl2q_data/optbench/slowpath.jsonl for timing (compare the pairs with `slowpath-time`)."""
    from ..compliance import judge
    from ..optimize.cost import cost
    from ..optimize.slowpath import slowpath
    S.SLOWPATH, S.HOLDOUT = True, True
    questions = {t["id"]: t["question"] for f in sorted((ROOT / "tests" / "bench").glob("*.jsonl"))
                 for t in map(json.loads, filter(str.strip, open(f)))}
    out = []
    for c in corpus():
        if c["source"] != "generated" or c["id"] not in questions:
            continue
        q, passes = optimize(c["sparql"], PASSES)
        usage, t0 = {}, time.perf_counter()
        new, after = slowpath(questions[c["id"]], q, judge(q), passes, None, usage)
        if after[-1:] in (["slowpath"], ["slowpath-kept"]):
            out.append({"key": c["key"], "id": c["id"], "accepted": after[-1] == "slowpath", "cost_before": cost(q),
                        "cost_after": cost(new) if new != q else None, "tokens": sum(usage.values()),
                        "seconds": round(time.perf_counter() - t0, 2), "before": q, "after": new})
            print(f"  {c['key']:12} cost {cost(q):.3g} -> {'%.3g' % cost(new) if new != q else 'kept'}  "
                  f"{sum(usage.values())} tokens {out[-1]['seconds']} s", flush=True)
    n = sum(c["source"] == "generated" for c in corpus())
    print(f"slow path on {n} generated queries: triggered {len(out)}, accepted {sum(r['accepted'] for r in out)}; "
          f"mean {statistics.mean([r['seconds'] for r in out] or [0]):.1f} s and "
          f"{statistics.mean([r['tokens'] for r in out] or [0]):.0f} tokens per trigger")
    with open(OUT / "slowpath.jsonl", "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in out)


def strip_pragma(q):
    return re.sub(r'(?im)^\s*DEFINE\s+sql:select-option\s+"order"\s*\n?', "", q)


def pragma(a, passes, b=None):
    """With against without the pragma, on every corpus query after the passes."""
    b, cs, out = b or Bench(a.endpoint, a.cap, a.warm), corpus(), []
    for c in cs:
        q = optimize(c["sparql"], passes)[0] if passes else c["sparql"]
        if PRAGMA not in q:
            continue
        rw, ro = b.time(q, strip_pragma(q))
        out.append({"key": c["key"], "source": c["source"], "with": mean(rw), "without": mean(ro), "same": same(rw, ro),
                    "capped": [capped(rw), capped(ro)], "flags": flags(q)})
    sig = [r for r in out if max(r["with"], r["without"]) >= NOISE]
    slower = [r for r in sig if r["with"] > 1.2 * r["without"]]
    faster = [r for r in sig if r["without"] > 1.2 * r["with"]]
    print(f"pragma after {'+'.join(passes) or 'no pass'}: {len(out)} queries, {len(sig)} above noise; with the pragma "
          f"faster on {len(faster)}, slower on {len(slower)}; total {sum(r['with'] for r in out):.2f} s with, "
          f"{sum(r['without'] for r in out):.2f} s without; different rows {[r['key'] for r in out if not r['same'] and not any(r['capped'])]}")
    for r in sorted(slower, key=lambda r: r["without"] - r["with"])[:10]:
        print(f"  slower with: {r['key']:16} {fmt(r['with'])} vs {fmt(r['without'])} s  {','.join(r['flags'])}")
    return out


def report(a):
    """Full mode: each pass alone and all passes against the original; prints and writes docs/report/."""
    b, cs = Bench(a.endpoint, a.cap, a.warm), corpus()
    runs = {p: compare(a, (p,), b, quiet=True) for p in PASSES}
    runs["all"] = compare(a, PASSES, b, quiet=True)
    p50, p99 = overhead(PASSES)
    bench = references()
    before = [mean(b.time(q)[0]) for q in bench]
    after = [mean(b.time(optimize(q, PASSES)[0])[0]) for q in bench]
    lines = [BEGIN, f"{datetime.now():%Y-%m-%d %H:%M}, `{a.endpoint}` ({CONTAINER.get(a.endpoint)}, 100,000 patients, "
             f"611,719,746 triples), idle check {b.idle:.1f}% CPU before the run, server-side cap {a.cap:.0f} s, "
             f"first run + mean of {a.warm} warm runs, pairs interleaved. Corpus {len(cs)} queries "
             + str({s: sum(c['source'] == s for c in cs) for s in ('generated', 'reference', 'example', 'paper')}) + ".", ""]
    for name, res in runs.items():
        lines += [f"### Passes: {name}", "```", *summary(res, len(cs)), "```", ""]
        lines += [f"- {r['key']}: {fmt(r['before'])} -> {fmt(r['after'])} s" + (" (capped before)" if r["capped_before"] else "")
                  + ("" if r["same"] else " DIFFERENT ROWS") + (f" (rows checked at {r['where']})" if r["where"] != "100k" else "")
                  for r in sorted(res, key=lambda r: -r["before"]) if max(r["before"], r["after"]) >= NOISE]
        lines.append("")
    lines += ["### Jury proxy", f"Mean execution time over the {len(bench)} compliant bench references: "
              f"{statistics.mean(before):.3f} s before, {statistics.mean(after):.3f} s after.", "",
              "### Optimizer overhead", f"p50 {p50:.2f} ms, p99 {p99:.2f} ms per query (all passes, corpus x20).", END]
    text = REPORT.read_text() if REPORT.exists() else "# SPARQL optimizer: measured at 100k\n\n"
    head, _, rest = text.partition(BEGIN)
    REPORT.write_text(head + "\n".join(lines) + rest.partition(END)[2])  # only the generated block is replaced
    print("\n".join(lines))


if __name__ == "__main__":
    main()
