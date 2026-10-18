"""flag: an OPTIONAL { P } used only as a flag (its own variables appear outside it only as BOUND(?v)) is removed and each
BOUND(?v) becomes EXISTS { GRAPH ?g { P } } (the GRAPH of the OPTIONAL kept, P as written, OPTION (inference ...)
included). Static: it reads only the query, never the data's counts.
It fixes a double count rather than keeping the rows: the OPTIONAL repeats an outer row once per match of P, the EXISTS
keeps it once, so the rows change only where P matches twice for one outer row (O02 at 100k: 6,006,645 rows for the
cholera group with the OPTIONAL, 153,464 with EXISTS, the form of the organizers' reference). Timed at 100k by session
nw-3a: the generated O02 went from the 120 s cap to 46 s.
Anything unusual leaves the query unchanged: P with anything but triple patterns and FILTERs on its own variables, a
variable of P used outside other than in BOUND, a BOUND before the OPTIONAL or in another scope, SELECT *."""
import re
from .sparql import Unsupported, read

BOUND = r"BOUND\s*\(\s*[?$]{}\s*\)"


def flag(query):
    """The query with one OPTIONAL flag rewritten at a time until none is left, or None when there was none."""
    out = None
    for _ in range(8):
        new = _once(out or query)
        if new is None:
            return out
        out = new
    return out


def _scopes(g, chain, graph, out):
    """(group, its enclosing groups in the same SELECT scope, the nearest GRAPH variable) for every group under g."""
    graph = g.graph or graph
    out.append((g, chain, graph))
    for e in g.elements:
        for sub in e.groups:
            if e.kind == "subselect":  # a new scope: its variables are not the outer ones
                _scopes(sub, [], None, out)
            else:
                _scopes(sub, chain + [g], graph, out)
    return out


def _once(q):
    if re.search(r"\b(SELECT|DISTINCT|REDUCED)\s+\*", q, re.I):
        return None
    try:
        root, _ = read(q)
    except Unsupported:
        return None
    scopes = _scopes(root, [], None, [])
    for g, chain, graph in scopes:
        for e in g.elements:
            if e.kind == "optional" and (new := _rewrite(q, g, chain, graph, e, scopes)) is not None:
                return new
    return None


def _rewrite(q, g, chain, graph, e, scopes):
    body = e.groups[0]
    if any(x.kind not in ("triples", "filter") or x.groups for x in body.elements) or body.graph:
        return None
    inside = q[e.start:e.end]
    outside = q[:e.start] + " " * (e.end - e.start) + q[e.end:]  # same positions, the OPTIONAL blanked
    names = set(re.findall(r"[?$](\w+)", inside))
    required = {v for x in [g] + chain for el in x.elements if el.kind == "triples" for t in el.triples
                for v in t.vars()} | ({graph} if graph else set())
    own, flags = set(), []
    for v in names:
        uses = [m.start() for m in re.finditer(rf"[?$]{v}\b", outside)]
        bounds = [m.span() for m in re.finditer(BOUND.format(v), outside, re.I)]
        if all(any(a <= u < b for a, b in bounds) for u in uses):
            own.add(v)
            flags += bounds
        elif f"?{v}" not in required and f"${v}" not in required:
            return None  # used outside, but not bound by the required patterns: more than a flag
    if not flags:
        return None
    for x in body.elements:  # a FILTER inside P may only look at P's own variables
        if x.kind == "filter" and not set(re.findall(r"[?$](\w+)", q[x.start:x.end])) <= own:
            return None
    home = {id(x) for x in [g] + chain}
    for a, _ in flags:  # every BOUND after the OPTIONAL, in its group or an enclosing one of the same scope
        innermost = min((x for x, *_ in scopes if x.start <= a < x.end), key=lambda x: x.end - x.start)
        if a < e.end or id(innermost) not in home:
            return None
    p = q[body.start + 1:body.end - 1].strip()
    exists = f"EXISTS {{ GRAPH {graph} {{ {p} }} }}" if graph else f"EXISTS {{ {p} }}"
    end = e.end
    if m := re.match(r"\s*\.", q[end:]):  # a "." after the OPTIONAL would follow another one
        end += m.end()
    start = len(q[:e.start].rstrip(" \t"))
    if (start == 0 or q[start - 1] == "\n") and (m := re.match(r"[ \t]*\n", q[end:])):
        end += m.end()  # the OPTIONAL had its own lines: remove them whole
    edits = sorted([(start, end, "")] + [(a, b, exists) for a, b in flags], reverse=True)
    for a, b, text in edits:
        q = q[:a] + text + q[b:]
    return q
