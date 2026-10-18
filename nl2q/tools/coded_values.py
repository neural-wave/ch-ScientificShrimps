"""Write the values the SPHN 2026.1 schema allows for each coded detail of the plan record types
(nl2q/translation/resources/coded_values.json), so plan.check() can reject a value the schema does not have:
- "value" (a value-set individual, resources/coded.json): the individuals of the restriction's value-set class
  (sphn:AdverseEvent_outcome -> Fatal, NotRecovered, Recovered, ...), by local name;
- "class" (a linked record's SPHN class): the subclasses of the link's generic class (sphn:GenomicVariation ->
  sphn:GenomicDeletion, ...);
- "code" (a code node, also the fixed rank and route): the codes of the restriction (owl:someValuesFrom, on the record
  type, a super- or a subtype); "closed" when the schema says "no subclasses allowed" (only those codes), else any
  code under them (their code systems: the hierarchy itself needs the terminologies, so check() tests the system).
  No restriction: any code. "examples": codes of the 5k-patient data (inventory) that fit, for the check's message.
  python -m nl2q.tools.coded_values        # needs the 2026.1 schema file (NL2Q_SCHEMA_TTL); run after coded_details"""
import json
from collections import defaultdict
from rdflib import OWL, RDF, RDFS, BNode, Graph, URIRef
from rdflib.collection import Collection
from .. import schema as SC
from .. import settings as S
from ..translation import plan as P
from .check_examples import SCHEMA_TTL, short
from .coded_details import dump

OUT = P.HERE / "resources" / "coded_values.json"
INVENTORY = S.DATA / "inventory" / "inventory.json"
SCOPE = URIRef("http://www.w3.org/2004/02/skos/core#scopeNote")
N_EXAMPLES = 6


def details(t):
    """{alias: (property, kind, generic class)} of a record type: its coded details and its fixed rank and route."""
    spec = P.RECORDS[t]
    out = {a: (v[0], v[1], v[2] if len(v) > 2 else None) for a, v in P.CODED.get(t, {}).items() if a in P.coded(t)}
    out |= {a: (spec[a], "code", None) for a in ("rank", "route") if spec.get(a)}
    return out


class Restrictions:
    def __init__(self, path):
        self.g = g = Graph()
        g.parse(path)
        self.up, self.down = defaultdict(set), defaultdict(set)
        for c, sup in g.subject_objects(RDFS.subClassOf):
            if isinstance(c, URIRef) and isinstance(sup, URIRef):
                self.up[c].add(sup), self.down[sup].add(c)

    def closure(self, rel, c):
        seen, todo = set(), [c]
        while todo:
            for x in rel[todo.pop()] - seen:
                seen.add(x), todo.append(x)
        return seen

    def kin(self, c):
        return {c} | self.closure(self.up, c) | self.closure(self.down, c)

    def union(self, n):
        lst = self.g.value(n, OWL.unionOf) if isinstance(n, BNode) else None
        return list(Collection(self.g, lst)) if lst is not None else [n]

    def some_values(self, c, prop):
        """The owl:someValuesFrom of the restrictions on prop of c's kin (own, inherited or a subtype's)."""
        out = []

        def walk(n):
            if self.g.value(n, OWL.onProperty) == prop and self.g.value(n, OWL.someValuesFrom) is not None:
                out.extend(self.union(self.g.value(n, OWL.someValuesFrom)))
            if (lst := self.g.value(n, OWL.intersectionOf)) is not None:
                for x in Collection(self.g, lst):
                    walk(x)
        for k in self.kin(c):
            for sup in self.g.objects(k, RDFS.subClassOf):
                if isinstance(sup, BNode):
                    walk(sup)
        return list(dict.fromkeys(out))

    def closed(self, c, prop):
        note = f"{short(prop)} no subclasses allowed"
        return any(str(n) == note for k in self.kin(c) for n in self.g.objects(k, SCOPE))

    def individuals(self, cls):
        return sorted(str(i).rsplit("#", 1)[-1] for i in self.g.subjects(RDF.type, cls))

    def subclasses(self, cls):
        return sorted(short(x) for x in self.closure(self.down, cls))


def data_codes(t, prop):
    """The codes the 5k-patient data holds under a record type's property (inventory), most patients first."""
    raw = json.loads(INVENTORY.read_text())["record_types"] if INVENTORY.exists() else {}
    return [x["prefixed"] for c in (raw.get(t) or {}).get("codes") or [] if c["path"] == prop
            for x in c["top_by_patients"]]


def allowed(r, t, alias, prop, kind, generic):
    c, p = URIRef(SC.NS + t), URIRef(SC.NS + prop.split(":", 1)[1])
    if kind == "class":
        return {"kind": "class", "values": r.subclasses(URIRef(SC.NS + generic.split(":", 1)[1]))}
    svf = r.some_values(c, p)
    if kind == "value":
        return {"kind": "value", "values": sorted({v for s in svf for v in r.individuals(s)})}
    codes = sorted(short(x) for x in svf if not str(x).startswith(SC.NS))  # sphn:Code / sphn:Terminology: no restriction
    closed = bool(codes) and r.closed(c, p)
    systems = sorted({x.split(":", 1)[0] for x in codes})
    fits = lambda x: x in codes if closed else not systems or x.split(":", 1)[0] in systems
    examples = [x for x in data_codes(t, prop) if ":" in x and x.split(":", 1)[0] in SC.PREFIXES and
                x.split(":", 1)[0] != "sphn" and fits(x)][:N_EXAMPLES]
    out = {"kind": "code", "codes": codes, "closed": closed, "systems": systems, "examples": examples}
    if alias == "rank":
        out["main"] = P.MAIN
    return out


def build(path=SCHEMA_TTL):
    r = Restrictions(path)
    return {t: {a: allowed(r, t, a, *v) for a, v in sorted(details(t).items())} for t in P.CURATED if details(t)}  # generic: schema_records.json


def main():
    OUT.write_text(dump(build()))
    got = json.loads(OUT.read_text())
    print(f"{OUT}: {sum(map(len, got.values()))} coded details")
    for t, ds in got.items():
        for a, d in ds.items():
            what = d.get("values") or d.get("codes") or "any code"
            print(f"  {t}.{a} ({d['kind']}{', closed' if d.get('closed') else ''}): {len(what) if isinstance(what, list) else what}")


if __name__ == "__main__":
    main()
