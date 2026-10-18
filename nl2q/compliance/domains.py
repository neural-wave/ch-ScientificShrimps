"""Which sphn: properties each SPHN 2026.1 class has, for validate()'s schema feedback (NL2Q_LINK=1). Static: built once
from the schema files into domains.json (committed), never from the database; at run time only the JSON is read.
A class has the properties of its OWL restrictions and of the SHACL shapes that target it (sh:path, first step), plus
those of its superclasses. rdfs:domain is read but left out: every domain lists sphn:SPHNConcept, and beyond the
restrictions it adds only abstract super-properties (sphn:hasCode, hasDateTime, hasRouteCode, ...), so it would let
sphn:hasRouteCode through on a DrugAdministrationEvent, whose property is sphn:hasAdministrationRouteCode.
  python -m nl2q.compliance.domains [sphn_rdf_schema.ttl shacl_2026-1.ttl]   # rewrites domains.json"""
import json, sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from .. import schema as SC

FILE = Path(__file__).with_name("domains.json")
SOURCES = ("/data/scratch/lorenzo.varese@supsi.ch/sphn_dl/sphn_rdf_schema.ttl",
           "/data/scratch/lorenzo.varese@supsi.ch/sphn_dl/shacl_2026-1.ttl")  # SPHN 2026.1 (GitLab), untrusted copies
ROOT = "SPHNConcept"  # parent of every concept: a node typed with it may have any property


@lru_cache(1)
def load():
    """(props, sub): {class: its properties, own or inherited}, {class: its subclasses, at any depth}; local names."""
    d = json.loads(FILE.read_text())
    return {c: frozenset(ps) for c, ps in d["props"].items()}, d["sub"]


def build(schema_ttl, shacl_ttl):
    """The JSON content, from the OWL schema and the SHACL shapes."""
    from rdflib import OWL, RDF, RDFS, Graph, Namespace, URIRef
    from rdflib.collection import Collection
    SH = Namespace("http://www.w3.org/ns/shacl#")
    g, s = Graph().parse(schema_ttl), Graph().parse(shacl_ttl)
    mine = lambda u: isinstance(u, URIRef) and str(u).startswith(SC.NS)
    name = lambda u: str(u)[len(SC.NS):]
    own, up = defaultdict(set), defaultdict(set)

    def restrictions(n, c):
        if mine(p := g.value(n, OWL.onProperty)):
            own[c].add(name(p))
        for k in (OWL.intersectionOf, OWL.unionOf):
            for m in Collection(g, g.value(n, k)) if g.value(n, k) is not None else ():
                restrictions(m, c)

    classes = {name(c) for c in g.subjects(RDF.type, OWL.Class) if mine(c)}
    for c in g.subjects(RDF.type, OWL.Class):
        for b in g.objects(c, RDFS.subClassOf) if mine(c) else ():
            if mine(b):
                up[name(c)].add(name(b))
            else:
                restrictions(b, name(c))
    for shape, c in s.subject_objects(SH.targetClass):
        for ps in s.objects(shape, SH.property) if mine(c) else ():
            path = s.value(ps, SH.path)
            first = path if isinstance(path, URIRef) else s.value(path, RDF.first)  # a sequence: its first step
            if mine(first):  # inverse paths (^rdf:type on value sets) say nothing about the class's properties
                own[name(c)].add(name(first))
    above = lambda c: set().union(*({b} | above(b) for b in up[c]))
    props = {c: sorted(own[c].union(*(own[a] for a in above(c)))) for c in sorted(classes - {ROOT})}
    sub = {c: sorted(d for d in classes if c in above(d)) for c in props}
    supers = {name(o) for o in g.objects(None, RDFS.subPropertyOf) if mine(o)}
    domain = {(name(c), name(p)) for p, d in g.subject_objects(RDFS.domain) if mine(p)
              for c in (Collection(g, g.value(d, OWL.unionOf)) if g.value(d, OWL.unionOf) is not None else [d])
              if mine(c) and name(c) in props}
    extra = {(c, p) for c, p in domain if p not in props[c]}
    print(f"{len(props)} classes, {sum(map(len, props.values()))} (class, property) pairs; rdfs:domain adds {len(extra)}, "
          f"{'all' if all(p in supers for _, p in extra) else 'NOT all'} abstract super-properties: left out", file=sys.stderr)
    return {"source": "SPHN 2026.1: sphn_rdf_schema.ttl (OWL restrictions, rdfs:subClassOf) + shacl_2026-1.ttl",
            "props": props, "sub": {c: ds for c, ds in sub.items() if ds}}


def write(d, path=FILE):
    """One class per line: a small diff when the schema changes."""
    body = lambda m: ",\n".join(f"  {json.dumps(k)}: {json.dumps(v)}" for k, v in m.items())
    path.write_text(f'{{"source": {json.dumps(d["source"])},\n"props": {{\n{body(d["props"])}\n}},\n'
                    f'"sub": {{\n{body(d["sub"])}\n}}}}\n')


if __name__ == "__main__":
    write(build(*(sys.argv[1:3] if len(sys.argv) > 2 else SOURCES)))
    print(f"wrote {FILE}")
