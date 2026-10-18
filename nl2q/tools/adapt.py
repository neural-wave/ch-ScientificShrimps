"""Generate nl2q's schema config from the SPHN schema (+ SHACL), keeping what the data really uses.
  python -m nl2q.tools.adapt sphn_rdf_schema.ttl [shacl.ttl] [--data patient.ttl ... | --endpoint URL] --out nl2q/config/schema.json
Reads SHACL node shapes, OWL restrictions, instance data and rdfs:domain/range. Writes the compliance check's
property sets (identifiers, codes, literal values, value sets, every sphn: term) and a schema card split per record
type. With --data or --endpoint the card keeps only record types and properties that actually occur in patient graphs
(only (class, property) pairs and code classes are read, never values). With --endpoint it also writes the labels of
the codes in use to --codes (nl2q_data/codes.json): those come from restricted terminologies, so never commit it."""
import argparse, json, re
from collections import defaultdict
from itertools import chain
from pathlib import Path
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS, SH, SKOS, XSD

VOCAB = tuple(str(n) for n in (RDF, RDFS, OWL, SH, SKOS, "http://purl.org/dc/", "http://www.w3.org/XML/"))
PATIENTISH = re.compile(r"subject|patient|person|pseudo", re.I)
IDENTIFIER = re.compile(r"\b(identifier|id|mrn|ssn|ahv|avs|passport|e ?mail|phone|telephone|fax|address|street|postal|zip|"
                        r"surname|(first|last|given|family|full|maiden) name)\b")  # on the camelCase-split name; review the list
CODEISH = re.compile(r"code$", re.I)
CORE = re.compile(r"^(birth|death|administrativesex|sex|gender|age)$", re.I)
NOEXPAND = re.compile(r"SourceSystem|Software|Device|Analyzer|Kit|Comparator|DataProvider|Instrument", re.I)  # card noise
GRAPH_PREFIX = "http://example.org/graph/"  # patient graphs; the ontologies live in their own graph
SPHN = "https://biomedit.ch/rdf/sphn-schema/sphn#"
# The mock data follows SPHN 2024.2, the queries SPHN 2026.1 (organizers, 16 Oct): a 2024.2 pair found in the data makes
# its 2026.1 successors populated, so the card shows the terms the queries must use.
# (class or "*", 2024.2 property) -> [(class, "" for the same one; 2026.1 property; namespace of its values, None: same)]
SUCCESSORS = {
    ("*", "hasRank"): [("", "hasRankCode", "http://snomed.info/id/")],
    ("*", "hasGeneticVariation"): [("", "hasGenomicVariation", None)],
    ("AdministrativeCase", "hasAdmissionDateTime"): [("", "hasAdmission", SPHN), ("Admission", "hasDateTime", "")],
    ("AdministrativeCase", "hasDischargeDateTime"): [("", "hasDischarge", SPHN), ("Discharge", "hasDateTime", "")],
    ("AdministrativeCase", "hasOriginLocation"): [("", "hasAdmission", SPHN), ("Admission", "hasOriginLocation", None)],
    ("AdministrativeCase", "hasDischargeLocation"): [("", "hasDischarge", SPHN), ("Discharge", "hasTargetLocation", None)],
    ("DrugPrescription", "hasAdministrativeCase"): [("DrugPrescriptionEvent", "hasSubjectPseudoIdentifier", SPHN),
                                                    ("DrugPrescriptionEvent", "hasDrugPrescription", SPHN),
                                                    ("DrugPrescriptionEvent", "hasAdministrativeCase", SPHN)],
}


def successors(populated):
    """The populated pairs plus the 2026.1 successors of the 2024.2 pairs among them."""
    out = defaultdict(set, {k: set(v) for k, v in populated.items()})
    for (c, p), spaces in populated.items():
        for (cls, old), new in SUCCESSORS.items():
            if local(p) == old and cls in ("*", local(c)):
                for nc, np_, ns in new:
                    out[URIRef(SPHN + nc) if nc else c, URIRef(SPHN + np_)] |= set(spaces) if ns is None else {ns}
    return out


def is_identifier(name):
    words = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", re.sub(r"([a-z])([A-Z])", r"\1 \2", name))  # hasAHVNumber: has AHV Number
    return bool(IDENTIFIER.search(words.lower()))


def local(u):
    return re.split(r"[#/=]", str(u).rstrip("/#"))[-1]


def facts(g):
    """(class, property, range) triples. Precise sources (SHACL, OWL restrictions, instance data) win;
    global rdfs:domain/range is used only where nothing precise is known for that class and property."""
    precise, loose = set(), set()

    def alts(x):  # a class, an owl:unionOf / sh:or list, or a SHACL shape with sh:class / sh:datatype
        if isinstance(x, BNode):
            for lst in chain(g.objects(x, OWL.unionOf), g.objects(x, SH["or"])):
                return [m for alt in Collection(g, lst) for m in alts(alt)]
            return [v for k in (SH["class"], SH.datatype) for v in g.objects(x, k)] or [None]
        return [x]

    for c, sup in g.subject_objects(RDFS.subClassOf):  # OWL restrictions, possibly inside intersections
        for r in chain([sup], *(Collection(g, lst) for lst in g.objects(sup, OWL.intersectionOf))):
            if (p := g.value(r, OWL.onProperty)) is not None:
                vals = [v for k in (OWL.someValuesFrom, OWL.allValuesFrom) for v in g.objects(r, k)]
                (precise if vals else loose).update((c, p, m) for v in vals or [None] for m in (alts(v) if v else [None]))
    for shape in g.subjects(RDF.type, SH.NodeShape):  # SHACL
        for c in g.objects(shape, SH.targetClass):
            for ps in g.objects(shape, SH.property):
                if isinstance(p := g.value(ps, SH.path), URIRef):
                    precise |= {(c, p, m) for m in alts(ps)}
    for p, d in g.subject_objects(RDFS.domain):  # global domain/range
        for c in alts(d):
            loose |= {(c, p, m) for r in list(g.objects(p, RDFS.range)) or [None] for m in alts(r)}
    known = {(c, p) for c, p, _ in precise}
    return {f for f in precise | {f for f in loose if f[:2] not in known}
            if isinstance(f[0], URIRef) and not str(f[1]).startswith(VOCAB)}


def _select(endpoint, q):
    import requests
    r = requests.post(endpoint, data={"query": q}, headers={"Accept": "application/sparql-results+json"}, timeout=3600)
    r.raise_for_status()
    return r.json()["results"]["bindings"]


def observed(g=None, endpoint=None):
    """{(class, property): namespaces of the values' code classes} for what occurs in patient data. Code nodes are typed
    with their terminology class (?code a icd-10-gm:E11), so the namespace tells the card which terminology is used."""
    out = defaultdict(set)
    if endpoint:
        q = (f'SELECT DISTINCT ?c ?p ?ns WHERE {{ GRAPH ?g {{ ?x a ?c ; ?p ?o . OPTIONAL {{ ?o a ?t }} }} '
             f'FILTER(STRSTARTS(STR(?g), "{GRAPH_PREFIX}")) BIND(IF(BOUND(?t), REPLACE(STR(?t), "[^/#=]+$", ""), "") AS ?ns) }}')  # + not *: Virtuoso refuses a pattern that matches ""
        for b in _select(endpoint, q):
            out[URIRef(b["c"]["value"]), URIRef(b["p"]["value"])].add(b["ns"]["value"])
        return out
    types = defaultdict(set)
    for s, c in g.subject_objects(RDF.type):
        types[s].add(c)
    for s, p, o in g:
        for c in types.get(s, ()) if p != RDF.type else ():
            out[c, p] |= {re.sub(r"[^/#=]*$", "", str(t)) for t in types.get(o, ())} or {""}
    return out


def codes_in_use(code_props, k_min, g=None, endpoint=None):
    """{(class, code property): [code class, ...]} for code classes on at least k records, most frequent first: a
    vocabulary of what is recorded (no patient data), so the model does not reach for codes the data never uses."""
    vals = " ".join(f"<{p}>" for p in code_props)
    if endpoint:
        q = (f"SELECT ?c ?p ?cls (COUNT(*) AS ?n) WHERE {{ GRAPH ?g {{ ?x a ?c ; ?p ?code . ?code a ?cls }} VALUES ?p {{ {vals} }} "
             f'FILTER(STRSTARTS(STR(?g), "{GRAPH_PREFIX}")) }} GROUP BY ?c ?p ?cls HAVING (COUNT(*) >= {k_min}) ORDER BY DESC(?n)')
        rows = [(URIRef(b["c"]["value"]), URIRef(b["p"]["value"]), URIRef(b["cls"]["value"])) for b in _select(endpoint, q)]
    else:  # plain loop: rdflib's SPARQL engine takes minutes on this join
        props, types, n = set(code_props), defaultdict(set), defaultdict(int)
        for s_, c in g.subject_objects(RDF.type):
            types[s_].add(c)
        for x, p, code in g:
            if p in props:
                for c in types.get(x, ()):
                    for cls in types.get(code, ()):
                        n[c, p, cls] += 1
        rows = [k for k, v in sorted(n.items(), key=lambda kv: -kv[1]) if v >= k_min]
    out = defaultdict(list)
    for c, p, cls in rows:
        out[c, p].append(cls)
    return out


def code_labels(endpoint, codes, ontology_graph):
    """[[code IRI, labels]] for the codes in use and their parents, from the terminologies loaded in Virtuoso."""
    out, codes = [], sorted({str(c) for c in codes})
    for i in range(0, len(codes), 200):
        vals = " ".join(f"<{c}>" for c in codes[i:i + 200])
        q = (f"SELECT ?code (GROUP_CONCAT(DISTINCT STR(?l); separator=' | ') AS ?labels) WHERE {{ VALUES ?c {{ {vals} }} "
             f"GRAPH <{ontology_graph}> {{ {{ BIND(?c AS ?code) }} UNION {{ ?c rdfs:subClassOf ?code }} ?code rdfs:label ?l }} }} GROUP BY ?code")
        out += [[b["code"]["value"], b["labels"]["value"]] for b in _select(endpoint, "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n" + q)]
    return out


def build(g, patient=None, link=None, populated=None, recorded=None, linked=False):
    """linked (--linked, schema only): a class with a SHACL shape shows only its SHACL properties (no abstract
    super-property such as hasRouteCode), each nested class is expanded once per block, a value-set root reads 'code
    under X', subclasses are listed as kinds, every class without a block gets one, and docs (label, definition, notes
    of every class) feed the retrieval (prompt.card with NL2Q_LINK)."""
    q = lambda u: g.namespace_manager.normalizeUri(u) if isinstance(u, URIRef) else str(u)
    props, ranges, of = defaultdict(set), defaultdict(set), defaultdict(lambda: defaultdict(set))
    for c, p, r in facts(g):
        props[p].add(c), ranges[p].add(r), of[c][p].add(r)
    roots = {c for c in of if len(of[c]) > 0.5 * len(props) and any(g.subjects(RDFS.subClassOf, c))}  # sphn:SPHNConcept

    def target(p):  # the single class a property points to, if any
        rs = {r for r in ranges[p] if r is not None}
        r = next(iter(rs)) if len(rs) == 1 else None
        return r if r is not None and not str(r).startswith(str(XSD)) else None

    cands = sorted(((len(props[p] - roots) * (3 if PATIENTISH.search(local(p)) else 1), q(p))
                    for p in props if target(p)), reverse=True)
    link = URIRef(link) if link else next(p for p in props if q(p) == cands[0][1])
    patient = URIRef(patient) if patient else target(link)
    concepts = sorted((c for c in props[link] if c not in roots and (not populated or (c, link) in populated)), key=q)
    ids = {p for p in props if is_identifier(local(p))} - {link}
    main = re.sub(r"[^#/]*$", "", str(link))  # the schema's own namespace; ranges outside it are terminologies
    coded = lambda r: r is not None and not str(r).startswith(str(XSD)) and (local(r) in ("Code", "Terminology") or not str(r).startswith(main))
    code_props = {p for p in props if CODEISH.search(local(p)) or any(map(coded, ranges[p]))} - {link}
    valuesets = {c for i in g.subjects(RDF.type, OWL.NamedIndividual) for c in g.objects(i, RDF.type) if c != OWL.NamedIndividual}
    known = lambda p: {r for r in ranges[p] if r is not None and r not in roots}  # sphn:SPHNConcept says nothing
    literal_props = {p for p in props if known(p) and all(str(r).startswith(str(XSD)) or r == RDFS.Literal for r in known(p))}
    value_props = {p for p in props if known(p) and all(r in valuesets for r in known(p))}
    shacl = defaultdict(dict)  # linked: {class: {property: ranges}} from the node shapes (constraints:sphnX targets sphn:X)
    rng = lambda ps: {v for x in chain([ps], *(Collection(g, l) for l in g.objects(ps, SH["or"])))
                      for k in (SH["class"], SH.datatype) for v in g.objects(x, k)} or {None}
    for shape in g.subjects(RDF.type, SH.NodeShape) if linked else ():
        m = re.fullmatch(r"sphn(\w+)", local(shape)) if not str(shape).startswith(main) else None
        for c in list(g.objects(shape, SH.targetClass)) or ([URIRef(main + m.group(1))] if m else []):
            for ps in g.objects(shape, SH.property):
                if isinstance(p := g.value(ps, SH.path), URIRef):
                    shacl[c][p] = shacl[c].get(p, set()) | rng(ps)
    for c, ps in shacl.items():
        of[c] = defaultdict(set, ps)
    kinds = lambda r: sorted((q(k) for k in g.subjects(RDFS.subClassOf, r) if str(k).startswith(main)), key=str)
    label = lambda c: str(g.value(c, RDFS.label) or local(c))
    comment = lambda c: re.sub(r"\s+", " ", str(g.value(c, RDFS.comment) or g.value(c, SKOS.definition) or ""))[:140]

    def code_line(vals, pad, p):  # linked: a single numeric code is a value-set root, a terminology root names the terminology
        if all(not re.search(r"\d{3}|^[A-Z]?\d", local(r)) for r in vals):
            return f"{pad}{q(p)} -> code ({', '.join(sorted({q(r).split(':')[0] + ':' for r in vals}))} codes)"
        if len(vals) == 1:
            return f"{pad}{q(p)} -> code under {q(vals[0])}"
        return f"{pad}{q(p)} -> code one of {{{', '.join(q(r) for r in vals[:6])}{', ...' if len(vals) > 6 else ''}}}"

    def describe(c, depth, seen, out, done=None):
        for p in sorted(of[c], key=q):
            if p == link or p in ids or len(out) > 40 or populated and (c, p) not in populated:
                continue
            if linked and (depth and all(NOEXPAND.search(local(r)) for r in of[c][p] if r)):
                continue
            rs = sorted({r for r in of[c][p] if r is not None}, key=q)
            pad = "    " * (depth + 1)
            if p in code_props:
                spaces = populated.get((c, p), set()) if populated else set()
                vals = [r for r in rs if local(r) not in ("Code", "Terminology")]  # schema value set, labelled
                vals = [f"{q(r)} ({g.value(r, RDFS.label)})" if g.value(r, RDFS.label) else q(r)
                        for r in vals if not spaces or any(str(r).startswith(s) for s in spaces if s)]
                vals = vals or sorted(f"{pre}:" for pre, ns in g.namespaces() if str(ns) in spaces)  # else: what the data uses
                if linked:
                    raw = [r for r in rs if local(r) not in ("Code", "Terminology")]
                    out.append(code_line(raw, pad, p) if raw else f"{pad}{q(p)} -> code")
                    continue
                rec = (recorded or {}).get((c, p), [])
                out.append(f"{pad}{q(p)} -> code" + (f" in {{{', '.join(vals[:3])}{', ...' if len(vals) > 3 else ''}}}" if vals else "")
                           + (f"; recorded: {', '.join(q(x) for x in rec[:30])}{f' (+{len(rec) - 30} more)' if len(rec) > 30 else ''}" if rec else ""))
            elif not rs or all(str(r).startswith(str(XSD)) for r in rs):
                out.append(f"{pad}{q(p)} {' | '.join(q(r) for r in rs) or 'value'}")
            elif p in value_props:
                inds = sorted(q(i) for r in rs for i in g.subjects(RDF.type, r))
                out.append(f"{pad}{q(p)} one of {', '.join(inds[:8])}{', ...' if len(inds) > 8 else ''}")
            else:
                ks = [k for r in rs[:1] for k in kinds(r)] if linked else []
                again = linked and done is not None and all(r in done for r in rs[:2])
                cap = 12 if linked else 4
                out.append(f"{pad}{q(p)} -> {' | '.join(q(r) for r in rs[:cap])}{' | ...' if len(rs) > cap else ''}"
                           + (f" (kinds: {', '.join(ks[:12])}{', ...' if len(ks) > 12 else ''})" if ks else "")
                           + (" (as above)" if again and any(of.get(r) for r in rs[:2]) else ""))
                for r in rs[:2]:
                    if r not in seen and r not in concepts and r != patient and depth < 3 and of.get(r) and not NOEXPAND.search(local(r)) \
                            and not (linked and r in done):
                        done is not None and done.add(r)
                        describe(r, depth + 1, seen | {r}, out, done)
        return out

    head = lambda c: f"{q(c)} ({label(c)}){': ' + comment(c) if comment(c) else ''}"
    blocks = {q(c): "\n".join([head(c)] + describe(c, 0, {c}, [], set() if linked else None)) for c in concepts}
    docs = {}
    if linked:  # every sphn class gets a retrieval document; one no block names gets its own block
        note = lambda c, k: " ".join(re.sub(r"\s+", " ", str(v)) for v in g.objects(c, k))
        for c in sorted({c for c in g.subjects(RDF.type, OWL.Class) if str(c).startswith(main)} - roots, key=q):
            docs[q(c)] = " ".join(filter(None, [label(c), note(c, SKOS.definition) or note(c, RDFS.comment),
                                                note(c, SKOS.scopeNote), note(c, SKOS.note)]))
        named = {m for b in blocks.values() for m in re.findall(r"sphn:[A-Z]\w*", b)}
        for c in sorted({c for c in g.subjects(RDF.type, OWL.Class) if str(c).startswith(main) and of.get(c)} - roots, key=q):
            if q(c) not in named and q(c) not in blocks and local(c) not in ("Code", "Terminology", "SubjectPseudoIdentifier"):
                blocks[q(c)] = "\n".join([head(c)] + [f"    (kinds: {', '.join(kinds(c))})"] * bool(kinds(c)) + describe(c, 0, {c}, [], set()))
    prefixes = {p: str(n) for p, n in g.namespaces() if p and not str(n).startswith(VOCAB)}  # as declared in the files
    terms = [p for p in prefixes if p not in ("xsd",) and not str(link).startswith(prefixes[p])]
    header = (f"Record types are listed as blocks: a record's properties, and with '->' the nodes they lead to (follow them "
              f"for nested values). Codes are nodes typed with their terminology class (prefixes: {', '.join(t + ':' for t in terms)}). "
              f"'code under X': the code's class is below X; X is only the root of the allowed codes, never a value, so do not "
              f"filter on X: filter on a class below it, or group by the class. 'one of' lists the allowed values; 'kinds' the "
              f"subclasses of a node's class. A code node belongs to a single record: to group, compare, count or return codes, "
              f"bind the class (?code rdf:type ?class, without the inference option) and use ?class, never ?code or STR(?code).") \
        if linked else (f"Record types are listed as blocks: a record's properties, and with '->' the nodes they lead to (follow them "
              f"for nested values). Codes ({', '.join(q(p) for p in sorted(code_props, key=lambda p: -len(props[p]))[:4])}, "
              f"...) are nodes typed with their terminology class (prefixes: {', '.join(t + ':' for t in terms)}). "
              f"'one of' lists the individuals a value-set property takes; 'recorded:' lists codes on enough records to be "
              f"listed (rarer codes may exist: still query them).")
    return dict(
        schema_ns=main, prefixes={**prefixes, "xsd": str(XSD)}, patient_class=str(patient), link=str(link),
        id_props=sorted(map(str, ids)), code_props=sorted(map(str, code_props)), literal_props=sorted(map(str, literal_props)),
        value_props=sorted(map(str, value_props)), terms=sorted({str(s) for s in g.subjects() if str(s).startswith(main)}),
        header=header, index=", ".join(f"{q(c)} ({label(c)})" for c in concepts), blocks=blocks,
        core=[q(c) for c in concepts if CORE.match(local(c))], **({"docs": docs} if linked else {}),
    ), cands[:3]


def main():
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("files", nargs="+", help="schema (OWL/RDFS) and SHACL, any RDF format")
    a.add_argument("--out", default="nl2q/config/schema.json")
    a.add_argument("--codes", default="nl2q_data/codes.json", help="labels of the codes in use (needs --endpoint; never commit)")
    a.add_argument("--patient", help="patient class IRI (default: guessed)")
    a.add_argument("--link", help="record-to-patient property IRI (default: guessed)")
    a.add_argument("--data", nargs="*", default=[], help="patient file(s): keep only what is populated")
    a.add_argument("--endpoint", help="SPARQL endpoint of the data: keep only what is populated")
    a.add_argument("--ontology-graph", default="https://biomedit.ch/rdf/sphn-resource/global")
    a.add_argument("--linked", action="store_true", help="the linked card (schema only: no --data/--endpoint), e.g. --out nl2q/config/card.json")
    a.add_argument("--min-records", type=int, default=10, help="list a code as recorded only if it is on at least this many records")
    a = a.parse_args()
    g = Graph(bind_namespaces="core")
    for f in a.files:
        g.parse(f)
    populated, d = None, None
    if a.data:
        d = Graph()
        for f in a.data:
            d.parse(f)
        populated = successors(observed(d))
    elif a.endpoint:
        populated = successors(observed(endpoint=a.endpoint))
    cfg, cands = build(g, a.patient, a.link, populated, linked=a.linked)
    recorded = None
    if populated:  # second pass: now that the code properties are known, list the codes the data really uses
        recorded = codes_in_use([URIRef(p) for p in cfg["code_props"]], a.min_records, d, a.endpoint)
        cfg, cands = build(g, a.patient, a.link, populated, recorded)
    with open(a.out, "w") as f:
        json.dump(cfg, f, indent=1, ensure_ascii=False)
    if a.endpoint and recorded:
        labels = code_labels(a.endpoint, {c for cs in recorded.values() for c in cs}, a.ontology_graph)
        Path(a.codes).parent.mkdir(parents=True, exist_ok=True)
        with open(a.codes, "w") as f:
            json.dump(labels, f, ensure_ascii=False)
        print(f"code labels    {len(labels)} -> {a.codes} (restricted terminologies: never commit)")
    sizes = sorted(len(b) for b in cfg["blocks"].values())
    print(f"patient class  {cfg['patient_class']}\nlink           {cfg['link']}   (candidates: {cands})\n"
          f"record types   {len(cfg['blocks'])}   core: {cfg['core']}\nterms          {len(cfg['terms'])}; code properties "
          f"{len(cfg['code_props'])}, literal {len(cfg['literal_props'])}, value sets {len(cfg['value_props'])}\n"
          f"REVIEW         identifiers (never released, except on code nodes): {[local(p) for p in cfg['id_props']]}\n"
          f"card           header {len(cfg['header'])} chars, index {len(cfg['index'])}, blocks median "
          f"{sizes[len(sizes) // 2] if sizes else 0} / max {sizes[-1] if sizes else 0} chars\n-> {a.out}")


if __name__ == "__main__":
    main()
