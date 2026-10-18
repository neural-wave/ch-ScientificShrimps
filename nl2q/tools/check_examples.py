"""Check the worked examples (nl2q/translation/resources/examples.md) one by one, and explain them in plain words.
  python -m nl2q.tools.check_examples                    # automatic checks; exit status 1 if any example fails
  python -m nl2q.tools.check_examples --virtuoso         # + compile each query on Virtuoso (nothing runs), look codes up
  python -m nl2q.tools.check_examples --run              # + run the compliant queries on the mock data (slow on 100k patients)
  python -m nl2q.tools.check_examples --explain org-03   # what a query does, in plain words ("all" for every example)
  python -m nl2q.tools.check_examples --pack             # one file to review the examples with another person or model
Checks: fields (labels well formed), valid (parses, as the API checks it), terms (SPHN 2026.1 terms only), links (each
property used on a record type that has it in the 2026.1 schema, read from NL2Q_SCHEMA_TTL), inference (an ICD-10-GM or
CHOP code class needs OPTION (inference ...): the data codes them by year), style (no PREFIX/DEFINE lines; compliant
queries go through the patient graphs), verdict (the compliance check agrees with the label).
Code labels come from restricted terminologies: --labels prints them on your screen only, never paste them elsewhere."""
import argparse, html, json, os, re, sys, time
from collections import defaultdict
from pathlib import Path
from rdflib import OWL, RDF, RDFS, Graph, URIRef, Variable
from rdflib.collection import Collection
from rdflib.paths import SequencePath
from rdflib.plugins.sparql.parserutils import CompValue
from ..compliance import rules as C
from .. import schema as SC
from .. import settings as S
from ..translation.examples import load
from ..translation.prompt import finish

SCHEMA_TTL = Path(os.getenv("NL2Q_SCHEMA_TTL", "/data/scratch/nw-data/virtuoso-release/ontologies/sphn_rdf_schema.ttl"))
FIELDS = ("id", "family", "topic", "kind", "compliant", "rule", "question", "sparql")
BY_YEAR = ("https://biomedit.ch/rdf/sphn-resource/icd-10-gm/", "https://biomedit.ch/rdf/sphn-resource/chop/")
TYPED = re.compile(r"(?:\ba\b|rdf:type)\s+(<[^>\s]+>|[A-Za-z][\w-]*:[\w.\-]*\w)\s*(OPTION\s*\(\s*inference)?", re.I)
SCHEMA_NS = (SC.NS, SC.PREFIXES["sphn-individual"])




def short(u):
    s = str(u)
    for p, ns in sorted(SC.PREFIXES.items(), key=lambda kv: -len(kv[1])):
        if s.startswith(ns) and re.fullmatch(r"[\w.\-]*", s[len(ns):]):
            return f"{p}:{s[len(ns):]}"
    return f"<{s}>"


def expand(t):
    if t.startswith("<"):
        return t[1:-1]
    p, _, rest = t.partition(":")
    return SC.PREFIXES.get(p, p + ":") + rest


def label(c):
    """A record type's name as the schema index has it, else its local name split into words."""
    m = re.search(re.escape(short(c)) + r" \(([^)]*)\)", SC.CFG["index"])
    return m.group(1) if m else re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(c).split("#")[-1])


class Schema:
    """The 2026.1 record types: which properties each has (own, inherited, or on a subtype) and where they lead."""

    def __init__(self, path):
        g = Graph()
        g.parse(path)
        mine = lambda u: isinstance(u, URIRef) and str(u).startswith(SC.NS)
        union = lambda n: list(Collection(g, g.value(n, OWL.unionOf))) if g.value(n, OWL.unionOf) is not None else [n]
        self.domain, self.range = defaultdict(set), defaultdict(set)
        props = set(g.subjects(RDF.type, OWL.ObjectProperty)) | set(g.subjects(RDF.type, OWL.DatatypeProperty))
        for p in props:
            for d in g.objects(p, RDFS.domain):
                self.domain[p] |= {c for c in union(d) if mine(c)}
            for r in g.objects(p, RDFS.range):
                self.range[p] |= {c for c in union(r) if mine(c) and str(c).split("#")[-1] not in ("Code", "Terminology")}
        uses = defaultdict(int)
        for p in props:
            for c in self.domain[p]:
                uses[c] += 1
        roots = {c for c, n in uses.items() if n > 0.5 * len(props)}  # sphn:SPHNConcept: in every domain, says nothing
        for p in props:
            self.domain[p] -= roots
        up, down = defaultdict(set), defaultdict(set)
        for c, sup in g.subject_objects(RDFS.subClassOf):
            if mine(c) and mine(sup):
                up[c].add(sup), down[sup].add(c)
        close = lambda rel, c, seen=None: set().union(*({x} | close(rel, x, (seen or set()) | {c}) for x in rel[c] if x not in (seen or set())))
        self.kin = defaultdict(set, {c: {c} | close(up, c) | close(down, c) for c in set(up) | set(down)})

    def has(self, c, p):
        return not self.domain[p] or bool(self.domain[p] & (self.kin[c] or {c}))


def triples(query):
    for n in C._walk(query.algebra):
        if isinstance(n, CompValue) and n.name == "BGP":
            yield from n["triples"]


def values(query):
    for n in C._walk(query.algebra):
        if isinstance(n, CompValue) and n.name == "values":
            for row in n["res"]:
                yield from row.values()


def links(query, schema):
    """Properties used on a record type that does not have them (classes known from rdf:type and property ranges)."""
    ts = list(triples(query))
    cls = defaultdict(set)
    for s, p, o in ts:
        if p == RDF.type and isinstance(o, URIRef) and str(o).startswith(SC.NS):
            cls[s].add(o)
    steps = lambda p: [p] if isinstance(p, URIRef) else list(p.args) if isinstance(p, SequencePath) and all(isinstance(a, URIRef) for a in p.args) else []
    issues = []
    for _ in range(4):  # follow ranges along chains of untyped variables
        for s, p, o in ts:
            cur = set(cls[s])
            for step in steps(p):
                if not cur or not str(step).startswith(SC.NS):
                    break
                cur = schema.range[step]
            if isinstance(o, Variable) and cur and not cls[o] and len(cur) <= 3:
                cls[o] |= cur
    for s, p, o in ts:
        cur = set(cls[s])
        for step in steps(p):
            if not cur or not str(step).startswith(SC.NS):
                break
            if not any(schema.has(c, step) for c in cur):
                issues.append(f"{short(step)} is not a property of {' / '.join(sorted(map(short, cur)))}")
            cur = schema.range[step]
    return sorted(set(issues))


def codes(query):
    """Code classes and value-set individuals the query names (not record types, not graphs)."""
    out = set()
    for s, p, o in triples(query):
        last = p if isinstance(p, URIRef) else p.args[-1] if isinstance(p, SequencePath) else None
        if last == RDF.type and isinstance(o, URIRef) and not str(o).startswith(SC.NS):
            out.add(o)
        elif isinstance(o, URIRef) and str(o).startswith(SC.PREFIXES["sphn-individual"]):
            out.add(o)
    return out | {v for v in values(query) if isinstance(v, URIRef) and not str(v).startswith(SC.NS)}


def check(e, schema=None):
    """[(check, problem)] for one example; empty means it passes."""
    bad = [("fields", f"missing {f}") for f in FIELDS if f not in e]
    if e.get("rule") not in (None, *C.RULES) or e.get("compliant") != (e.get("rule") is None):
        bad.append(("fields", f"compliant={e.get('compliant')} does not fit rule={e.get('rule')}"))
    body = e.get("sparql", "")
    if re.search(r"(?im)^\s*(PREFIX|DEFINE)\b", body):
        bad.append(("style", "store the query without PREFIX/DEFINE lines (finish adds them)"))
    if e.get("compliant") and not (e.get("source") or "").startswith("docs/official") and not re.search(r"GRAPH\s+\?", body, re.I):
        bad.append(("style", "a compliant query should match GRAPH ?g, one graph per patient"))
    for m in TYPED.finditer(body if e.get("compliant") else ""):  # non-compliant examples never run
        iri = expand(m.group(1))
        if iri.startswith(BY_YEAR) and not re.search(r"/\d{4}/", iri) and not m.group(2):
            bad.append(("inference", f"{m.group(1)} without OPTION (inference ...) never matches: the data codes it by year"))
    query = finish(body)
    try:
        violations, unknown = C.analyse(query)
        parsed, _ = C.parse(query)
    except C.Invalid as x:
        return bad + [("valid", str(x)[:200])]
    bad += [("terms", f"not in SPHN 2026.1: {short(t)}") for t in sorted(unknown)]
    rules = {r for r, _ in violations}
    if e.get("compliant") and violations:
        bad.append(("verdict", f"labelled compliant, the check says: {C.reason(violations)}"))
    elif not e.get("compliant") and e.get("rule") not in rules:
        bad.append(("verdict", f"labelled {e.get('rule')}, the check finds {sorted(rules) or 'nothing'}"))
    if schema and parsed is not None:
        bad += [("links", x) for x in links(parsed, schema)]
    return bad


def compile_error(query):
    """Virtuoso's own compiler on the query (explain=on: compiled, never run)."""
    import requests
    r = requests.post(S.SPARQL_ENDPOINT, data={"query": query, "explain": "on"}, timeout=120)
    m = re.search(r"ERROR \w+: [^\n]{0,200}", html.unescape(re.sub(r"<[^>]+>", " ", r.text)))
    return m.group(0).strip() if m else None if r.ok else f"HTTP {r.status_code}"


def missing_codes(iris):
    """The code classes and individuals that are not in the ontology graph (schema + terminologies)."""
    from ..store import run
    if not iris:
        return set()
    vals = " ".join(f"<{i}>" for i in sorted(iris))
    _, rows = run(f"SELECT DISTINCT ?c WHERE {{ VALUES ?c {{ {vals} }} GRAPH <{S.INFERENCE}> {{ ?c ?p ?o }} }}")
    return set(map(str, iris)) - {r[0] for r in rows}


def run_mock(e, timeout):
    """(rows, seconds, all numbers zero) of the version that runs on the mock data (SPHN 2024.2)."""
    from ..store import run
    t0 = time.perf_counter()
    _, rows = run(finish(e.get("sparql_mock") or e["sparql"]), timeout=timeout)
    nums = [float(v) for r in rows for v in r if v is not None and re.fullmatch(r"-?\d+(\.\d+)?(e-?\d+)?", v)]
    return len(rows), time.perf_counter() - t0, bool(nums) and not any(nums)


# ---- plain words -------------------------------------------------------------------------------------------------

def _outer_select(body):
    """(projection text, outer GROUP BY text) of the top-level query."""
    m = re.search(r"\bSELECT\b(.*?)(?=\bWHERE\b|\{)", body, re.S | re.I)
    depth, groups = 0, None
    for tok in re.finditer(r"[{}]|\bGROUP\s+BY\b([^\n{}]*)", body, re.I):
        if tok.group(0) == "{":
            depth += 1
        elif tok.group(0) == "}":
            depth -= 1
        elif depth == 0:
            groups = re.split(r"\b(?:ORDER|HAVING|LIMIT)\b", tok.group(1), flags=re.I)[0]
    return (m.group(1) if m else ""), groups


def _items(proj):
    """Projected items: ('?v', None) or ('?v', expression)."""
    out, i = [], 0
    while i < len(proj):
        if proj[i] == "(":
            depth, j = 0, i
            while j < len(proj):
                depth += {"(": 1, ")": -1}.get(proj[j], 0)
                if depth == 0:
                    break
                j += 1
            inner = proj[i + 1:j]
            m = re.match(r"(.*)\bAS\s+\?(\w+)\s*$", inner, re.S | re.I)
            out.append((m.group(2), re.sub(r"\s+", " ", m.group(1)).strip()) if m else ("?", inner))
            i = j + 1
        elif proj[i] == "?":
            m = re.match(r"\?(\w+)", proj[i:])
            out.append((m.group(1), None))
            i += len(m.group(0))
        else:
            i += 1
    return out


def words(v):
    return v.replace("_", " ")


def _says(expr):
    e = expr.strip()
    if re.fullmatch(r"COUNT\s*\(\s*(DISTINCT\s+)?\?(g|pseudo_id)\s*\)", e, re.I):
        return "number of patients"
    if m := re.fullmatch(r"COUNT\s*\(\s*DISTINCT\s+\?(\w+)\s*\)", e, re.I):
        return f"number of different {words(m.group(1))}"
    if re.fullmatch(r"COUNT\s*\(\s*\*\s*\)", e, re.I):
        return "number of matching rows"
    if m := re.fullmatch(r"COUNT\s*\(\s*\?(\w+)\s*\)", e, re.I):
        return f"number of {words(m.group(1))} (repeats counted)"
    for fn, what in (("AVG", "average"), ("MIN", "lowest / earliest"), ("MAX", "highest / latest"), ("SUM", "total"),
                     ("SAMPLE", "one example value (CHECK: not a total)"), ("GROUP_CONCAT", "list of values joined as text (CHECK)")):
        if m := re.fullmatch(rf"(?:ROUND\s*\()?\s*{fn}\s*\((.*)\)\s*\)?", e, re.I | re.S):
            return f"{what} of {words(m.group(1).strip())}"
    parts = [_says(a) for a in re.findall(r"\b(?:COUNT|AVG|MIN|MAX|SUM)\s*\((?:[^()]|\([^()]*\))*\)", e, re.I) if a != e]
    return "computed from: " + " ; ".join(dict.fromkeys(parts)) if parts else f"computed: {e}"


def _filters(body):
    out = []
    for m in re.finditer(r"\bFILTER\s*(NOT\s+EXISTS|EXISTS)?\s*([({])", body, re.I):
        open_, close = m.group(2), {"(": ")", "{": "}"}[m.group(2)]
        depth, j = 0, m.end() - 1
        while j < len(body):
            depth += {open_: 1, close: -1}.get(body[j], 0)
            if depth == 0:
                break
            j += 1
        out.append(re.sub(r"\s+", " ", body[m.start():j + 1]))
    return out


def explain(e, labels=None):
    body, lines = e["sparql"], []
    kind = re.search(r"\b(SELECT|CONSTRUCT|ASK|DESCRIBE|DELETE|INSERT|CLEAR|DROP|LOAD)\b", body, re.I)
    lines.append(f"Query type: {kind.group(1).upper() if kind else '?'}")
    fixed = re.findall(r"GRAPH\s+<(http://example\.org/graph/[^>]+)>", body)
    lines.append("Patients: " + (f"ONE patient only ({', '.join(fixed)})" if fixed else
                                 "each patient's graph (GRAPH ?g)" if re.search(r"GRAPH\s+\?", body) else "no per-patient graph"))
    proj, groups = _outer_select(body)
    group_vars = re.findall(r"\?(\w+)", groups or "")
    lines.append("Returns:")
    for v, expr in _items(proj) or [("*", None)]:
        if expr is not None:
            lines.append(f"  - {words(v)}: {_says(expr)}")
        elif v in group_vars:
            lines.append(f"  - {words(v)}: the group (one row per {words(v)})")
        else:
            lines.append(f"  - {words(v)}: a value per row (CHECK: not a total)")
    if group_vars:
        lines.append(f"Grouped by: {', '.join(map(words, group_vars))}")
    try:
        parsed, _ = C.parse(finish(body))
    except C.Invalid as x:
        return "\n".join(lines + [f"NOT VALID: {x}"])
    if parsed is not None:
        types = sorted({o for s, p, o in triples(parsed) if p == RDF.type and isinstance(o, URIRef) and str(o).startswith(SC.NS)}, key=str)
        lines.append("Record types: " + (", ".join(f"{label(c)}" for c in types) or "none"))
        found = sorted(codes(parsed), key=str)
        inferred = {expand(m.group(1)) for m in TYPED.finditer(body) if m.group(2)}
        names = labels(found) if labels else {}
        lines.append("Codes: " + (", ".join(f"{short(c)}{' (and its sub-codes)' if str(c) in inferred else ''}"
                                          f"{' = ' + names[str(c)] if str(c) in names else ''}" for c in found) or "none"))
    for f in _filters(body):
        lines.append(f"Condition: {f[:180]}")
    if re.search(r"\bOPTIONAL\b", body):
        lines.append(f"Optional parts: {len(re.findall(r'\bOPTIONAL\b', body))} (rows are kept even when these are missing)")
    try:
        v, _ = C.analyse(finish(body))
        lines.append("Compliance check: " + (f"NOT compliant: {C.reason(v)}" if v else "compliant"))
    except C.Invalid as x:
        lines.append(f"Compliance check: not valid ({x})")
    return "\n".join(lines)


def code_labels(iris):
    """{code: labels} from the ontology graph in Virtuoso. Restricted content: for the reviewer's screen only."""
    from ..store import run
    if not iris:
        return {}
    vals = " ".join(f"<{i}>" for i in iris)
    _, rows = run(f"PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\nSELECT ?c (GROUP_CONCAT(DISTINCT STR(?l); separator=' | ') AS ?l) "
                  f"WHERE {{ VALUES ?c {{ {vals} }} GRAPH <{S.INFERENCE}> {{ ?c rdfs:label ?l }} }} GROUP BY ?c")
    return {c: l for c, l in rows}


PACK_INTRO = """# Worked examples: review pack

You review question -> SPARQL examples. A system translating researchers' questions into SPARQL over SPHN 2026.1
clinical data (Virtuoso 7.2.16) shows them to its model as worked examples, so each one must be right. For every
example below, check:
1. Meaning: the query answers exactly the question: no missing condition, no extra one.
2. Label: "compliant" or "not compliant (rule)" is right under the rules below. A not-compliant example must break
   the rule it names, and its question must really ask for that (it is translated literally, never refused).
3. Schema: SPHN 2026.1 classes and properties are used correctly, each property on a record type that has it
   (schema: https://www.biomedit.ch/rdf/sphn-schema/sphn).
4. Fairness: the question is something a researcher would plausibly ask, worded naturally.
Answer with one table row per example: id | OK / FIX / UNSURE | what is wrong | suggested fix. Be strict.

## Data model
- One named graph per patient: GRAPH ?g { ... }; patients are counted as distinct graphs (COUNT(DISTINCT ?g)).
- Codes are nodes typed with their terminology class: ?record sphn:hasCode ?code . ?code rdf:type icd-10-gm:E11
  OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global") . The inference option also matches sub-codes
  and every yearly version of ICD-10-GM and CHOP codes (the data codes them per year).
- Queries are stored without PREFIX/DEFINE lines (added before running). The mock data follows SPHN 2024.2: an
  example using 2026.1 terms the mock data lacks was run through an equivalent 2024.2 version.
"""


def pack(examples, path, runs=()):
    """One self-contained Markdown file to review the examples with another person or model: instructions, rules,
    conventions, then every example with its label, query, plain-words summary and run on the mock data."""
    ran = {}
    for f in runs:
        for line in Path(f).read_text().splitlines() if Path(f).exists() else []:
            if m := re.match(r"ok\s+(\S+)\s+(.+)", line):
                ran[m.group(1).replace("team-", "ex-")] = m.group(2).strip()
    rules = (S.HERE.parent / "docs" / "official" / "Query_Compliance.md").read_text().strip()
    conventions = (S.HERE / "translation" / "resources" / "conventions.md").read_text().strip()
    out = [PACK_INTRO, "## Compliance rules (official)", rules, "", "## Dataset conventions", conventions, "",
           "## Already checked by code, for every example",
           "Parses; only SPHN 2026.1 terms; each property used on a record type that has it; year-coded terminologies "
           "matched with the inference option; the compliance rules give the label stated; Virtuoso 7.2.16 compiles it; "
           "its codes exist in the loaded terminologies. Not checked by code: meaning and fairness, your part.", ""]
    for e in examples:
        label = "compliant" if e["compliant"] else f"NOT compliant, {e['rule']}: {e['reason']}"
        out += [f"## {e['id']} · {label} · {e['topic']} · {e['kind']}", f"Question: {e['question']}"]
        if e.get("original_question"):
            out.append(f"(official wording before our fix: {e['original_question']})")
        if e.get("changes"):
            out.append("Adapted from the official 2024.2 query: " + e["changes"])
        if e.get("note"):
            out.append(f"Open point: {e['note']}")
        out += ["```sparql", e["sparql"], "```", "What the query does (generated):", explain(e)]
        if e["id"] in ran:
            out.append(f"Run on the mock data: {ran[e['id']]}")
        out.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")
    return path


def main():
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("--file", default=S.EXAMPLES, type=Path, help="the examples file")
    a.add_argument("--virtuoso", action="store_true", help=f"compile on {S.SPARQL_ENDPOINT} and look the codes up")
    a.add_argument("--run", action="store_true", help="run compliant queries on the mock data (rows, seconds)")
    a.add_argument("--timeout", type=int, default=300, help="seconds per query with --run")
    a.add_argument("--explain", nargs="+", metavar="ID", help="plain-words summary of these examples ('all' for every one)")
    a.add_argument("--labels", action="store_true", help="with --explain: show code labels (restricted: your screen only)")
    a.add_argument("--pack", action="store_true", help=f"write a review pack for another reviewer: {S.DATA}/examples/review_pack.md")
    a = a.parse_args()
    examples = load(a.file)
    if a.pack:
        runs = [S.DATA / "examples" / "run-organisers.txt", S.DATA / "examples" / "run-team.txt"]
        print(f"review pack: {pack(examples, S.DATA / 'examples' / 'review_pack.md', runs)}")
        return
    if a.explain:
        for e in examples if "all" in a.explain else [x for x in examples if x["id"] in a.explain]:
            print(f"=== {e['id']} [{'compliant' if e['compliant'] else 'NOT compliant, ' + e['rule']}] {e['question']}\n"
                  f"{explain(e, code_labels if a.labels else None)}\n")
        return
    schema = Schema(SCHEMA_TTL) if SCHEMA_TTL.exists() else None
    if not schema:
        print(f"links check skipped: no schema file at {SCHEMA_TTL} (set NL2Q_SCHEMA_TTL)")
    ids = [e.get("id") for e in examples]
    failed = 0
    for e in examples:
        bad = check(e, schema) + [("fields", "duplicate id")] * (ids.count(e.get("id")) > 1)
        notes = []
        if a.virtuoso and not any(k == "valid" for k, _ in bad):
            if err := compile_error(finish(e["sparql"])):
                bad.append(("virtuoso", err))
            parsed, _ = C.parse(finish(e["sparql"]))
            if parsed is not None and (gone := missing_codes(codes(parsed))):
                bad.append(("codes", f"not in the terminologies: {', '.join(map(short, sorted(gone)))}"))
        if a.run and e.get("compliant") and not bad:
            try:
                rows, secs, zero = run_mock(e, a.timeout)
                notes.append(f"{rows} rows in {secs:.2f} s{' (all numbers 0: no matching data?)' if zero else ''}"
                             + (" [2024.2 version]" if "sparql_mock" in e else ""))
            except Exception as x:
                bad.append(("run", str(x)[:200]))
        failed += bool(bad)
        print(f"{'FAIL' if bad else 'ok  '} {e.get('id')}" + "".join(f"\n     {k}: {v}" for k, v in bad)
              + (f"  {'; '.join(notes)}" if notes else ""))
    print(f"\n{len(examples) - failed}/{len(examples)} examples pass" + (f", {failed} fail" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
