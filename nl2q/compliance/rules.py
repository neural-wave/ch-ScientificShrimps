"""Compliance check for a generated SPARQL query: deterministic, in code, no model and no tokens.
Implements the official constraint set (Query_Compliance.md) plus the API's validity rule (the query must be valid
for Virtuoso; its DEFINE and OPTION(...) extensions are allowed and set aside before rdflib parses the query).

The check reads what a query *returns*. A variable that carries a patient (GRAPH ?g, an alias such as
BIND(?g AS ?p), the object of sphn:hasSubjectPseudoIdentifier), a record of the patient data (any node: a diagnosis,
a lab test, a code node) or an identifier value may be counted, but never returned, grouped by, sampled or compared
with a fixed value. Inside subqueries anything goes, as long as the outer query aggregates (the official examples
group by ?g in a subquery and count at the top)."""
import re
from collections import Counter
from rdflib import RDF, XSD, Literal, URIRef, Variable
from rdflib.paths import AlternativePath, InvPath, MulPath, NegatedPath, Path, SequencePath
from rdflib.plugins.sparql import prepareQuery
from rdflib.plugins.sparql.parser import parseUpdate
from rdflib.plugins.sparql.parserutils import CompValue
from .. import schema as SC
from .. import settings as S

RULES = {
    "R1": "queries about a single, identifiable patient are not allowed",
    "R2": "queries must always return aggregated information across all patients",
    "R3": "queries that match the whole graph without restriction are not allowed",
    "R4": "only read-only SELECT queries are allowed",
    "R5": "no identifiers may be exposed (patient, record, laboratory or diagnosis IDs)",
}
VIRTUOSO_PREFIXES = {"rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
                     "xsd": "http://www.w3.org/2001/XMLSchema#", "owl": "http://www.w3.org/2002/07/owl#", "bif": "bif:",
                     "sql": "sql:", "skos": "http://www.w3.org/2004/02/skos/core#", "dc": "http://purl.org/dc/elements/1.1/",
                     "dcterms": "http://purl.org/dc/terms/", "foaf": "http://xmlns.com/foaf/0.1/"}  # predefined by Virtuoso
RESOURCE = "https://biomedit.ch/rdf/sphn-resource/"  # data IRIs sit directly below it, terminologies in sub-folders
COMPARE = {"RelationalExpression", "Builtin_sameTerm", "Builtin_REGEX", "Builtin_STRSTARTS", "Builtin_STRENDS", "Builtin_CONTAINS"}
OPAQUE = COMPARE | {"ConditionalAndExpression", "ConditionalOrExpression", "UnaryNot", "Builtin_BOUND", "Builtin_EXISTS",
                    "Builtin_NOTEXISTS", "Builtin_isIRI", "Builtin_isBLANK", "Builtin_isLITERAL", "Builtin_isNUMERIC",
                    "Builtin_LANGMATCHES", "Builtin_LANG", "Builtin_DATATYPE", "Builtin_STRLEN", "Builtin_YEAR",
                    "Builtin_MONTH", "Builtin_DAY", "Builtin_HOURS", "Builtin_MINUTES", "Builtin_SECONDS"}  # results carry no identity
CODE_CLASS = URIRef(SC.NS + "Code")  # the generic code node: in the data its identifier is a record ID (ID-<patient>...)
CODING_SYSTEM = URIRef(SC.NS + "hasCodingSystemAndVersion")
# A file hash's algorithm is one of three value-set individuals (sphn-individual:MD5, SHA256, SHA512), but the config
# cannot list hasAlgorithm in value_props: on an imaging frame (2026.1) it reaches a DataCompressionAlgorithm node, which
# has properties of its own and stays a record.
HASH, HAS_HASH, HAS_ALGORITHM = (URIRef(SC.NS + n) for n in ("Hash", "hasHash", "hasAlgorithm"))
# The namespaces of the patient data's properties: sphn:, and dct: on the data's own DataRelease node (one per patient,
# dct:conformsTo). The subject of any of them is a node of the data. A property from elsewhere matches nothing there.
DATA_NS = (SC.NS, "http://purl.org/dc/terms/")
# SPHN 2024.2 terms of the mock data that 2026.1 dropped (its DataRelease conforms to sphn/2024/2). Queries are written in
# 2026.1 and validate() keeps steering the generator there (these stay out of SC.TERMS), but the verdict must still read
# them right: an admission date is a value, not a record (object types from the data's edge statistics).
_legacy = lambda *names: frozenset(URIRef(SC.NS + n) for n in names)
LITERAL_PROPS = SC.LITERAL_PROPS | _legacy("hasAdmissionDateTime", "hasDischargeDateTime", "hasFirstAdministrationDateTime",
                                           "hasLastAdministrationDateTime", "hasGenomicSequence")
VALUE_PROPS = SC.VALUE_PROPS | _legacy("hasRank", "hasCellMorphology")
CODE_PROPS = SC.CODE_PROPS | _legacy("hasAcquistionCode")  # sic: the data's spelling of hasAcquisitionCode
SHAPES = [re.compile(x) for x in S.IDENTIFIER_SHAPES]
# Virtuoso built-ins that act on the server instead of reading the store: SQL, shell, files, HTTP; sql: calls procedures
SERVER_SIDE = re.compile(r"^(bif:(exec|system|run_executable|file_\w+|string_to_file|http_\w+|xml_uri_get|sparql_exec\w*)|sql:.+)$", re.I)


class Invalid(Exception):
    """Not valid SPARQL for Virtuoso: the generator retries; if it never manages, the query is not compliant."""


def strip_virtuoso(q):
    """(standard SPARQL, [(DEFINE name, value)]): DEFINE lines before the prologue, OPTION (...) after triple patterns."""
    defines = []
    while m := re.match(r'\s*(?:#[^\n]*\n\s*)*DEFINE\s+([\w-]+:[\w-]+)\s+("[^"]*"|<[^>]*>|[^\s{]+)', q, re.I):
        defines.append((m.group(1).lower(), m.group(2).strip('"<>')))
        q = q[m.end():]
    while m := re.search(r"\bOPTION\s*\(", q, re.I):
        depth, j = 0, m.end() - 1
        while j < len(q):
            depth += {"(": 1, ")": -1}.get(q[j], 0)
            if depth == 0:
                break
            j += 1
        else:
            raise Invalid("unbalanced parentheses in OPTION (...)")
        if (rs := re.match(r'\s*inference\s+"([^"]*)"', q[m.end():j], re.I)) and rs.group(1) != S.INFERENCE:
            raise Invalid(f'unknown inference rule set "{rs.group(1)}": use OPTION (inference "{S.INFERENCE}")')
        q = q[:m.start()] + " " + q[j + 1:]
    return q, defines


# rdflib names the aggregates of a query ?__agg_1__, ?__agg_2__, ...: a variable of the query with such a name merges
# with them and the checker misreads it (fuzz finding). Strings, IRIs and comments are set aside before looking.
RESERVED = re.compile(r"[?$](__agg_\w*)")
NOT_CODE = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|<[^<>"{}|^`\\\s]*>|#[^\n]*')


def parse(q):
    """(rdflib Query, or None for a SPARQL Update, DEFINEs). Raises Invalid if the text is not valid SPARQL."""
    text, defines = strip_virtuoso(q)
    if m := RESERVED.search(NOT_CODE.sub(" ", text)):
        raise Invalid(f"the variable name ?{m.group(1)} is reserved by the query parser: rename the variable")
    for _ in range(len(VIRTUOSO_PREFIXES)):
        try:
            return prepareQuery(text), defines
        except Exception as e:
            m = re.search(r"Unknown namespace prefix : ([\w-]+)", str(e))
            if m and m.group(1) in VIRTUOSO_PREFIXES:  # Virtuoso knows these without a PREFIX line
                text = f"PREFIX {m.group(1)}: <{VIRTUOSO_PREFIXES[m.group(1)]}>\n" + text
                continue
            try:
                parseUpdate(text)
                return None, defines
            except Exception:
                raise Invalid(f"SPARQL syntax error: {str(e)[:200]}") from None
    raise Invalid("too many undeclared prefixes")


def _walk(node, into_sub=True):
    """Every algebra node and leaf, including patterns nested inside expressions (EXISTS); into_sub=False stops at
    subqueries (ToMultiSet), to look at the outer query alone."""
    yield node
    if isinstance(node, CompValue) and (into_sub or node.name != "ToMultiSet"):
        for k, v in node.items():
            if not k.startswith("_"):  # rdflib stores the translated EXISTS pattern as an attribute
                yield from _walk(vars(node).get(k, v), into_sub)  # shadowing the raw parse tree
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _walk(v, into_sub)


def _carriers(e):
    """Variables whose value flows into the result of expression e: IF branches, COALESCE, string and arithmetic
    transforms, casts, functions. Conditions (comparisons, BOUND, EXISTS, ...) return a boolean, not an identity."""
    if isinstance(e, Variable):
        return {e}
    if not isinstance(e, CompValue) or e.name in OPAQUE:
        return set()
    if e.name == "Builtin_IF":
        return _carriers(e["arg2"]) | _carriers(e["arg3"])
    out = set()
    for k, v in e.items():
        if not k.startswith("_"):
            for x in (v if isinstance(v, list) else [v]):
                out |= _carriers(x)
    return out


def _steps(p):
    """(IRIs a path starts with, IRIs it ends with, all its IRIs); a plain predicate is a one-step path."""
    if isinstance(p, URIRef):
        return {p}, {p}, {p}
    if isinstance(p, SequencePath):
        parts = [_steps(a) for a in p.args]
        return parts[0][0], parts[-1][1], set().union(*(x[2] for x in parts))
    inner = [p.path] if isinstance(p, MulPath) else [p.arg] if isinstance(p, InvPath) else list(getattr(p, "args", []))
    every = set().union(*(_steps(a)[2] for a in inner)) if inner else set()
    return every, every, every  # alternatives, inverses, closures: any step may come first or last


def _inverse(p):
    """Whether a path walks a step backwards (^p) anywhere."""
    if isinstance(p, InvPath):
        return True
    return any(_inverse(a) for a in ([p.path] if isinstance(p, MulPath) else getattr(p, "args", [])))


def _reverse(p):
    """The same path walked from its object to its subject: (?s ^p1/p2 ?o) is (?o ^p2/p1 ?s)."""
    if isinstance(p, InvPath):
        return p.arg
    if isinstance(p, SequencePath):
        return SequencePath(*[_reverse(a) for a in reversed(p.args)])
    if isinstance(p, AlternativePath):
        return AlternativePath(*[_reverse(a) for a in p.args])
    if isinstance(p, MulPath):
        return MulPath(_reverse(p.path), p.mod)
    return InvPath(p)


def _end_step(p, last):
    """Whether the first (last=False) or last step of a path is walked forwards ("fwd"), backwards ("inv") or either."""
    if isinstance(p, URIRef):
        return "fwd"
    if isinstance(p, InvPath):
        return {"fwd": "inv", "inv": "fwd"}.get(_end_step(p.arg, not last), "both")
    if isinstance(p, SequencePath):
        return _end_step(p.args[-1 if last else 0], last)
    if isinstance(p, MulPath):
        return _end_step(p.path, last)
    ends = {_end_step(a, last) for a in getattr(p, "args", [])} if isinstance(p, AlternativePath) else {"both"}
    return ends.pop() if len(ends) == 1 else "both"


NOBODY = URIRef("urn:nl2q:reversed-end")  # stands for an end that the reversed copy must not give a role to


def _both_ways(triples):
    """The triples, plus each triple whose path starts or ends with an inverse step, read from the other end. _steps()
    reads a path forwards, so (?id ^sphn:hasIdentifier ?sub) alone gives the identifier role to ?sub instead of ?id
    (fuzz finding). The reversed copy gives such an end the role its own step implies; the end of a forward step keeps
    the role the original triple gives it (NOBODY in the copy). It only adds roles: never less strict."""
    out = list(triples)
    for s, p, o in triples:
        if isinstance(p, Path) and _inverse(p):
            first, last = _end_step(p, False), _end_step(p, True)
            if first != "fwd" or last != "fwd":
                out.append((o if last != "fwd" else NOBODY, _reverse(p), s if first != "fwd" else NOBODY))
    return out


def _instance(u):
    """An IRI (or a string holding one) of the patient data itself, a patient graph or a record: naming one targets an
    individual."""
    s = str(u)
    rest = s[len(RESOURCE):] if s.startswith(RESOURCE) else None
    return s.startswith(S.GRAPH_PREFIX) and s != S.GRAPH_PREFIX or rest is not None and rest not in ("", "global") and "/" not in rest


def _local_aggs(n):
    """The aggregates under an Extend, in its own query level (rdflib reuses __agg_N__ names across subqueries)."""
    m = n.get("p")
    while isinstance(m, CompValue) and m.name != "AggregateJoin":
        m = m.get("p")
    return {a["res"]: a for a in m["A"]} if isinstance(m, CompValue) else {}


def _local_keys(n):
    """The GROUP BY variables of an Extend's own query level. rdflib projects a group key as SAMPLE(key)."""
    m = n.get("p")
    while isinstance(m, CompValue) and m.name != "AggregateJoin":
        m = m.get("p")
    g = m.get("p") if isinstance(m, CompValue) else None
    return {k for k in (g.get("expr") or []) if isinstance(k, Variable)} if isinstance(g, CompValue) else set()


def _taint(nodes, base):
    """Close `base` under BIND / projection aliases and aggregates other than COUNT (MIN(?g) is still ?g)."""
    out, grew = set(base), True
    while grew:
        grew = False
        for n in nodes:
            if n.name != "Extend" or n["var"] in out:
                continue
            aggs, hit = None, False
            for v in _carriers(n["expr"]):
                if str(v).startswith("__agg_"):
                    aggs = _local_aggs(n) if aggs is None else aggs
                    a = aggs.get(v)
                    hit |= a is not None and not a.name.endswith("_Count") and a["vars"] != "*" and bool(_carriers(a["vars"]) & out)
                else:
                    hit |= v in out
            if hit:
                out.add(n["var"])
                grew = True
    return out


def _fixed(nodes, values):
    """Variables whose value the query itself sets: bound by VALUES, or by BIND from constants and such variables, as in
    BIND(IRI(CONCAT("http://example.org/graph/", "0042")) AS ?g). Aggregates are never fixed."""
    out, grew = {var for row in values for var in row}, True
    while grew:
        grew = False
        for n in nodes:
            if n.name == "Extend" and n["var"] not in out:
                refs = {r for r in _walk(n["expr"]) if isinstance(r, Variable)}
                if refs <= out and not any(str(r).startswith("__agg_") for r in refs):
                    out.add(n["var"])
                    grew = True
    return out


def _code_ident(s, p, codes):
    """Whether the identifier a triple reads belongs to a code node: the subject is one, or the path reaches the
    identifier through a code property (?dx sphn:hasCode/sphn:hasIdentifier "E78.5")."""
    if isinstance(p, SequencePath) and len(p.args) > 1:
        return bool(_steps(p.args[-2])[1] & CODE_PROPS)
    return s in codes


def _hash_algorithm(s, p, hashes):
    """Whether a triple reads the algorithm of a file hash, a value: the subject is a sphn:Hash node, or the path is
    ?file sphn:hasHash/sphn:hasAlgorithm ?alg."""
    if isinstance(p, SequencePath) and len(p.args) > 1:
        return p.args[-2] == HAS_HASH and p.args[-1] == HAS_ALGORITHM
    return p == HAS_ALGORITHM and s in hashes


def _occurs(node):
    """How often each variable is bound under node: in triple patterns (EXISTS ones included), BIND and VALUES."""
    n = [x for m in _walk(node) if isinstance(m, CompValue) for x in
         ([v for t in m["triples"] for v in t] if m.name == "BGP" else [m["var"]] if m.name == "Extend"
          else [v for row in m["res"] for v in row] if m.name == "values" else [])]
    return Counter(x for x in n if isinstance(x, Variable))


def _sure(node, find, occurs):
    """What `find` reads in the triple patterns that hold on every result row where its variable is bound. A MINUS
    part or a FILTER (NOT) EXISTS pattern holds on no row; an OPTIONAL part or one UNION branch holds only for variables
    bound nowhere else, since they are unbound when it fails. occurs: _occurs of the whole query."""
    if not isinstance(node, CompValue):
        return set()
    if node.name == "BGP":
        return find(node["triples"])
    sure = lambda part: _sure(part, find, occurs)
    own = lambda part: {x for x in sure(part) if isinstance(x, Variable) and occurs[x] == _occurs(part)[x]}
    if node.name == "Union":
        return sure(node["p1"]) & sure(node["p2"]) | own(node["p1"]) | own(node["p2"])
    if node.name == "LeftJoin":
        return sure(node["p1"]) | own(node["p2"])
    if node.name == "Minus":
        return sure(node["p1"])
    if node.name == "Join":
        return sure(node["p1"]) | sure(node["p2"])
    if node.name == "ToMultiSet":  # a subquery: only what it projects reaches the outer query
        m = node["p"]
        while isinstance(m, CompValue) and m.name != "Project":
            m = m.get("p")
        return sure(node["p"]) & set(m["PV"]) if isinstance(m, CompValue) else set()
    return sure(node.get("p"))  # Graph, Filter (not its expression), Extend, ...


def _scopes(alg):
    """[(the outer query or a subquery's pattern, the triple patterns it matches itself)]: a subquery's own patterns
    belong to it alone, and only what it projects reaches the query around it."""
    out, todo = [], [alg]
    while todo:
        part = todo.pop()
        ns = [n for n in _walk(part, into_sub=False) if isinstance(n, CompValue)]
        out.append((part, [t for n in ns if n.name == "BGP" for t in n["triples"]]))
        todo += [n["p"] for n in ns if n.name == "ToMultiSet"]
    return out


def _roles(alg, graphs, fixed):
    """(patients, identifier values, records, code nodes, code identifiers not pinned to a terminology, unknown sphn:
    terms), from the triple patterns. A code node is pinned when a terminology class (?c a icd-10-gm:E11) or a coding
    system (?c sphn:hasCodingSystemAndVersion "ICD-10-GM 2024") fixes it: its identifiers are then codes such as
    "E78.5". Unpinned, a sphn:Code node's identifier is in the data a record ID carrying the patient number. Pins and
    hashes allow what is otherwise refused, so each query level takes them only from its own patterns that hold on
    every row (_sure): a pin inside OPTIONAL or FILTER NOT EXISTS on a node bound elsewhere, or inside a subquery that
    does not project the node, pins nothing (review finding)."""
    patients = {g for g in graphs if isinstance(g, Variable)}
    base = [t for n in _walk(alg) if isinstance(n, CompValue) and n.name == "BGP" for t in n["triples"]]
    codes = {o for s, p, o in base if _steps(p)[1] & CODE_PROPS} | {s for s, p, o in base if p == RDF.type and o == CODE_CLASS}
    isfixed = lambda o: isinstance(o, Variable) and o in fixed
    pins = lambda ts: {s for s, p, o in ts if p == RDF.type and (isinstance(o, URIRef) and not str(o).startswith(SC.NS) or isfixed(o))
                       or p == CODING_SYSTEM and (isinstance(o, Literal) or isfixed(o))}
    file_hashes = lambda ts: {s for s, p, o in ts if p == RDF.type and o == HASH} | {o for s, p, o in ts if p == HAS_HASH}
    occurs = _occurs(alg)
    idents, code_idents, records, unknown = set(), set(), set(), set()
    for part, own in _scopes(alg):
        pinned, hashes = _sure(part, pins, occurs), _sure(part, file_hashes, occurs)
        _role_triples(_both_ways(own), codes, pinned, hashes, patients, idents, code_idents, records, unknown)
    isvar = lambda xs: {x for x in xs if isinstance(x, Variable)}
    return isvar(patients), isvar(idents), isvar(records), codes, isvar(code_idents), unknown


def _role_triples(triples, codes, pinned, hashes, patients, idents, code_idents, records, unknown):
    """_roles for the triple patterns of one query level: adds to the five sets."""
    for s, p, o in triples:
        first, last, every = _steps(p) if not isinstance(p, Variable) else (set(), set(), set())
        unknown.update(str(t) for t in every | {o} if isinstance(t, URIRef) and str(t).startswith(SC.NS) and str(t) not in SC.TERMS)
        if SC.LINK in last:
            patients.add(o)
        if RDF.type in first and o == SC.PATIENT_CLASS:
            patients.add(s)
        if last & SC.ID_PROPS:
            if not _code_ident(s, p, codes):
                idents.add(o)
            elif isinstance(p, Path) or s not in pinned:  # a code node inside a path cannot be pinned
                code_idents.add(o)
        if RDF.type in first or any(str(t).startswith(DATA_NS) for t in first):
            records.add(s)  # a typed node or the subject of a data property (sphn:, dct:): part of a patient's data
        if RDF.type not in last and any(str(t).startswith(SC.NS) and t not in LITERAL_PROPS | VALUE_PROPS for t in last) \
                and not _hash_algorithm(s, p, hashes):
            records.add(o)  # reached through an object property: a record, a nested node or a code node


def _short(t):
    """A term or path as the query would write it, with the usual prefixes."""
    if isinstance(t, Variable):
        return f"?{t}"
    text = t.n3() if isinstance(t, (Path, Literal)) else f"<{t}>"
    for k, ns in sorted(SC.PREFIXES.items(), key=lambda kv: -len(kv[1])):
        text = re.sub(f"<{re.escape(ns)}([^>]*)>", lambda m: f"{k}:{m.group(1)}", text)
    return text


def _unrestricted(triples, nodes, fixed, graph_vars):
    """The triple patterns of groups with no class, code or value in them. Shared variables join patterns, and so do
    BIND and FILTER; the GRAPH variable does not, since every pattern of a patient shares it. A group without a constant
    matches every triple of its shape: ?x a sphn:Age . ?s a ?t scans every typed node of every patient."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def join(xs):
        xs = [x for x in xs if x not in graph_vars]
        for a, b in zip(xs, xs[1:]):
            parent[find(a)] = find(b)

    for i, t in enumerate(triples):
        join([("triple", i)] + [x for x in t if isinstance(x, Variable)])
    for n in nodes:
        if n.name in ("Extend", "Filter"):
            join(([n["var"]] if n.name == "Extend" else []) + [r for r in _walk(n["expr"]) if isinstance(r, Variable)])
    anchored = {find(("triple", i)) for i, (s, p, o) in enumerate(triples)
                if not isinstance(s, Variable) or not isinstance(o, Variable) or s in fixed or o in fixed}
    return [t for i, t in enumerate(triples) if find(("triple", i)) not in anchored]


def analyse(q):
    """(violations as [(rule, detail)], unknown sphn: terms). Raises Invalid. No violations = compliant."""
    query, defines = parse(q)
    if query is None:
        return [("R4", "the query modifies data (SPARQL Update)")], set()
    alg = query.algebra
    v = []
    for name, value in defines:
        if name.startswith(("input:grab", "get:")):
            v.append(("R4", f"DEFINE {name} makes the database fetch remote data"))
        if name in ("input:default-graph-uri", "input:named-graph-uri"):
            v.append(("R1", f"DEFINE {name} restricts the query to chosen graphs"))
    if alg.name != "SelectQuery":
        return v + [("R4", f"{alg.name.replace('Query', '').upper()} query")], set()
    nodes = [n for n in _walk(alg) if isinstance(n, CompValue)]
    triples = [t for n in nodes if n.name == "BGP" for t in n["triples"]]
    graphs = [n["term"] for n in nodes if n.name == "Graph"]
    values = [row for n in nodes if n.name == "values" for row in n["res"]]
    by_values = {var for row in values for var in row}
    fixed = _fixed(nodes, values)
    graph_vars = {g for g in graphs if isinstance(g, Variable)}
    patients, idents, records, codes, code_idents, unknown = _roles(alg, graphs, fixed)
    who = _taint(nodes, patients | idents)                               # patients and their identifiers
    sensitive = _taint(nodes, patients | idents | records | code_idents)  # ... every record node, unpinned code IDs
    rec = _taint(nodes, records - codes) - who                           # record nodes other than codes

    # R4: SELECT only, on this endpoint, reading only
    if any(n.name == "ServiceGraphPattern" for n in nodes):
        v.append(("R4", "SERVICE sends the query to another endpoint"))
    for f in dict.fromkeys(str(n["iri"]) for n in nodes if n.name == "Function" and SERVER_SIDE.match(str(n["iri"]))):
        v.append(("R4", f"{f} acts on the database server instead of reading data"))
    # R1: a single, identifiable patient
    if alg.get("datasetClause"):
        v.append(("R1", "FROM / FROM NAMED restricts the query to chosen graphs"))
    for g in graphs:
        if isinstance(g, URIRef) and str(g) != S.INFERENCE:
            v.append(("R1", f"GRAPH <{g}> reads one fixed graph"))
    consts = [x for x in _walk(alg) if isinstance(x, (URIRef, Literal))] + [x for row in values for x in row.values()]
    for u in dict.fromkeys(c for c in consts if isinstance(c, URIRef) and _instance(c) and c not in graphs):
        v.append(("R1", f"<{u}> names one specific patient or record"))
    named = set()
    for s, p, o in _both_ways(triples):
        if isinstance(o, Literal) and not isinstance(p, Variable) and _steps(p)[1] & SC.ID_PROPS and not _code_ident(s, p, codes):
            v.append(("R1", f"matches the identifier \"{o}\""))
            named.add(o)
    for c in dict.fromkeys(c for c in consts if isinstance(c, Literal) and c not in named):
        if _instance(c) or any(r.search(str(c)) for r in SHAPES):
            v.append(("R1", f"\"{c}\" names one specific patient or record"))
    for n in nodes:
        if n.name in COMPARE:
            sides = [n.get(k) for k in ("expr", "other", "arg1", "arg2", "text", "pattern", "arg")]
            flat = [x for s in sides if s is not None for x in (s if isinstance(s, list) else [s])]
            const = {i for i, x in enumerate(flat) if isinstance(x, (Literal, URIRef)) and str(x) != S.GRAPH_PREFIX
                     or isinstance(x, Variable) and x in fixed}
            rest = [x for i, x in enumerate(flat) if i not in const]
            if const and any(_carriers(x) & who for x in rest):
                v.append(("R1", "compares a patient or an identifier with a fixed value"))
            elif any(isinstance(flat[i], Literal) and flat[i].datatype in (None, XSD.string) for i in const) \
                    and any(_carriers(x) & rec for x in rest):
                v.append(("R1", "compares a record's IRI with a fixed string"))
    for x in sorted(fixed & who, key=str):
        v.append(("R1", f"{'VALUES' if x in by_values else 'BIND'} fixes ?{x}, a patient or an identifier"))
    for t in (n for n in nodes if n.name == "ToMultiSet"):  # subqueries
        m, sliced = t["p"], False
        while isinstance(m, CompValue) and m.name != "Project":
            sliced |= m.name == "Slice"
            m = m.get("p")
        if sliced and isinstance(m, CompValue) and set(m["PV"]) & who:
            v.append(("R1", "LIMIT / OFFSET in a subquery keeps particular patients"))
    used = graph_vars | {x for t in triples for x in t if isinstance(x, Variable)}
    for n in nodes:
        refs = [r for r in _carriers(n["expr"]) if str(r).startswith("__agg_")] if n.name == "Extend" and n["var"] in used else []
        aggs = _local_aggs(n) if refs else {}
        for r in refs:
            a = aggs.get(r)
            kind = a.name.split("_", 1)[1] if a is not None else ""
            key = kind == "Sample" and isinstance(a["vars"], Variable) and a["vars"] in _local_keys(n)
            if kind in ("Min", "Max", "Sample") and not key and a["vars"] != "*" and _carriers(a["vars"]) & who:
                v.append(("R1", f"{kind.upper()} picks one patient as ?{n['var']}, and the query then reads that patient's data"))
    # R3: restricted match
    if any(isinstance(p, Variable) for _, p, _ in triples) or any(isinstance(x, NegatedPath) for x in _walk(alg)):
        v.append(("R3", "a variable predicate (?s ?p ?o) matches every triple"))
    loose = _unrestricted(triples, nodes, fixed, graph_vars)
    if not triples or len(loose) == len(triples):
        v.append(("R3", "no class, code or value restricts the match"))
    elif loose:
        shown = "; ".join(" ".join(_short(x) for x in t) for t in loose[:2])
        v.append(("R3", f"{shown} is joined to no class, code or value, so it matches every such triple"))
    # R2 + R5: what the outer query returns
    def which(xs):
        return ("an identifier of code nodes that no terminology class or coding system pins down (in the data, a "
                "record ID)" if xs and set(xs) <= code_idents else "which identifies patients or records")
    proj = alg["p"]
    while proj.name != "Project":
        proj = proj["p"]
    outer = [n for n in _walk(proj["p"], into_sub=False) if isinstance(n, CompValue)]
    agg = next((n for n in outer if n.name == "AggregateJoin"), None)
    if agg is None:
        raw = " ".join(f"?{x}" for x in proj["PV"])
        v.append(("R2", f"returns {raw} row by row instead of aggregates"))
        if set(proj["PV"]) & sensitive:
            v.append(("R5", f"returns {' '.join(f'?{x}' for x in proj['PV'] if x in sensitive)}, which identify patients or records"))
        return list(dict.fromkeys(v)), unknown
    keys = {k for k in (next(n for n in outer if n.name == "Group").get("expr") or []) if isinstance(k, Variable)}
    aggs = {a["res"]: a for a in agg["A"]}
    above = []  # projection expressions: the Extends between the projection and the aggregation
    n = proj["p"]
    while isinstance(n, CompValue) and n.name != "AggregateJoin":
        if n.name == "Extend":
            above.append(n)
        n = n.get("p")
    exprs = {e["var"]: e["expr"] for e in above}
    for out in proj["PV"]:
        refs = {r for r in _walk(exprs[out], into_sub=False) if isinstance(r, Variable)} if out in exprs else {out}
        for r in refs:
            a = aggs.get(r)
            sampled = a is not None and a.name.endswith("_Sample") and isinstance(a["vars"], Variable) and a["vars"] in keys
            key = r if r in keys else a["vars"] if sampled else None
            if key is not None:
                if key in sensitive:
                    v.append(("R5", f"returns ?{key}, {which([key])}"))
            elif a is None:
                v.append(("R2", f"returns ?{r} without aggregating it"))
            elif not a.name.endswith("_Count") and a["vars"] != "*" and _carriers(a["vars"]) & sensitive:
                kind = a.name.split("_", 1)[1].upper().replace("GROUPCONCAT", "GROUP_CONCAT")
                hit = _carriers(a["vars"]) & sensitive
                v.append(("R5", f"{kind} returns the value of {', '.join(f'?{x}' for x in hit)}, {which(hit)}"))
    for k in keys - set(proj["PV"]):
        if k in sensitive:
            v.append(("R2", f"GROUP BY ?{k} returns one row per patient or record"))
    return list(dict.fromkeys(v)), unknown


def reason(violations):
    """The API's compliance_reason: every broken rule, with what in the query breaks it."""
    by = {}
    for rule, detail in violations:
        by.setdefault(rule, []).append(detail)
    return " ".join(f"{RULES[r][0].upper()}{RULES[r][1:]}: {', '.join(d)}." for r, d in sorted(by.items()))
