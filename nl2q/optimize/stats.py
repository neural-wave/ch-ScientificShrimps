"""How many rows a triple pattern matches in the 100,000 mock patients: instances and patients per class, triples and
patients per predicate, per terminology code and per (predicate, individual), fan-out and fan-in per predicate, from
the full scan in /data/scratch/nw-data/stats (built into cardinality.json by `python -m nl2q.tools.optbench stats`).
Counts and code IRIs only, never terminology labels. What the measurements at 100k showed (docs/report/sparql-optimization.md):
- the first pattern decides how many patient graphs Virtuoso visits: rank it by patients, then by instances;
- once the graph is bound, an inferred code type (?c a icd-10-gm:E11 OPTION (inference ...)) probes every sub-class
  of the code in the ontology in every graph: cheap as a check on a bound node, expensive as a scan (moved to the
  front of such a run, N03 went from 2.8 to 9.7 s and from 8.3 to 29 s);
- a code the data has no count for keeps the paper's rule: the code first;
- a pattern that matches nothing in the data goes first wherever it is: moved behind other patterns it cost O08#4
  0.14 -> 60 s (cap) and O05#8 0.03 -> 0.44 s."""
import json, re
from functools import lru_cache
from .. import schema as SC
from .. import settings as S

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
SPHN = SC.PREFIXES["sphn"]
NOT_TERMINOLOGY = {"rdf", "rdfs", "owl", "xsd", "bif", "sphn", "sphn-individual", "sphn-deprecated", "constraints"}
ROLLUP = {"icd-10-gm", "chop", "atc", "sphn-atc"}  # lexical hierarchies: with inference E11 covers 2021/E11.9
VERSION = re.compile(r"^\d{4}/")
LINK = 10  # rows per binding up to which following a bound variable beats any other pattern
UNKNOWN_CODE = (1, 1, 1)  # (instances, patients, sub-codes) of a code without a count: first, after true zeros


@lru_cache(1)
def data():
    d = json.loads(S.OPT_STATS.read_text())
    for k in ("individuals", "edges", "fanout", "fanin", "card", "class_max", "disagree"):  # an older file lacks some
        d.setdefault(k, {})
    bases = sorted(((v, k) for k, v in SC.PREFIXES.items() if k not in NOT_TERMINOLOGY), key=lambda b: -len(b[0]))
    d["bases"], d["family"], d["rollup"] = bases, {b: 0 for b, _ in bases}, {}
    for iri, (n, pats) in d["codes"].items():
        if base := family(iri, bases):
            d["family"][base[0]] += n
            if base[1] in ROLLUP:
                d["rollup"].setdefault(base[0], []).append((VERSION.sub("", iri[len(base[0]):]), n, pats))
    return d


def family(iri, bases=None):
    """(namespace, prefix) of the terminology an IRI belongs to, or None."""
    return next(((b, k) for b, k in (bases or data()["bases"]) if iri.startswith(b)), None)


def code(iri, inference):
    """(instances, patients, sub-codes) of the code nodes typed with a terminology class; None if the IRI is not in a
    terminology."""
    d = data()
    if not (base := family(iri)):
        return None
    if not d["family"][base[0]]:
        return 0, 0, 0  # a terminology the data does not use at all (ATC, LOINC): nothing matches, inferred or not
    exact = d["codes"].get(iri)
    if not inference:
        return (*exact, 1) if exact else (0, 0, 0)  # the exact class: the scan saw every code of the data
    if base[1] in ROLLUP:
        local = VERSION.sub("", iri[len(base[0]):])
        under = [(n, p) for name, n, p in d["rollup"].get(base[0], []) if name.startswith(local)]
        if under:  # patients: summed over the sub-codes, an upper bound
            return sum(n for n, _ in under), min(d["patients"], sum(p for _, p in under)), len(under)
        return UNKNOWN_CODE
    return (*exact, 1) if exact else UNKNOWN_CODE  # SNOMED and the others: the exact count (sub-codes unknown)


def triple(t, iri, in_graph, inference=False):
    """(matches, patients) of a Triple in the whole store; iri(term) resolves a term to a full IRI. Unknown: every
    triple in every patient."""
    d = data()
    unknown = d["triples"], d["patients"]
    if t.p.kind in ("var", "path"):
        return unknown
    p = iri(t.p)
    inference = inference or "inference" in t.option.lower()
    if p == RDF_TYPE and t.o.kind in ("iri", "pname"):
        o = iri(t.o)
        if (c := code(o, inference)) is not None:
            return c[:2]
        if (n := d["classes"].get(o)) is not None:
            return tuple(n)
        # a patient graph holds only the scanned classes; outside GRAPH the ontology graph adds its own
        return (0, 0) if in_graph and o.startswith(SPHN) and not inference else unknown
    n = d["predicates"].get(p)
    if n is None:  # a 2026.1 property the 2024.2 data does not have matches nothing (unless inferred from another)
        return (0, 0) if in_graph and p.startswith(SPHN) and not inference else unknown
    if t.o.kind in ("iri", "pname"):
        return tuple(d["individuals"].get(f"{p} {iri(t.o)}", n))
    return tuple(n)


def dead(t, iri, in_graph, types):
    """A property no subject of its class has in the data (hasRouteCode on a 2024.2 DrugPrescription): types maps a
    variable to its sphn classes in the run."""
    cs = types.get(t.s.text) if in_graph else None
    if not cs or t.p.kind not in ("iri", "pname"):
        return False
    p = iri(t.p)
    return p.startswith(SPHN) and p != RDF_TYPE and not any(f"{c} {p}" in data()["edges"] for c in cs)


def key(t, iri, in_graph, inference, bound, graph_bound, types=None, doomed=()):
    """Sort key (tier, ...) of a Triple once the variables in `bound` (and the patient graph, if graph_bound) are bound:
    -2 a pattern that matches nothing in the data (a 2026.1 term, a terminology the data lacks): an index miss that
    empties the run; -1 the class of a subject with a dead property (doomed: such subjects), then that property as soon
    as the subject is bound (each row dies there); 0 a check (every variable bound); 1 a cheap link (a bound end, at
    most LINK rows each: fan-out or fan-in); 2 an expensive link outside a bound patient graph; 3 a scan, by the
    patients it occurs in, then its matches (the first pattern decides how many graphs Virtuoso visits, a rare one
    prunes the graph early); inside a bound graph 4 an expensive link and 5 an inferred code scan (it becomes a check
    once the chain binds its node)."""
    d, vs = data(), t.vars()
    n, pats = triple(t, iri, in_graph, inference)
    if not pats:
        return -2, 0, 0
    if t.s.text in doomed:
        if t.s.text in bound and dead(t, iri, in_graph, types or {}):
            return -1, 0, 0
        if t.p.kind in ("a", "iri", "pname") and iri(t.p) == RDF_TYPE and t.o.kind in ("iri", "pname") \
                and iri(t.o) in (types or {}).get(t.s.text, ()):
            return -1, pats, n
    if vs <= bound:
        return 0, 0, 0
    if vs & bound:
        fan = n / d["patients"]
        if t.p.kind not in ("var", "path"):
            p = iri(t.p)
            if t.s.text in bound:
                fan = min(fan, d["fanout"].get(p, 1.0))  # SPHN properties are mostly single-valued (SHACL maxCount 1)
            elif t.o.text in bound:
                fan = min(fan, d["fanin"].get(p, fan))
        return (1 if fan <= LINK else 4 if graph_bound else 2), fan, 0
    if graph_bound and t.p.kind not in ("var", "path") and iri(t.p) == RDF_TYPE and t.o.kind in ("iri", "pname") \
            and (inference or "inference" in t.option.lower()) and code(iri(t.o), True):
        return 5, pats, n
    return 3, pats, n
