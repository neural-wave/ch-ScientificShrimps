"""NL2Q_CODE_CLASS: a static rewrite of the generated query, before it is checked and judged. In the data a code is a
node typed with its terminology class (?r sphn:hasCode ?c . ?c a icd-10-gm:E11.9), one node per record, and the node's
IRI is a record ID. The model often groups by, returns or compares the node itself: the rules then refuse the query
(it returns record IDs), or a comparison of two nodes is always true. Here such a node is replaced by its class:
  ?r sphn:hasCode ?c .            ->  ?r sphn:hasCode ?c . ?c a ?c_class .
  ... GROUP BY ?c / FILTER(?c != ?d) / STR(?c)  ->  ?c_class / ?c_class != ?d_class / STR(?c_class)
Grounded in the schema: a code node is the object of a code property (SC.CODE_PROPS, the set the rules use; every code
node in the data has exactly one asserted type). Only on a query whose outer level aggregates; COUNT(?c) (counting
records) is left alone. The rewritten query is judged as returned, so the rules decide as for any other query.
Anything the optimizer's reader does not fully understand, or a rewrite that no longer parses, leaves it unchanged."""
import logging, re
from .. import schema as SC
from .. import settings as S
from ..compliance.rules import CODE_PROPS, Invalid, parse
from ..optimize import apply
from ..optimize.sparql import Unsupported, groups, read, tokenize

log = logging.getLogger(__name__)
CODES = {str(p) for p in CODE_PROPS}
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
# a request that itself names nodes or identifiers may want exactly those: then the query stays as written
LITERAL = re.compile(r"\b(nodes?|IRIs?|URIs?|identifiers?|IDs?)\b", re.I)
AGGREGATES = {"COUNT", "SUM", "AVG", "MIN", "MAX", "SAMPLE", "GROUP_CONCAT", "GROUP"}  # GROUP: GROUP BY


def _iri(kind, text):
    if kind == "iri":
        return text[1:-1]
    prefix, _, local = text.partition(":")
    return SC.PREFIXES[prefix] + local if kind == "pname" and prefix in SC.PREFIXES else ""


def _code_prop(p):
    """Whether the verb ends, going forward, with a code property (sphn:hasCode, sphn:hasDrug/sphn:hasCode, ...)."""
    steps = p.steps or ()
    if p.kind in ("iri", "pname"):
        return _iri(p.kind, p.text) in CODES
    if p.kind != "path" or any(t.text in ("|", "*", "+", "?", "(", ")") for t in steps):
        return False
    last = steps[-1]
    return (len(steps) < 2 or steps[-2].text != "^") and _iri(last.kind, last.text) in CODES


def _is_type(p):
    return p.kind == "a" or _iri(p.kind, p.text) == RDF_TYPE


def to_class(query, text=""):
    """The query with each code node it groups by, returns or compares replaced by the node's class; else unchanged.
    text is the request: one that names nodes or identifiers leaves the query alone."""
    if not S.CODE_CLASS or not query or LITERAL.search(text):
        return query
    try:
        out = _rewrite(query)
    except Unsupported:
        return query
    except Exception as e:  # fail safe: a bug here must never cost a query
        log.warning(f"code-class rewrite skipped: {type(e).__name__}: {e}")
        return query
    if out == query:
        return query
    try:
        parse(out)
    except Invalid:
        return query
    log.info("code nodes replaced by their classes")
    return out


def _rewrite(query):
    root, _ = read(query)
    toks, _ = tokenize(query)
    elements = [e for g in groups(root) for e in g.elements]
    blocks = [e for e in elements if e.kind == "triples"]
    inside = lambda pos, kinds: any(e.start <= pos < e.end for e in elements if e.kind in kinds)
    subs = [e for e in elements if e.kind == "subselect"]
    level = lambda pos: min((e for e in subs if e.start <= pos < e.end), key=lambda e: e.end - e.start, default=None)
    # the outer level aggregates: an aggregate in its SELECT clause or GROUP BY after its WHERE group
    select = next(i for i, t in enumerate(toks) if t.kind == "name" and t.text.upper() == "SELECT")
    outer = [t for t in toks if toks[select].start <= t.start < root.start or t.start >= root.end]
    if not any(t.kind == "name" and t.text.upper() in AGGREGATES for t in outer):
        return query
    binding = {}  # code node variable -> the triples blocks that bind it as the object of a code property
    for e in blocks:
        for t in e.triples:
            if t.o.kind == "var" and _code_prop(t.p):
                binding.setdefault(t.o.text[1:], []).append(e)
    names = {t.text[1:] for t in toks if t.kind == "var"}
    edits = []
    for v, where in binding.items():
        used = [t for e in blocks for t in e.triples if v in {x.text[1:] for x in (t.s, t.p, t.o) if x.kind == "var"}]
        # in triple patterns the node may only be bound (object of a code property) or described (subject)
        if any(not (t.s.text[1:] == v and t.s.kind == "var") and not (t.o.text[1:] == v and _code_prop(t.p)) for t in used):
            continue
        # all in one query level: a node projected out of a sub-select and joined again outside keeps its name
        if len({id(level(e.start)) for e in blocks for t in e.triples if t in used}) > 1:
            continue
        spots, skip = [], False
        for i, t in enumerate(toks):
            if t.kind != "var" or t.text[1:] != v or inside(t.start, ("triples",)):
                continue
            if inside(t.start, ("values",)):
                skip = True
                break
            before = [x.text.upper() for x in toks[max(0, i - 3):i]]
            if before[-2:] == ["COUNT", "("] or before == ["COUNT", "(", "DISTINCT"]:
                continue  # counting the nodes counts records: that stays
            spots.append(t)
        if skip or not spots:
            continue
        # the class: an existing ?v a ?class in a block that binds it, else a new variable added after those blocks
        known = next((t.o.text[1:] for e in where for t in e.triples
                      if t.s.kind == "var" and t.s.text[1:] == v and _is_type(t.p) and t.o.kind == "var"), None)
        cls = known
        if cls is None:
            cls = f"{v}_class"
            while cls in names:
                cls += "_"
            names.add(cls)
            for e in {id(e): e for e in where}.values():
                sep = " " if query[e.end - 1] == "." else " . "
                edits.append((e.end, e.end, f"{sep}?{v} a ?{cls} ."))
        edits += [(t.start, t.end, t.text[0] + cls) for t in spots]
    return apply(query, edits, 0, len(query)) if edits else query
