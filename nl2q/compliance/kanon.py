"""k-anonymity on a compliant query (NL2Q_KANON, off by default): no released group may describe fewer than K patients.
Group size is counted in distinct patients (the named graph ?g, or what carries it: an alias such as the official
(?g AS ?pseudo_id), a SubjectPseudoIdentifier node), never in the values the query returns: a COUNT of 15 diagnoses
can be one patient, and an AVG over one patient is that patient's value.
- flag: run the query with one more column, COUNT(DISTINCT <patient>), and refuse it when a released group has
  1 to K-1 patients. If the run fails or times out, the rules' verdict stands.
- rewrite: add HAVING (COUNT(DISTINCT <patient>) >= K) to the outer query, the level that aggregates, so Virtuoso drops
  the small groups. An ungrouped aggregate is one group; Virtuoso accepts HAVING only after a GROUP BY, so it also
  gets GROUP BY ("all"). The rewrite must parse and stay compliant under judge(), or the query is left as it was.
The patient variable must be bound on every row the outer level aggregates (not only in an OPTIONAL branch, not
through an IF). When there is none, or two that may be different patients, the query is `unsupported`: left
unchanged, never guessed."""
import logging, re
from rdflib import RDF, Variable
from rdflib.plugins.sparql.parserutils import CompValue
from .. import schema as SC
from .. import settings as S
from .rules import Invalid, _steps, _walk, parse
from .verdict import judge

log = logging.getLogger(__name__)
RULE = "groups smaller than {k} patients may identify a patient"
COLUMN = "kanon_patients"  # the column flag mode adds to count the patients of each released group
_IRI = re.compile(r'<[^<>"{}|^`\\\x00-\x20]*>')
_STRING = re.compile(r'"""(?:[^"\\]|\\.|"(?!""))*"""|\'\'\'(?:[^\'\\]|\\.|\'(?!\'\'))*\'\'\'|"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'', re.S)


def _kw(word):
    """A SPARQL keyword, not part of a name (sql:select-option, sphn:hasGroup, ?order)."""
    return re.compile(rf"(?<![\w:?$-]){word}(?![\w:-])", re.I)


SELECT, MODIFIERS = _kw("SELECT"), {w: _kw(w) for w in ("GROUP", "HAVING", "ORDER", "LIMIT", "OFFSET", "VALUES")}
DISTINCT = re.compile(r"\s*(?:DISTINCT|REDUCED)(?![\w:-])", re.I)


# --- the patient variable at the level that aggregates ---------------------------------------------------------------

def _merge(classes):
    """Join the sets of variables that share a member: they denote the same patient."""
    out = []
    for c in map(set, classes):
        for o in [o for o in out if o & c]:
            c |= o
            out.remove(o)
        out.append(c)
    return out


def _bound(n):
    """(variables bound on every solution of pattern n, classes of those that carry one patient). Unknown shapes bind
    nothing: the query then has no patient variable and is unsupported."""
    if not isinstance(n, CompValue):
        return set(), []
    name = n.name
    if name == "BGP":
        vs = {x for t in n["triples"] for x in t if isinstance(x, Variable)}
        pats = set()
        for s, p, o in n["triples"]:
            if isinstance(p, Variable):
                continue
            first, last, _ = _steps(p)
            if SC.LINK in last and isinstance(o, Variable):
                pats.add(o)  # one SubjectPseudoIdentifier per patient graph (EXPLORE.md)
            if RDF.type in first and o == SC.PATIENT_CLASS and isinstance(s, Variable):
                pats.add(s)
        return vs, [{x} for x in pats]
    if name == "Graph":
        vs, cls = _bound(n["p"])
        if isinstance(n["term"], Variable):  # a patient file; the patient nodes inside it are the same patient
            g = n["term"]
            inner = {x["term"] for x in _walk(n["p"]) if isinstance(x, CompValue) and x.name == "Graph"}
            same = [c for c in cls if not c & inner]  # a nested GRAPH ?h may be another patient's file
            return vs | {g}, [{g} | set().union(*same)] + [c for c in cls if c & inner]
        return vs, cls
    if name == "Join":
        (v1, c1), (v2, c2) = _bound(n["p1"]), _bound(n["p2"])
        return v1 | v2, _merge(c1 + c2)
    if name in ("LeftJoin", "Minus"):
        return _bound(n["p1"])
    if name == "Union":
        (v1, c1), (v2, c2) = _bound(n["p1"]), _bound(n["p2"])
        both = v1 & v2
        return both, _merge([a & b & both for a in c1 for b in c2 if a & b & both])
    if name == "Extend":
        vs, cls = _bound(n["p"])
        e = n["expr"]
        if isinstance(e, Variable) and e in vs:  # BIND(?g AS ?p): an alias; anything else may be unbound or not a patient
            vs = vs | {n["var"]}
            cls = [c | {n["var"]} if e in c else c for c in cls]
        return vs, cls
    if name in ("Filter", "Distinct", "Reduced", "OrderBy", "Slice", "Group", "ToMultiSet"):
        return _bound(n["p"])
    if name == "values":
        rows = n["res"]
        return (set.intersection(*(set(r) for r in rows)) if rows else set()), []
    if name == "Project":
        vs, cls = _bound(n["p"])
        pv = set(n["PV"])
        return vs & pv, [c & pv for c in cls if c & pv]
    if name == "AggregateJoin":  # a subquery that aggregates: its group keys come out as SAMPLE(key)
        group = n["p"]
        vs, cls = _bound(group["p"])
        keys = {k for k in (group.get("expr") or []) if isinstance(k, Variable)} & vs
        out = {a["res"]: a["vars"] for a in n["A"] if a.name == "Aggregate_Sample" and a["vars"] in keys}
        return set(out), [{r for r, k in out.items() if k in c} for c in cls if keys & c and any(k in c for k in out.values())]
    return set(), []


def patient(query):
    """(patient variable of the outer aggregation or None, why not). Raises Invalid."""
    q, _ = parse(query)
    if q is None or q.algebra.name != "SelectQuery":
        return None, "not a SELECT query"
    n = q.algebra["p"]
    while isinstance(n, CompValue) and n.name in ("Project", "Distinct", "Reduced", "OrderBy", "Slice", "Filter", "Extend"):
        n = n.get("p")  # down the outer query only: never into a subquery
    if not isinstance(n, CompValue) or n.name != "AggregateJoin":
        return None, "the outer query does not aggregate"
    _, cls = _bound(n["p"]["p"])
    cls = [c for c in cls if c]
    if not cls:
        return None, "no patient variable bound on every row the outer query aggregates"
    if len(cls) > 1:
        return None, f"several patient variables that may be different patients ({', '.join(sorted('?' + str(min(c, key=str)) for c in cls))})"
    pick = sorted(cls[0], key=lambda v: (str(v).startswith("__"), str(v) != "g", str(v)))[0]  # ?g if there, else by name
    if str(pick).startswith("__") or not re.search(rf"[?$]{re.escape(str(pick))}(?![\w])", query):
        return None, "the patient variable is not written in the outer query"
    return pick, None


# --- where to write: the outer query in the text ---------------------------------------------------------------------

def _mask(q):
    """The query with comments, strings and IRIs blanked (same length), so braces and keywords can be found in it."""
    out, i = [], 0
    while i < len(q):
        c = q[i]
        m = _STRING.match(q, i) if c in "\"'" else _IRI.match(q, i) if c == "<" else None
        if m:
            out.append(c + " " * (m.end() - i - 2) + q[m.end() - 1])
            i = m.end()
        elif c == "#":
            j = q.find("\n", i)
            j = len(q) if j < 0 else j
            out.append(" " * (j - i))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _outer(q):
    """(end of SELECT [DISTINCT], {modifier: offset} after the outer WHERE, end of the query text), on the masked text."""
    m = _mask(q)
    depth, sel = 0, None
    for i, c in enumerate(m):
        depth += {"{": 1, "}": -1}.get(c, 0)
        if depth == 0 and (s := SELECT.match(m, i)):
            sel = s.end()
            break
    if sel is None:
        raise Invalid("no outer SELECT")
    sel += (d.end() - sel) if (d := DISTINCT.match(m, sel)) else 0
    paren, open_ = 0, None
    for i in range(sel, len(m)):
        paren += {"(": 1, ")": -1}.get(m[i], 0)
        if m[i] == "{" and paren == 0:
            open_ = i
            break
    if open_ is None:
        raise Invalid("no WHERE clause")
    depth, close = 0, None
    for i in range(open_, len(m)):
        depth += {"{": 1, "}": -1}.get(m[i], 0)
        if depth == 0:
            close = i + 1
            break
    if close is None:
        raise Invalid("unbalanced braces")
    found, paren = {}, 0
    for i in range(close, len(m)):
        paren += {"(": 1, ")": -1}.get(m[i], 0)
        if paren == 0:
            for w, rx in MODIFIERS.items():
                if w not in found and rx.match(m, i):
                    found[w] = i
    return sel, found, len(m.rstrip())


# --- the two modes ---------------------------------------------------------------------------------------------------

def rewrite(query, k=None):
    """(query with HAVING (COUNT(DISTINCT ?patient) >= k) at the outer level, None) or (query unchanged, why not)."""
    k = S.K if k is None else k
    try:
        pv, why = patient(query)
        if pv is None:
            return query, f"unsupported: {why}"
        _, found, end = _outer(query)
    except Invalid as e:
        return query, f"unsupported: {e}"
    cond = f"(COUNT(DISTINCT ?{pv}) >= {k})"
    if "HAVING" in found:  # one more constraint after the query's own ones' keyword: all must hold
        at = found["HAVING"] + len("HAVING")
        new = f"{query[:at]} {cond}{query[at:]}"
    else:
        add = ("" if "GROUP" in found else 'GROUP BY ("all") ') + f"HAVING {cond}"
        at = min([found[w] for w in ("ORDER", "LIMIT", "OFFSET", "VALUES") if w in found], default=end)
        new = f"{query[:at]}{add}\n{query[at:]}" if at < end else f"{query[:at]}\n{add}{query[at:]}"
    if (r := judge(new)) is not None:  # never return a query the rules would refuse
        return query, f"rewrite rejected by the rules: {r}"
    return new, None


def measure_query(query):
    """(the query with one more column, COUNT(DISTINCT ?patient) + 0 AS ?kanon_patients, its name) or (None, why
    not)."""
    try:
        pv, why = patient(query)
        if pv is None:
            return None, f"unsupported: {why}"
        sel, _, _ = _outer(query)
    except Invalid as e:
        return None, f"unsupported: {e}"
    col = COLUMN
    while re.search(rf"[?$]{col}(?!\w)", query):
        col += "_"
    # + 0: Virtuoso fails (HTTP 500) on an ungrouped SELECT with the same aggregate twice, e.g. the query's own
    return f"{query[:sel]} (COUNT(DISTINCT ?{pv}) + 0 AS ?{col}){query[sel:]}", col


def group_sizes(query, run_fn=None, timeout=None):
    """Distinct patients of each group the query releases, from one run of the measuring query. Raises on failure."""
    from .. import store
    mq, col = measure_query(query)
    if mq is None:
        raise ValueError(col)
    cols, rows = (run_fn or store.run)(mq, timeout=S.KANON_TIMEOUT if timeout is None else timeout)
    i = cols.index(col)
    return [int(float(r[i])) if r[i] is not None else 0 for r in rows]


def flag(query, run_fn=None, k=None):
    """compliance_reason when a released group has 1 to k-1 distinct patients, else None (also when the query cannot be
    measured: the rules' verdict stands)."""
    k = S.K if k is None else k
    try:
        sizes = group_sizes(query, run_fn)
    except Exception as e:  # unsupported shape, timeout, Virtuoso error
        log.warning(f"k-anonymity not checked ({type(e).__name__}: {str(e)[:200]}): the rules' verdict stands")
        return None
    small = [n for n in sizes if 0 < n < k]
    if not small:
        return None
    return (f"{RULE[0].upper()}{RULE[1:].format(k=k)}: {len(small)} of the {len(sizes)} groups the query returns "
            f"{'has' if len(small) == 1 else 'have'} fewer than {k} patients (the smallest has {min(small)}).")


def kanon(query, run_fn=None):
    """(sparql_query, compliance_reason) for a query the rules found compliant, by NL2Q_KANON (off|flag|rewrite)."""
    if S.KANON == "flag":
        return query, flag(query, run_fn)
    if S.KANON == "rewrite":
        new, why = rewrite(query)
        if why:
            log.info(f"k-anonymity rewrite skipped, query unchanged: {why}")
        return new, None
    return query, None
