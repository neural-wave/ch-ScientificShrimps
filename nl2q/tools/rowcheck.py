"""Do the generated queries return what the reference queries return? Each distinct (id, query) of one or more traces
runs on S.SPARQL_ENDPOINT next to its reference (tests/bench/*.jsonl), one query at a time, with Virtuoso's own
timeout; the reference runs once per id. Rows are never printed or stored: only counts, column names, hashes and the
numbers of single-row aggregates of compliant requests.
  python -m nl2q.tools.rowcheck TRACE.jsonl [...] --out rows.jsonl   # lines of {id, sparql_query[, is_compliant, source]}
  python -m nl2q.tools.rowcheck TRACE.jsonl --ids O02 O04 --all      # --all: the non-compliant references too
Classes, first that holds: error (ours / ref / both, timeouts included), both empty, identical (evaluate.py's strict
comparison), empty on one side (no rows, or only 0, false and unbound), same up to format (numbers to 6 significant
digits, column order ignored), same numbers other shape (columns aligned by their values with extra columns ignored,
or the same multiset of numbers), subset / superset (rows, after the same alignment), different.
Also per query: the sphn: terms and the codes it names that the full 100k scan (NL2Q_STATS, default
/data/scratch/nw-data/stats) never saw: a 2026.1 term the 2024.2 data lacks, or a code absent from the data."""
import argparse, hashlib, json, re, sys, time
from collections import Counter
from pathlib import Path
import requests
from .. import settings as S
from .. import schema as SC

BENCH = sorted((Path(__file__).parents[2] / "tests" / "bench").glob("*.jsonl"))
STATS = Path(__import__("os").getenv("NL2Q_STATS", "/data/scratch/nw-data/stats"))
XSD = "http://www.w3.org/2001/XMLSchema#"
NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
BLANK = ("", "0", "false")

# ---- running: read-only SELECT only, Virtuoso's timeout ------------------------------------------------------------
_LEX = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|<[^<>"{}|^`\\\s]*>|#[^\n]*')
COMPUTE = {f"bif:{f}" for f in ("datediff", "dateadd", "year", "month", "dayofmonth", "now", "curdatetime", "round",
                                 "floor", "ceiling", "abs", "sqrt", "power", "mod", "lower", "upper", "length", "substring")}
WRITE = re.compile(r"(?i)\b(INSERT|DELETE|LOAD|CLEAR|DROP|CREATE|ADD|MOVE|COPY|WITH|SERVICE|CONSTRUCT|DESCRIBE|ASK)\b")


def runnable(q):
    """None when the query may run here (a SELECT, no update or federation, only the two steering DEFINEs, no bif:/sql:
    call), else why not."""
    text = _LEX.sub(lambda m: m.group(0) if m.group(0)[0] == "<" else ' "" ', q)
    if not re.search(r"(?i)\bSELECT\b", text):
        return "not a SELECT"
    if m := WRITE.search(re.sub(r"<[^>]*>|[?$]\w+|\w[\w-]*:[\w.-]*", " ", text)):
        return f"{m.group(1).upper()}, never run"
    if bad := [d for d in re.findall(r"(?i)\bDEFINE\s+([\w-]+:[\w-]+)", text) if d.lower() not in ("sql:select-option", "input:inference")]:
        return f"DEFINE {bad[0]}, never run"
    if bad := [f for f in re.findall(r"(?i)\b((?:bif|sql):\w+)\s*\(", text) if f.lower() not in COMPUTE]:
        return f"{bad[0]} call, never run"
    return None


def execute(q, timeout):
    """(seconds, columns, rows of JSON bindings or None). Virtuoso answers its own timeout with HTTP 206."""
    t0 = time.perf_counter()
    r = requests.post(S.SPARQL_ENDPOINT, data={"query": q, "timeout": str(int(timeout * 1000))},
                      headers={"Accept": "application/sparql-results+json"}, timeout=2 * timeout + 30)
    if r.status_code == 206 or r.headers.get("X-SQL-State") == "S1TAT":
        raise TimeoutError(f"timeout after {timeout} s")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {' '.join(r.text.split())[:160]}")
    j = r.json()
    cols = j["head"]["vars"]
    return time.perf_counter() - t0, cols, [tuple(b.get(c) for c in cols) for b in j["results"]["bindings"]]


def outcome(q, timeout):
    """{status, seconds, cols, rows} or {status, error}; never raises."""
    if why := runnable(q):
        return {"status": "not run", "error": why}
    try:
        s, cols, rows = execute(q, timeout)
        return {"status": "ok", "seconds": round(s, 3), "cols": cols, "rows": rows}
    except TimeoutError as e:
        return {"status": "timeout", "error": str(e)}
    except Exception as e:
        return {"status": "error", "error": " ".join(str(e).split())[:160]}


# ---- comparing ------------------------------------------------------------------------------------------------------
def raw(b):
    """evaluate.py's view of a value: the string, None when unbound."""
    return str(None) if b is None else b["value"]


def norm(b):
    """A value as compared: numbers to 6 significant digits, booleans true/false, '' when unbound, else the string."""
    if b is None:
        return ""
    v = b.get("value", "")
    if b.get("datatype") == XSD + "boolean":
        return "true" if v.strip().lower() in ("true", "1") else "false"
    if b.get("type") == "literal" and NUMBER.fullmatch(v.strip()):
        x = float(v)
        return f"{x:.6g}" if x else "0"
    return v


def is_num(v):
    return bool(NUMBER.fullmatch(v)) and v != ""


def rowset(rows, f=norm):
    """The rows as a multiset, each row the sorted tuple of its values: column order and names ignored."""
    return Counter(tuple(sorted(f(v) for v in r)) for r in rows)


def digest(counter):
    return hashlib.sha256(repr(sorted(counter.items())).encode()).hexdigest()[:12]


def columns(rows, n):
    return [[norm(r[i]) for r in rows] for i in range(n)]


def align(a_cols, b_cols, how):
    """[(i, j)]: each column of the narrower side matched to a distinct column of the wider one whose values are equal as
    a multiset (how='multiset') or a superset as a set (how='subset'); None when a narrower column has no match."""
    swap = len(a_cols) > len(b_cols)
    narrow, wide = (b_cols, a_cols) if swap else (a_cols, b_cols)
    used, pairs = set(), []
    for i, c in enumerate(narrow):
        for j, d in enumerate(wide):
            if j in used:
                continue
            if (Counter(c) == Counter(d)) if how == "multiset" else (set(c) <= set(d) or set(d) <= set(c)):
                used.add(j)
                pairs.append((j, i) if swap else (i, j))
                break
        else:
            return None
    return pairs


def project(rows, idx):
    return Counter(tuple(norm(r[i]) for i in idx) for r in rows)


def compare(o, r):
    """The comparison of our result o with the reference's r (both ok): class, hashes, aligned, subset, numbers."""
    a, b = o["rows"], r["rows"]
    out = {"hash_ours": digest(rowset(a)), "hash_ref": digest(rowset(b))}
    strict = sorted(map(sorted, map(lambda x: map(raw, x), a))) == sorted(map(sorted, map(lambda x: map(raw, x), b)))
    na, nb = rowset(a), rowset(b)
    blank_a, blank_b = (all(v in BLANK for row in x for v in row) for x in (na, nb))
    ca, cb = columns(a, len(o["cols"])), columns(b, len(r["cols"]))
    pairs = align(ca, cb, "multiset") if a and b else None
    aligned = pairs is not None and project(a, [i for i, _ in pairs]) == project(b, [j for _, j in pairs])
    if pairs is not None:
        out["hash_aligned"] = [digest(project(a, [i for i, _ in pairs])), digest(project(b, [j for _, j in pairs]))]
    nums_a = Counter(v for row in na.elements() for v in row if is_num(v))
    nums_b = Counter(v for row in nb.elements() for v in row if is_num(v))
    loose = align(ca, cb, "subset") if a and b else None
    sub = None
    if loose is not None:
        pa, pb = set(project(a, [i for i, _ in loose])), set(project(b, [j for _, j in loose]))
        sub = "equal sets" if pa == pb else "ours subset of ref" if pa < pb else "ours superset of ref" if pa > pb else None
    out["subset"] = sub
    if len(a) == 1 and len(b) == 1:
        x, y = [float(v) for v in nums_a.elements()], [float(v) for v in nums_b.elements()]
        out["numbers"] = [x, y]
        if len(x) == 1 and len(y) == 1:
            out["ratio"] = round(x[0] / y[0], 4) if y[0] else None
    out["class"] = ("both empty" if blank_a and blank_b else "identical" if strict else
                    "empty on one side" if blank_a or blank_b else "same up to format" if na == nb else
                    "same numbers other shape" if aligned or (nums_a and nums_a == nums_b) else
                    "subset/superset" if sub in ("ours subset of ref", "ours superset of ref", "equal sets") else "different")
    return out


# ---- terms and codes the data never saw -----------------------------------------------------------------------------
def _stats():
    """(sphn terms in the data, code IRIs in the data, prefixes) from the 100k scan."""
    pre = dict(re.findall(r"@prefix\s+([\w-]*):\s*<([^>]*)>", (STATS / "prefixes.ttl").read_text()))
    full = lambda t: t[1:-1] if t.startswith("<") else pre.get(t.split(":", 1)[0], t.split(":", 1)[0] + ":") + t.split(":", 1)[1]
    terms = {full(l.split("\t")[0]) for f in ("classes.tsv", "predicates.tsv") for l in (STATS / f).read_text().splitlines()[1:]}
    codes = {full(l.split("\t")[2]) for l in (STATS / "codes.tsv").read_text().splitlines()[1:]}
    return terms, codes, pre


_DATA = None
HIERARCHICAL = ("/chop/", "/icd-10-gm/", "/atc/")  # a code string's prefix is its parent; SNOMED's hierarchy is not in the string
TERMINOLOGY = ("chop", "icd-10-gm", "snomed", "ucum", "loinc", "atc", "sphn-atc", "sphn-loinc")


def unseen(q):
    """{terms: sphn: terms absent from the data, codes: {code: present | sub-codes | absent}} of one query."""
    global _DATA
    _DATA = _DATA or _stats()
    terms, codes, pre = _DATA
    text = _LEX.sub(lambda m: m.group(0) if m.group(0)[0] == "<" else ' "" ', q)
    prefixes = {**pre, **SC.PREFIXES, **dict(re.findall(r"(?i)\bPREFIX\s+([\w.-]*):\s*<([^>]*)>", text))}
    text = re.sub(r"(?i)\bPREFIX\s+[\w.-]*:\s*<[^>]*>", " ", text)
    names = {(p, prefixes.get(p, p + ":") + n) for p, n in re.findall(r"(?<![\w.:?$-])([A-Za-z][\w-]*):([\w.-]*\w)", text)}
    names |= {("", i) for i in re.findall(r"<([^<>\s]*)>", text)}
    out = {"terms": sorted("sphn:" + i[len(SC.NS):] for _, i in names if i.startswith(SC.NS) and i not in terms), "codes": {},
           "inference": bool(re.search(r"(?i)OPTION\s*\(\s*inference", q))}
    for p, i in names:
        ns = next((prefixes[t] for t in TERMINOLOGY if t in prefixes and i.startswith(prefixes[t])), None)
        if not ns or i == ns:
            continue
        local, tree = i[len(ns):], any(t in ns for t in HIERARCHICAL)
        here = [c for c in codes if c.startswith(ns) and (re.sub(r"^\d{4}/", "", c[len(ns):]).startswith(local) if tree else c == i)]
        exact = i in codes or any(re.sub(r"^\d{4}/", "", c[len(ns):]) == local for c in here)
        out["codes"][(p + ":" + local) if p else i] = "present" if exact else f"sub-codes ({len(here)})" if here else \
            "absent (exact)" if not tree and out.get("inference") else "absent"
    return out


# ---- the run --------------------------------------------------------------------------------------------------------
def load(paths, ids):
    """{(id, query): {count, sources, verdicts}} of the traces, identical queries merged."""
    seen = {}
    for p in paths:
        for t in map(json.loads, filter(str.strip, Path(p).read_text().splitlines())):
            q = (t.get("sparql_query") or "").strip()
            if ids and t["id"] not in ids:
                continue
            e = seen.setdefault((t["id"], q), {"count": 0, "sources": Counter(), "verdicts": Counter()})
            e["count"] += 1
            e["sources"][t.get("source") or Path(p).parent.name + "/" + Path(p).name] += 1
            e["verdicts"]["compliant" if t.get("is_compliant") else "refused"] += 1
    return seen


def main():
    a = argparse.ArgumentParser()
    a.add_argument("traces", nargs="+")
    a.add_argument("--out", help="JSONL, one line per distinct query (no rows)")
    a.add_argument("--ids", nargs="+")
    a.add_argument("--bench", nargs="+", type=Path, default=BENCH, help="reference JSONL files (default: tests/bench/*.jsonl)")
    a.add_argument("--all", action="store_true", help="also the requests whose reference is not compliant")
    a.add_argument("--timeout", type=float, default=60, help="seconds for our query, Virtuoso's own timeout")
    a.add_argument("--ref-timeout", type=float, default=300, help="seconds for the reference (O05 takes ~95 s on 5k)")
    a = a.parse_args()
    bench = {t["id"]: t for f in a.bench for t in map(json.loads, filter(str.strip, f.read_text().splitlines()))}
    refs, lines = {}, []
    for (i, q), e in sorted(load(a.traces, set(a.ids or [])).items(), key=lambda kv: (kv[0][0], -kv[1]["count"])):
        t = bench.get(i)
        if not t or not t.get("sparql") or not (t["compliant"] or a.all):
            continue
        if i not in refs:
            refs[i] = outcome(t["sparql"], a.ref_timeout)
        r, o = refs[i], outcome(q, a.timeout) if q else {"status": "error", "error": "no query"}
        line = {"id": i, "compliant_ref": t["compliant"], "query_hash": hashlib.sha256(q.encode()).hexdigest()[:12],
                "count": e["count"], "verdicts": dict(e["verdicts"]), "sources": dict(e["sources"]),
                **{f"{k}_{side}": x.get(k) for side, x in (("ours", o), ("ref", r)) for k in ("status", "seconds", "error")},
                **{f"rows_{side}": len(x["rows"]) if x["status"] == "ok" else None for side, x in (("ours", o), ("ref", r))},
                **{f"cols_{side}": x.get("cols") for side, x in (("ours", o), ("ref", r))},
                "unseen_ours": unseen(q) if q else None, "unseen_ref": unseen(t["sparql"])}
        if o["status"] == "ok" and r["status"] == "ok":
            line |= compare(o, r)
            if not t["compliant"]:
                line.pop("numbers", None), line.pop("ratio", None)
        else:
            line["class"] = "error: " + ("both" if o["status"] != "ok" and r["status"] != "ok" else "ours" if o["status"] != "ok" else "ref")
        lines.append(line)
        nums = f" {line['numbers']}" if line.get("numbers") else ""
        print(f"{i} x{e['count']:<2} {line['class']:<25} rows {line['rows_ours']}/{line['rows_ref']} cols "
              f"{len(line['cols_ours'] or [])}/{len(line['cols_ref'] or [])} {line['query_hash']}{nums}"
              + (f" | {line['error_ours'] or ''}" if o["status"] != "ok" else ""), flush=True)
        if a.out:
            Path(a.out).write_text("".join(json.dumps(l) + "\n" for l in lines))
    print(Counter(l["class"] for l in lines))


if __name__ == "__main__":
    main()
