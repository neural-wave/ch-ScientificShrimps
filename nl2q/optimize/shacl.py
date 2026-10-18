"""The static optimizer (NL2Q_OPT_STATS=schema, the default): what the SPHN 2026.1 schema and its SHACL shapes say, never
the data. A hospital's database follows the same schema, so these rules hold in production; the ranking from the 100k
mock patients' counts (stats.py, NL2Q_OPT_STATS=data) is an optional extra measured on this dataset only.
shacl.json (optbench stats): per (class, property) the shapes' [sh:minCount, sh:maxCount]. Terminologies come from the
schema's prefixes. The sort key follows the paper's rules (Brites Marto et al. 2025): the coded triple first, then the
joins, a check on a bound node as soon as possible; an inferred code scanned inside a bound patient graph goes last
(measured at 100k: it probes every sub-code in every graph). Ties keep the written order."""
import json
from functools import lru_cache
from .. import schema as SC
from .. import settings as S

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
NOT_TERMINOLOGY = {"rdf", "rdfs", "owl", "xsd", "bif", "sphn", "sphn-individual", "sphn-deprecated", "constraints"}
BASES = sorted((v for k, v in SC.PREFIXES.items() if k not in NOT_TERMINOLOGY), key=len, reverse=True)


@lru_cache(1)
def single():
    """Properties with sh:maxCount 1, per class: {property: {classes}}; and those with maxCount 1 for every class."""
    card = json.loads(S.OPT_SHACL.read_text())["card"] if S.OPT_SHACL.exists() else {}
    per, every = {}, {}
    for k, (_, hi) in card.items():
        c, p = k.split(" ")
        every.setdefault(p, True)
        every[p] = every[p] and hi == 1
        if hi == 1:
            per.setdefault(p, set()).add(c)
    return per, {p for p, one in every.items() if one}


def terminology(iri):
    return any(iri.startswith(b) for b in BASES)


def key(t, iri, in_graph, inference, bound, graph_bound, types=None, doomed=()):
    """Sort key of a Triple once the variables in `bound` (and the patient graph, if graph_bound) are bound:
    0 a check; 1 a link from a bound subject over a property SHACL says has one value; 2 any other link; with nothing
    bound, 3 a terminology code, 4 a class, 5 the rest; inside a bound graph, unlinked, 6 a class, 7 the rest, 8 an
    inferred code (a scan that probes every sub-code in every graph)."""
    vs = t.vars()
    if vs <= bound:
        return 0,
    typed = t.p.kind in ("a", "iri", "pname") and iri(t.p) == RDF_TYPE and t.o.kind in ("iri", "pname")
    code = typed and terminology(iri(t.o))
    if vs & bound:
        if t.s.text in bound and t.p.kind in ("iri", "pname"):
            per, every = single()
            p, classes = iri(t.p), (types or {}).get(t.s.text)
            if p in every or classes and classes & per.get(p, set()):
                return 1,
        return 2,
    if not graph_bound:
        return (3 if code else 4 if typed else 5),
    if code and (inference or "inference" in t.option.lower()):
        return 8,
    return (6 if typed else 7),
