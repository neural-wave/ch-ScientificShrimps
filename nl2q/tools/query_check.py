"""Check the queries the translation layer writes, three things per query: CORRECT (the same rows as a trusted
reference query on the mock data), VALID (it parses and names only SPHN 2026.1 terms) and FAST (seconds on Virtuoso,
the median of the runs, next to the reference). It never asks for a compliance verdict (the compliance layer's job).
  python -m nl2q.tools.query_check --set bench --out nl2q_data/runs/NN-qc-bench   # tests/bench/*.jsonl
  python -m nl2q.tools.query_check --set easy --out DIR    # tests/easy/*.jsonl: 30 clear questions, written blind
  python -m nl2q.tools.query_check --set fresh --out DIR   # tests/fresh/*.jsonl: 30 more, over 26 SPHN classes
  python -m nl2q.tools.query_check --set clean --out DIR   # held out, SHA-256 checked: ids and numbers only, never text
Options: --runs N timed runs of our query, the median kept (default 3); --timeout S our query's limit (default 120 s;
30 s on the 5k-patient mock is about the jury's 10 minutes on 100k); --workers N requests translated at a time
(default 4); --ids to rerun some items; --no-holdout to let a request see its own worked example; --refresh-terms,
--refresh-refs to build the two caches (nl2q_data/query_check) again.
Every request is translated first (in threads), then the queries run one at a time: never two on the database at once.
The mock data is SPHN 2024.2, our queries are 2026.1: to_2024 maps the renamed terms back before a query runs (ours and
the reference; a 2024.2 query passes unchanged). Only read-only SELECT and ASK queries run (runnable), and none that
reads terminology labels. A reference runs once, at most 120 s, and its result is kept for every later version and run
(References). Which sphn: terms the mock data has is asked once and kept too (data_terms): a result that does not match
is untestable, not wrong, when our query names a term the data lacks (ref_untestable when the reference does).
Categories: match, both_empty (inconclusive), mismatch, untestable, ours_error, ours_timeout, ref_error, ref_timeout,
ref_untestable, not_compared. F1 is value-level, as in SIB's SPARQL-LLM benchmark: partial credit on a mismatch."""
import argparse, hashlib, json, re, statistics, sys, threading, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import requests
from .. import settings as S

BENCH = sorted((Path(__file__).parents[2] / "tests" / "bench").glob("*.jsonl"))
EASY = sorted((Path(__file__).parents[2] / "tests" / "easy").glob("*.jsonl"))
FRESH = sorted((Path(__file__).parents[2] / "tests" / "fresh").glob("*.jsonl"))
TIMEOUT = 120             # seconds per run: the reference's limit, and ours unless --timeout
ASK_TIMEOUT = 10          # seconds per ASK of data_terms
WORKERS = 4               # requests translated at a time
CACHE = S.DATA / "query_check"  # terms.json (data_terms) and references.json (References)
SLOW_RATIO, SLOW_SECONDS = 3, 10  # flagged: ours over 3 times the reference, or over 10 s
CATEGORIES = ("match", "both_empty", "mismatch", "untestable", "ours_error", "ours_timeout", "ref_error", "ref_timeout",
              "ref_untestable", "not_compared")
PUBLIC = ("id", "path", "category", "f1", "rows_ours", "rows_ref", "valid", "seconds_ours", "seconds_ref", "ratio", "slow",
          "status_ours", "status_ref", "mapped_ours", "mapped_ref", "timeout_ours", "ref_cached", "translate_seconds",
          "tokens", "rejected")  # clean set
XSD = "http://www.w3.org/2001/XMLSchema#"

# ---- SPHN 2026.1 -> 2024.2 -----------------------------------------------------------------------------------------
# make_examples.RENAMES backwards, plus the other ways our queries write the principal rank and the stay dates. Every
# pattern needs a 2026.1 term the mock data lacks (hasRankCode, hasAdmission, hasDischarge, hasGenomicVariation).
IND = "https://biomedit.ch/rdf/sphn-schema/sphn/individual#"
PRINCIPAL = f"<{IND}Principal>"  # 2024.2: sphn-individual:Principal
MAIN = "<http://snomed.info/id/63161005>"  # 2026.1: the principal rank's code class
# The 2024.2 rank individuals (all four in the mock data) with their 2026.1 class: only the principal one has a known
# code (RENAMES), the others stay themselves. A VALUES table, not a BIND: under DEFINE sql:select-option "order" a
# BIND there makes Virtuoso time out on a query that runs in 0.05 s.
RANKS = " ".join(f"(<{IND}{r}> {MAIN if r == 'Principal' else f'<{IND}{r}>'})"
                 for r in ("Principal", "Secondary", "Complementary", "Supplementary"))
_TYPE = r"(?:rdf:type|a|<http://www\.w3\.org/1999/02/22-rdf-syntax-ns#type>)"
_MAIN = r"(?:snomed:63161005|<http://snomed\.info/id/63161005>)"
BACK = [("rank", rf"(?<![\w-])sphn:hasRankCode\s*/\s*{_TYPE}\s+{_MAIN}", f"sphn:hasRank {PRINCIPAL}"),
        ("stay dates", r"(\?\w+)\s+sphn:has(Admission|Discharge)\s+(\?\w+)\s*\.\s*\3\s+sphn:hasDateTime\b", r"\1 sphn:has\2DateTime"),
        ("stay dates", r"(?<![\w-])sphn:has(Admission|Discharge)\s*/\s*sphn:hasDateTime\b", r"sphn:has\1DateTime"),
        ("genetic variation", r"(?<![\w-])sphn:hasGenomicVariation\b", "sphn:hasGeneticVariation")]
NAMES_2024 = ("hasRank", "hasAdmissionDateTime", "hasDischargeDateTime", "hasGeneticVariation")  # what to_2024 writes


def _rank_codes(q):
    """?record sphn:hasRankCode ?c . ?c rdf:type <class>: in 2024.2 ?c is the rank individual itself (sphn:hasRank).
    The principal class becomes a FILTER on Principal; a class variable comes from the RANKS table."""
    for v in set(re.findall(r"sphn:hasRankCode\s+(\?\w+)", q)):
        def typed(m, v=v):
            cls, end = m.group(1), m.group(2)
            return (f"VALUES ({v} {cls}) {{ {RANKS} }}{end}" if cls.startswith("?") else
                    f"FILTER ({v} = {PRINCIPAL}){end}")
        q = re.sub(rf"(?<![\w?$]){re.escape(v)}\s+{_TYPE}\s+({_MAIN}|\?\w+)(?:\s*OPTION\s*\([^()]*\))?(\s*\.|(?=\s*\}}))",
                   typed, q)
    return re.sub(r"(?<![\w-])sphn:hasRankCode\b", "sphn:hasRank", q)


def to_2024(query):
    """(the query for the mock data, [what was mapped]): the SPHN 2026.1 terms the mock data (2024.2) lacks, mapped
    back. A 2024.2 query comes back unchanged, with []."""
    done = []
    for name, pattern, new in BACK:
        query, n = re.subn(pattern, new, query)
        done += [name] * bool(n)
    if re.search(r"(?<![\w-])sphn:hasRankCode\b", query):
        query = _rank_codes(query)
        done.append("rank")
    return query, sorted(set(done))


# ---- what may run: read-only SELECT and ASK ------------------------------------------------------------------------
UPDATE = ("INSERT", "DELETE", "LOAD", "CLEAR", "DROP", "CREATE", "ADD", "MOVE", "COPY", "WITH")
FORMS = ("SELECT", "ASK", "CONSTRUCT", "DESCRIBE", *UPDATE)
DEFINES = {"sql:select-option", "input:inference", "input:same-as"}  # Virtuoso pragmas that only steer a query
VENDOR = ("bif:", "sql:", "http://www.openlinksw.com/schemas/bif#", "http://www.openlinksw.com/schemas/sql#")
BIF = {"datediff", "dateadd", "year", "month", "dayofmonth", "hour", "minute", "second", "now", "curdatetime", "contains",
       "lower", "upper", "lcase", "ucase", "length", "left", "right", "substring", "strstr", "replace", "concat", "sprintf",
       "round", "floor", "ceiling", "abs", "sqrt", "power", "mod", "isnull"}  # Virtuoso functions that only compute
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"  # terminology labels (restricted licences): never read
SKOS = "http://www.w3.org/2004/02/skos/core#"
_LEX = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|<[^<>"{}|^`\\\s]*>|#[^\n]*|[?$]\w+')
_DECLARED = r"(?i)\bPREFIX\s+([\w.-]*):\s*<([^>]*)>"


def _blank(query):
    """The query with its strings, comments and variables blanked out (IRIs kept): no keyword can hide in them."""
    return _LEX.sub(lambda m: {"#": " ", "?": " ?v ", "$": " ?v ", '"': ' "" ', "'": ' "" '}.get(m.group(0)[0], m.group(0)), query)


def names(query):
    """Every IRI the query names, <...> or prefixed (read with its PREFIX lines, else nl2q.schema's prefixes); not in
    strings or comments."""
    from .. import schema as SC
    text = _blank(query)
    prefixes = {**SC.PREFIXES, **dict(re.findall(_DECLARED, text))}
    text = re.sub(_DECLARED, " ", text)
    return {prefixes.get(p, p + ":") + local for p, local in re.findall(r"(?<![\w.:-])([A-Za-z][\w.-]*|):([\w.-]*\w)", text)} \
        | set(re.findall(r"<([^<>\s]*)>", text))


def runnable(query):
    """None when the query may run here, else why not. It runs when it is a SELECT or ASK with no update operation, no
    SERVICE (another endpoint), only the Virtuoso pragmas and functions that do not write or fetch, and no terminology
    labels (rdfs:label, skos:)."""
    text = _blank(query)
    words = re.sub(r"<[^<>\s]*>|(?<![\w.:-])[A-Za-z][\w.-]*:[\w.%-]*|(?<![\w.:-]):[\w.%-]+", " ", text)  # IRIs, names
    form = re.search(rf"\b({'|'.join(FORMS)})\b", words, re.I)
    form = form.group(1).upper() if form else None
    if form in ("CONSTRUCT", "DESCRIBE"):
        return f"{form}, not compared"
    if form not in ("SELECT", "ASK"):
        return f"{form or 'no query form'}, never run"
    found = {w.upper() for w in re.findall(r"[A-Za-z]+", words)}
    if bad := sorted(found & set(UPDATE)):
        return f"{bad[0]}, never run"
    if "SERVICE" in found:
        return "SERVICE (another endpoint), never run"
    if bad := [d for d in re.findall(r"(?i)\bDEFINE\s+([\w-]+:[\w-]+)", text) if d.lower() not in DEFINES]:
        return f"DEFINE {bad[0]}, never run"
    prefixes = dict(re.findall(r"(?i)\bPREFIX\s+([\w.-]*):\s*<([^>]*)>", text))
    calls = [prefixes.get(p, p + ":") + n for p, n in re.findall(r"(?<![\w.:-])([A-Za-z][\w.-]*|):([\w.-]+)\s*\(", text)]
    calls += re.findall(r"<([^<>\s]*)>\s*\(", text)
    if bad := [c for c in calls if c.startswith(VENDOR) and re.split(r"[:#]", c)[-1].lower() not in BIF]:
        return f"{bad[0]} (not read-only), never run"
    if bad := sorted(n for n in names(query) if n == LABEL or n.startswith((SKOS, "skos:"))):
        return ("rdfs:label" if bad[0] == LABEL else "skos:" + re.split(r"[#:]", bad[0])[-1]) + \
            " (terminology labels), never run"
    return None


def valid(query):
    """(True, None) when the query parses (compliance.validate's syntax check: Virtuoso's DEFINE and OPTION allowed)
    and every sphn: term it names is in the SPHN 2026.1 schema (nl2q.schema.TERMS), else (False, why)."""
    from .. import schema as SC
    from ..compliance import validate
    if not query.strip():
        return False, "no query"
    if (problem := validate(query)) and not problem.startswith("not in the SPHN schema"):
        return False, problem
    unknown = sorted(n for n in names(query) if n.startswith(SC.NS) and n not in SC.TERMS)
    return (False, "not in the SPHN 2026.1 schema: " + ", ".join("sphn:" + n[len(SC.NS):] for n in unknown)) if unknown \
        else (True, None)


# ---- running a query -----------------------------------------------------------------------------------------------
class TooSlow(Exception):
    """The query ran longer than the limit."""


_http = requests.Session()


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def execute(query, timeout=TIMEOUT):
    """(seconds, rows) of one run on S.SPARQL_ENDPOINT, rows of JSON bindings (None when unbound); an ASK gives one row
    with its boolean. Virtuoso stops the query itself at the limit (an anytime query: HTTP 206, X-SQL-State S1TAT,
    partial rows), so no query outlives its run; that, or a run over the limit, raises TooSlow."""
    t0 = time.perf_counter()
    try:
        r = _http.post(S.SPARQL_ENDPOINT, data={"query": query, "timeout": int(timeout * 1000)},
                       headers={"Accept": "application/sparql-results+json"}, timeout=2 * timeout + 30)
        if r.status_code == 206 or r.headers.get("X-SQL-State") == "S1TAT":
            raise TooSlow
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {' '.join(r.text.split())[:300]}")
        j = r.json()
    except requests.Timeout:
        raise TooSlow from None
    seconds = time.perf_counter() - t0
    if seconds > timeout:
        raise TooSlow
    if "boolean" in j:
        return seconds, [({"type": "literal", "datatype": XSD + "boolean", "value": str(j["boolean"]).lower()},)]
    cols = j["head"]["vars"]
    return seconds, [tuple(b.get(c) for c in cols) for b in j["results"]["bindings"]]


def measure(queries, runs, timeout=TIMEOUT, run=execute):
    """{name: {status (ok, error or timeout), seconds (median of the runs), rows (of the first run), error}}: the queries
    run in turn, `runs` times each, at most `timeout` s ({name: seconds} for a limit per query); one that fails is not
    run again."""
    limit = timeout if isinstance(timeout, dict) else dict.fromkeys(queries, timeout)
    out = {k: {"status": "ok", "times": []} for k in queries}
    for _ in range(runs):
        for k, q in queries.items():
            o = out[k]
            if o["status"] != "ok":
                continue
            try:
                seconds, rows = run(q, limit[k])
            except TooSlow:
                o["status"] = "timeout"
            except Exception as e:
                o["status"], o["error"] = "error", " ".join(str(e).split())[:300]
            else:
                o["times"].append(seconds)
                o.setdefault("rows", rows)
    for o in out.values():
        o["seconds"] = statistics.median(o["times"]) if o["status"] == "ok" else None
    return out


class References:
    """The reference queries' results, each run once (at most TIMEOUT s) and kept in a file (CACHE/references.json)
    for every later version and run: {key: {status, seconds, rows, error, endpoint, date}}, the key the SHA-256 of the
    endpoint and the mapped reference (the text itself is never kept). refresh: run each reference again, once in this
    run. secret (the clean set): of an error only its HTTP status is kept (Virtuoso's message quotes the query). A
    failure that is not Virtuoso's answer (no HTTP 400 or 500: the server out of reach) is not kept."""

    def __init__(self, path=None, refresh=False, secret=False):
        self.path, self.refresh, self.secret, self.fresh = path or CACHE / "references.json", refresh, secret, set()
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    @staticmethod
    def key(query):
        return hashlib.sha256(f"{S.SPARQL_ENDPOINT}\n{query}".encode()).hexdigest()

    def get(self, query, run=execute):
        """(the reference's result {status, seconds, rows, error}, True when it was not run now)."""
        k = self.key(query)
        if k in self.data and (k in self.fresh or not self.refresh):
            return self.data[k], True
        o = measure({"ref": query}, 1, TIMEOUT, run)["ref"]
        e = {"status": o["status"], "seconds": o["seconds"], "rows": o.get("rows"), "endpoint": S.SPARQL_ENDPOINT,
             "date": _now()}
        if o.get("error"):
            e["error"] = o["error"].split(":")[0] if self.secret else o["error"]
            if not o["error"].startswith(("HTTP 400", "HTTP 500")):
                return e, False
        self.data[k] = e
        self.fresh.add(k)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False))
        tmp.replace(self.path)
        return e, False


# ---- the sphn: terms the mock data has -----------------------------------------------------------------------------
def asked_terms():
    """The sphn: terms data_terms asks about: nl2q.schema's (SPHN 2026.1) and the 2024.2 names to_2024 writes."""
    from .. import schema as SC
    return sorted(SC.TERMS | {SC.NS + n for n in NAMES_2024})


def ask(term):
    """The tiny ASK that tells whether the data has a term: an instance of a class (capitalised, as SPHN names them)
    or a triple with a property."""
    return f"ASK {{ ?s a <{term}> }}" if re.split(r"[#/]", term)[-1][:1].isupper() else f"ASK {{ ?s <{term}> ?o }}"


def data_terms(refresh=False, run=execute, path=None, terms=None):
    """{endpoint, date, seconds, ns, present, absent, failed}: which sphn: terms (names under ns) the mock data has,
    one ASK per term (ask), one at a time, each stopped by Virtuoso at ASK_TIMEOUT s. Kept in a file (CACHE/terms.json)
    and asked again with refresh, for another endpoint, for terms the file lacks, or after an ASK failed (a term whose
    ASK failed never counts as absent)."""
    from .. import schema as SC
    path, terms = path or CACHE / "terms.json", sorted(terms or asked_terms())
    if path.exists() and not refresh:
        kept = json.loads(path.read_text())
        if kept.get("endpoint") == S.SPARQL_ENDPOINT and not kept.get("failed") and \
                {kept["ns"] + n for n in kept["present"] + kept["absent"]} >= set(terms):
            return kept
    t0, found = time.perf_counter(), {"present": [], "absent": [], "failed": []}
    for term in terms:
        try:
            _, rows = run(ask(term), ASK_TIMEOUT)
            found["present" if rows and value(rows[0][0]) == "true" else "absent"].append(term[len(SC.NS):])
        except Exception:
            found["failed"].append(term[len(SC.NS):])
    out = {"endpoint": S.SPARQL_ENDPOINT, "date": _now(), "seconds": round(time.perf_counter() - t0, 1), "ns": SC.NS, **found}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1) + "\n")
    return out


def gone(query, absent):
    """The sphn: terms the query names that the mock data lacks (absent: their IRIs), as sphn:Name."""
    return sorted("sphn:" + n.rsplit("#", 1)[-1] for n in names(query) if n in absent)


def untestable(out, gone_ours, gone_ref):
    """{category, why, absent_*}: a result that does not match is untestable, not wrong, when a query names a sphn:
    term the mock data lacks: the reference first (ref_untestable), then ours (untestable). Only for what such a term
    can cause: no rows, other rows, or a runtime error (HTTP 500: a share of nothing divides by zero). A timeout, or a
    query Virtuoso cannot compile (HTTP 400), is the query's own fault whatever the data."""
    fault = lambda side: (out.get(f"error_{side}") or "").startswith("HTTP 500")
    cat = out["category"]
    data = cat in ("mismatch", "both_empty") or (cat == "ours_error" and fault("ours"))
    new = {f"absent_{side}": g for side, g in (("ours", gone_ours), ("ref", gone_ref)) if g}
    if gone_ref and (data or (cat == "ref_error" and fault("ref"))):
        return {**new, "category": "ref_untestable", "why": f"the reference names {', '.join(gone_ref)}: not in the mock data"}
    if gone_ours and data:
        return {**new, "category": "untestable", "why": f"ours names {', '.join(gone_ours)}: not in the mock data"}
    return new


# ---- comparing results ---------------------------------------------------------------------------------------------
NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def value(b):
    """One result value as compared: a number rounded to 6 significant digits, a boolean as true/false, an IRI or a
    string (or date) exactly; '' when unbound. b: a JSON binding ({type, value, datatype}) or a plain value."""
    if b is None:
        return ""
    b = b if isinstance(b, dict) else {"type": "literal", "value": str(b)}
    v = b.get("value", "")
    if b.get("type") == "bnode":
        return "_:"
    if b.get("type") == "uri":
        return v
    if b.get("datatype") == XSD + "boolean":
        return "true" if v.strip().lower() in ("true", "1") else "false"
    if NUMBER.fullmatch(v.strip()):
        x = float(v)
        return f"{x:.6g}" if x else "0"
    return v


def normalize(rows):
    """The rows as a multiset, each row the sorted tuple of its values: column names and order do not matter."""
    return Counter(tuple(sorted(value(v) for v in row)) for row in rows)


def blank(result):
    """No answer: no rows, or nothing but unbound values, zeros and false (a count of nothing, an ASK that says no)."""
    return all(v in ("", "0", "false") for row in result for v in row)


def f1(ours, ref):
    """Value-level F1 (SIB's SPARQL-LLM benchmark): precision and recall of the set of all values ours returns against
    the set of all values the reference returns, unbound values left out."""
    a, b = ({v for row in x for v in row if v != ""} for x in (ours, ref))
    hit = len(a & b)
    return 1.0 if not a and not b else 0.0 if not hit else 2 * hit / (len(a) + len(b))


def compare(ours_rows, ref_rows):
    """(category, F1) of two results that both came back: both_empty (F1 None), match or mismatch."""
    a, b = normalize(ours_rows), normalize(ref_rows)
    if blank(a) and blank(b):
        return "both_empty", None
    return ("match" if a == b else "mismatch"), f1(a, b)


def check(ours, ref, runs=3, timeout=TIMEOUT, run=execute, refs=None, absent=frozenset()):
    """Our query against the reference, both mapped to 2024.2 first: {category, why, f1, rows_*, seconds_*, ratio, slow,
    status_*, error_*, mapped_*, absent_*, timeout_ours, ref_cached}. Ours runs `runs` times, at most `timeout` s
    each; the reference at most TIMEOUT s, once from `refs` (References) when given, else next to ours. When both fail,
    the reference's failure is the category (nothing to compare with). absent: the IRIs of the sphn: terms the mock
    data lacks (data_terms), for the untestable categories."""
    (ours24, mapped_ours), (ref24, mapped_ref) = to_2024(ours or ""), to_2024(ref)
    out = {"mapped_ours": mapped_ours, "mapped_ref": mapped_ref}
    if not (ours or "").strip():
        return {**out, "category": "ours_error", "why": "no query written"}
    why = [f"{side}: {w}" for side, q in (("ours", ours24), ("reference", ref24)) if (w := runnable(q))]
    if why:
        return {**out, "category": "not_compared", "why": "; ".join(why)}
    out["timeout_ours"] = timeout
    if refs is None:
        m = measure({"ref": ref24, "ours": ours24}, runs, {"ref": TIMEOUT, "ours": timeout}, run)
    else:
        kept, out["ref_cached"] = refs.get(ref24, run)
        m = {"ref": kept, **measure({"ours": ours24}, runs, timeout, run)}
    for side, o in m.items():
        out |= {f"status_{side}": o["status"], f"seconds_{side}": o["seconds"],
                f"rows_{side}": len(o["rows"]) if o["status"] == "ok" else None}
        if o.get("error"):
            out[f"error_{side}"] = o["error"]
    if m["ref"]["status"] != "ok":
        out["category"] = f"ref_{m['ref']['status']}"
    elif m["ours"]["status"] != "ok":
        out["category"] = f"ours_{m['ours']['status']}"
    else:
        out["category"], out["f1"] = compare(m["ours"]["rows"], m["ref"]["rows"])
    if absent and out["category"] != "match":
        out |= untestable(out, gone(ours24, absent), gone(ref24, absent))
    a, b = out["seconds_ours"], out["seconds_ref"]
    out["ratio"] = a / b if a is not None and b else None
    out["slow"] = a is not None and (a > SLOW_SECONDS or (out["ratio"] or 0) > SLOW_RATIO)
    return out


# ---- the sets ------------------------------------------------------------------------------------------------------
def bench(files=None):
    """tests/bench/*.jsonl (or files): {id, question, reference}."""
    return [{"id": t["id"], "question": t["question"], "reference": t["sparql"]}
            for f in files or BENCH for t in map(json.loads, filter(str.strip, f.read_text().splitlines()))
            if t.get("sparql")]


def easy():
    """tests/easy/*.jsonl: clear questions written without seeing the translator, references checked on the data."""
    return bench(EASY)


def clean():
    """The held-out set (clean_test.FILE, SHA-256 checked first): the items with a reference_sparql."""
    from .clean_test import FILE, SHA256
    raw = FILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SHA256:
        raise SystemExit(f"{FILE} changed since it was built (SHA-256 differs): not running it")
    return [{"id": t["id"], "question": t["question"], "reference": t["reference_sparql"]}
            for t in map(json.loads, filter(str.strip, raw.decode().splitlines())) if t.get("reference_sparql")]


# ---- translating, in threads ---------------------------------------------------------------------------------------
_parse = threading.Lock()  # compliance.validate (rdflib's SPARQL parser) in one thread at a time
_db = threading.Lock()     # one query on the database at a time


def _validate(query):
    from ..compliance import validate
    with _parse:
        return validate(query)


def translate(t, check=None):
    """{query, attempts, usage, info, seconds, failed}: the request translated by the real translator (generate), check
    (compliance.validate) its feedback; failed says why when the translator raised (the model server, say)."""
    from ..translation import generate
    usage, info, t0, failed = {}, {}, time.perf_counter(), None
    try:
        ours, attempts = generate(t["question"], check or _validate, usage=usage, info=info)
    except Exception as e:  # recorded, and the run goes on
        ours, attempts, failed = "", [], f"translation failed: {type(e).__name__}"
    return {"query": ours, "attempts": attempts, "usage": usage, "info": info, "seconds": time.perf_counter() - t0,
            "failed": failed}


@contextmanager
def one_query_at_a_time():
    """While requests are translated in threads: any database lookup made while answering (store.run) waits for the
    others, and carries Virtuoso's server-side timeout. The same (columns, rows) and the same failure past the limit
    (an exception)."""
    from .. import store
    original = store.run

    def run(query, timeout=300):
        with _db:
            r = _http.post(S.SPARQL_ENDPOINT, data={"query": query, "timeout": int(timeout * 1000)},
                           headers={"Accept": "application/sparql-results+json"}, timeout=timeout)
            if r.status_code == 206 or r.headers.get("X-SQL-State") == "S1TAT":
                raise TooSlow
            r.raise_for_status()
            j = r.json()
        cols = j["head"]["vars"]
        return cols, [tuple(b[c]["value"] if c in b else None for c in cols) for b in j["results"]["bindings"]]
    store.run = run
    try:
        yield
    finally:
        store.run = original


def translate_all(items, workers=WORKERS):
    """{id: translation} of every item, `workers` requests at a time (the model server batches them), before any query
    runs. Progress on stderr: ids and seconds only."""
    done = {}
    with one_query_at_a_time(), ThreadPoolExecutor(max(1, workers)) as pool:
        jobs = {pool.submit(translate, t): t["id"] for t in items}
        for job in as_completed(jobs):
            done[jobs[job]] = d = job.result()
            print(f"{jobs[job]:<12} translated in {d['seconds']:.1f} s" + (f" ({d['failed']})" if d["failed"] else ""),
                  file=sys.stderr, flush=True)
    return done


# ---- one item, the report ------------------------------------------------------------------------------------------
def one(t, runs, secret, timeout=TIMEOUT, refs=None, absent=frozenset(), done=None):
    """Translate the request (unless done is its translation), then check our query: valid, and against the reference.
    secret (the clean set): ids and numbers only."""
    done = done or translate(t)
    ours, info, failed = done["query"], done["info"], done["failed"]
    r = {"id": t["id"], "path": info.get("path", "-"), "translate_seconds": round(done["seconds"], 2),
         "tokens": sum(done["usage"].values()), "rejected": len(done["attempts"])}
    try:  # a bug in either must not end a long run
        r["valid"], r["invalid"] = valid(ours)
    except Exception as e:
        r["valid"], r["invalid"] = None, f"valid() failed: {type(e).__name__}"
    try:
        r |= check(ours, t["reference"], runs, timeout, refs=refs, absent=absent)
    except Exception as e:
        r |= {"category": "not_compared", "why": f"query_check failed: {type(e).__name__}"}
    r["why"] = failed or r.get("why")
    for k in ("seconds_ours", "seconds_ref", "ratio", "f1"):
        if r.get(k) is not None:
            r[k] = round(r[k], 4)
    if secret:
        return {k: r[k] for k in PUBLIC if k in r}
    return {**r, "fallback": info.get("fallback"), "shape": t.get("shape"), "question": t["question"],
            "sparql_ours": ours, "sparql_ref": t["reference"]}


def _secs(r, side):
    return f">{r.get(f'timeout_{side}') or TIMEOUT:g}s" if r.get(f"status_{side}") == "timeout" else \
        "-" if r.get(f"seconds_{side}") is None else f"{r[f'seconds_{side}']:.3f}s"


def line(r):
    """id, path; correct: category, F1, rows ours/reference; valid; fast: seconds ours/reference. Why a query is not
    valid, untestable or not compared only where the item keeps text (never for the clean set)."""
    f1_ = "-" if r.get("f1") is None else f"{r['f1']:.2f}"
    rows = "/".join("-" if r.get(f"rows_{s}") is None else str(r[f"rows_{s}"]) for s in ("ours", "ref"))
    ok = {True: "yes", False: "NO"}.get(r.get("valid"), "-")
    return (f"{r['id']:<12} {r['path']:<4} {r['category']:<14} F1 {f1_:<4} rows {rows:<11} valid {ok:<3} "
            f"time {_secs(r, 'ours')}/{_secs(r, 'ref')}" + ("  SLOW" if r.get("slow") else "") +
            (f"  ({r['invalid'][:120]})" if r.get("invalid") else "") +
            (f"  [{r['why'][:160]}]" if r.get("why") and r["category"] in ("untestable", "ref_untestable", "not_compared") else ""))


def summary(rows, a, terms=None):
    """Correct, valid, fast; then which path wrote the queries, how many needed the 2024.2 names, the caches, the
    settings."""
    n = Counter(r["category"] for r in rows)
    done = [r for r in rows if r["category"] in ("match", "mismatch")]
    f1s = [r["f1"] for r in done]
    ours = [r["seconds_ours"] for r in rows if r.get("seconds_ours") is not None]
    refs = [r["seconds_ref"] for r in rows if r.get("seconds_ref") is not None]
    timed = [r for r in rows if r.get("ratio") is not None]
    slowest = sorted((r for r in rows if r.get("seconds_ours") is not None or r.get("status_ours") == "timeout"),
                     key=lambda r: (r.get("status_ours") != "timeout", -(r.get("seconds_ours") or 0)))[:3]
    mean = lambda xs, f="{:.3f}": f.format(statistics.mean(xs)) if xs else "-"
    median = lambda xs, f="{:.3f}": f.format(statistics.median(xs)) if xs else "-"

    def ids(c):  # with the terms the data lacks, or why not compared, where the item keeps text
        more = lambda r: r.get("absent_ref" if c == "ref_untestable" else "absent_ours") if c.endswith("untestable") \
            else [r["why"]] if c == "not_compared" and r.get("why") else None
        return ", ".join(r["id"] + (f" ({', '.join(m)})" if (m := more(r)) else "") for r in rows if r["category"] == c) or "-"
    bad = [r for r in rows if r.get("valid") is False]
    paths = "; ".join(f"{p} {k} (" + ", ".join(f"{c} {v}" for c, v in Counter(r["category"] for r in rows if r["path"] == p)
                                               .most_common()) + ")" for p, k in Counter(r["path"] for r in rows).most_common())
    kept = sum(r.get("ref_cached") is True for r in rows), sum("ref_cached" in r for r in rows)
    return "\n".join([
        f"set             {a.set}, {len(rows)} items",
        "CORRECT         " + ", ".join(f"{c} {n[c]}" for c in CATEGORIES),
        f"match rate      {n['match']}/{len(done)}" + (f" = {n['match'] / len(done):.1%}" if done else "") +
        " of the comparable items (match / (match + mismatch))",
        f"mean F1         {mean(f1s)} over match + mismatch, "
        f"{mean(f1s + [0.0] * (n['ours_error'] + n['ours_timeout']))} counting our errors and timeouts as 0",
        "mismatch        " + (", ".join(f"{r['id']} (F1 {r['f1']:.2f})" for r in rows if r["category"] == "mismatch") or "-"),
        *[f"{c:<15} {ids(c)}" for c in CATEGORIES if c not in ("match", "mismatch") and n[c]],
        f"VALID           {sum(r.get('valid') is True for r in rows)}/{len(rows)} of our queries parse and name only SPHN "
        f"2026.1 terms" + ("; not valid: " + ", ".join(r["id"] + (f" ({r['invalid'][:100]})" if r.get("invalid") else "")
                                                       for r in bad) if bad else ""),
        f"FAST            median seconds ours {median(ours)} over the {len(ours)} that ran within {a.timeout:g} s, "
        f"reference {median(refs)} over {len(refs)}; median ratio ours/reference "
        f"{median([r['ratio'] for r in timed], '{:.2f}')} over {len(timed)} items run on both sides",
        "slowest of ours " + (", ".join(f"{r['id']} {_secs(r, 'ours')}" for r in slowest) or "-"),
        f"slow            " + (", ".join(f"{r['id']} ({_secs(r, 'ours')}, x{r['ratio']:.1f})" if r.get("ratio") else
                                         f"{r['id']} ({_secs(r, 'ours')})" for r in rows if r.get("slow")) or "-") +
        f" (ours over {SLOW_RATIO}x the reference or over {SLOW_SECONDS} s)",
        f"path            {paths}",
        f"mapped to 2024.2 ours {sum(bool(r.get('mapped_ours')) for r in rows)}/{len(rows)}, "
        f"references {sum(bool(r.get('mapped_ref')) for r in rows)}/{len(rows)}",
        f"caches          references kept from an earlier run {kept[0]}/{kept[1]}" +
        (f"; sphn: terms not in the mock data {len(terms['absent'])}/{len(terms['present']) + len(terms['absent'])} "
         f"(asked {terms['date']})" if terms else ""),
        f"settings        runs={a.runs} timeout={a.timeout:g}s reference_timeout={TIMEOUT}s workers={a.workers} "
        f"holdout={S.HOLDOUT} plan={S.PLAN} grammar={S.GRAMMAR} model={S.LLM_MODEL} plan_model={S.PLAN_MODEL} "
        f"candidates={S.PLAN_CANDIDATES}"])


def benchmark(items, a, refs=None, absent=frozenset(), trace=None):
    """Every item translated first (translate_all, a.workers at a time), then checked one at a time in the item order,
    each line printed (and written to trace) as it comes: the rows."""
    secret = a.set == "clean"
    done = translate_all(items, a.workers)
    if items and all(d["failed"] for d in done.values()):
        raise SystemExit("every translation failed (is the model server up?): no query run")
    rows = []
    for t in items:
        rows.append(one(t, a.runs, secret, a.timeout, refs, absent, done[t["id"]]))
        print(line(rows[-1]), flush=True)
        if trace:
            trace.write(json.dumps(rows[-1], ensure_ascii=False) + "\n")
            trace.flush()
    return rows


def options(argv=None):
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("--set", choices=("bench", "clean", "easy", "fresh"), required=True)
    a.add_argument("--runs", type=int, default=3, help="timed runs of our query (the median is kept)")
    a.add_argument("--timeout", type=float, default=TIMEOUT, help=f"our query's limit in seconds (the reference's: {TIMEOUT})")
    a.add_argument("--workers", type=int, default=WORKERS, help="requests translated at a time")
    a.add_argument("--ids", nargs="+", help="only these items")
    a.add_argument("--out", type=Path, help="folder for summary.txt and trace.jsonl (clean set: no text in either)")
    a.add_argument("--no-holdout", action="store_true", help="let a request see its own worked example in the bank")
    a.add_argument("--refresh-terms", action="store_true", help="ask again which sphn: terms the mock data has")
    a.add_argument("--refresh-refs", action="store_true", help="run the reference queries again")
    return a.parse_args(argv)


def main(argv=None):
    a = options(argv)
    S.HOLDOUT = not a.no_holdout
    secret = a.set == "clean"
    pool = clean() if secret else bench(EASY) if a.set == "easy" else bench(FRESH) if a.set == "fresh" else bench()
    items = [t for t in pool if not a.ids or t["id"] in a.ids]
    try:
        execute("ASK {}", 10)
    except Exception as e:
        raise SystemExit(f"{S.SPARQL_ENDPOINT} does not answer ({type(e).__name__}): not running")
    terms = data_terms(a.refresh_terms)
    print(f"terms: {len(terms['absent'])} of {len(terms['present']) + len(terms['absent'])} sphn: terms are not in the "
          f"mock data (asked {terms['date']})" + (f"; {len(terms['failed'])} ASKs failed" if terms["failed"] else ""),
          file=sys.stderr)
    refs = References(refresh=a.refresh_refs, secret=secret)
    from ..translation import warm_up
    warm_up()
    trace = None
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        trace = open(a.out / "trace.jsonl", "w")
    try:
        rows = benchmark(items, a, refs, frozenset(terms["ns"] + n for n in terms["absent"]), trace)
    finally:
        if trace:
            trace.close()
    text = summary(rows, a, terms)
    print(text)
    if a.out:
        (a.out / "summary.txt").write_text(text + "\n")


if __name__ == "__main__":
    main()
