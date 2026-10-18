"""validate()'s extra feedback with schema linking on (NL2Q_LINK=1), after the syntax and term checks: a property the
subject's class does not have, SQL words SPARQL lacks, a code used as a value, a variable returned or grouped but bound
nowhere. Static (the
query text and domains.json, built from the schema files): no database, no model. Like validate() it speaks only of
syntax and schema, never of compliance, and judge() never reads it."""
import logging, re
from collections import defaultdict
from rdflib import RDF, RDFS, Literal, URIRef, Variable
from rdflib.paths import InvPath, MulPath, SequencePath
from rdflib.plugins.sparql.parserutils import CompValue
from .. import schema as SC
from .rules import NOBODY, NOT_CODE, Invalid, _both_ways, _end_step, _short, _steps, _walk, parse

log = logging.getLogger(__name__)
_w = r"(?<![\w:?$])"  # not inside a name, a prefixed name or a variable
SQL = [(re.compile(_w + r"CASE\s+WHEN\b", re.I), "use IF(cond, a, b) instead of CASE WHEN"),
       (re.compile(_w + r"IS\s+NOT\s+NULL\b", re.I), "use BOUND(?x) instead of IS NOT NULL"),
       (re.compile(_w + r"IS\s+NULL\b", re.I), "use !BOUND(?x) instead of IS NULL"),
       (re.compile(_w + r"LEAST\s*\(", re.I), "use IF(?a < ?b, ?a, ?b) instead of LEAST(...)"),
       (re.compile(_w + r"GREATEST\s*\(", re.I), "use IF(?a > ?b, ?a, ?b) instead of GREATEST(...)"),
       (re.compile(_w + r"OVER\s*\(", re.I), "no window functions in SPARQL: compute the total in a subquery and join it")]
# Identity properties get no class feedback: a retry must not steer a query about identifiers to other properties.
SILENT = {str(p) for p in SC.ID_PROPS | {SC.LINK}}


def sql(query):
    """The SQL words in the query (strings, IRIs and comments aside) with their SPARQL form, or None."""
    code = NOT_CODE.sub(" ", query)
    found = [say for rx, say in SQL if rx.search(code)]
    return f"SQL syntax: {'; '.join(found)}" if found else None


def feedback(query):
    """The first of: a property the subject's class lacks, SQL words, a code used as a value, an unbound variable;
    None when there is none."""
    try:
        q, _ = parse(query)
        if q is None:  # a SPARQL Update
            return sql(query)
        out = wrong_property(q.algebra) or sql(query) or codes(q.algebra) or unbound(q.algebra)
        return out and out[:300]
    except Invalid:  # validate() says it first
        return sql(query)
    except Exception:  # a bug in the feedback is not the generator's to fix
        log.exception("schema feedback failed")
        return None


# ---------------------------------------------------------------- 1. a property the class does not have
def _bgps(node, neg=False):
    """(triples, negated) of every basic graph pattern; negated under NOT EXISTS, !EXISTS and MINUS's right side."""
    if isinstance(node, CompValue):
        if node.name == "BGP":
            yield node["triples"], neg
            return
        for k, v in node.items():
            if not k.startswith("_"):  # the translated EXISTS pattern is an attribute, as in rules._walk
                flip = node.name in ("Builtin_NOTEXISTS", "UnaryNot") or node.name == "Minus" and k == "p2"
                yield from _bgps(vars(node).get(k, v), neg or flip)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _bgps(v, neg)


def _type(s, p, o):
    """The sphn: class (local name) a triple gives its subject: ?x a sphn:X, or ?x rdf:type/rdfs:subClassOf* sphn:X."""
    steps = p.args if isinstance(p, SequencePath) else [p]
    if steps[0] == RDF.type and all(isinstance(x, MulPath) and x.path == RDFS.subClassOf for x in steps[1:]) \
            and isinstance(o, URIRef) and str(o).startswith(SC.NS):
        return str(o)[len(SC.NS):]


def _first(s, p, o):
    """(node, property) for a path's first step when it is one forward IRI (or the whole path is ^IRI), else None."""
    if isinstance(p, InvPath) and isinstance(p.arg, URIRef):
        return o, p.arg
    while isinstance(p, SequencePath):
        p = p.args[0]
    return (s, p) if isinstance(p, URIRef) else None


def _words(name):
    return {w.lower() for w in re.findall(r"[A-Z][a-z]*|[a-z]+", name)} - {"has"}


def wrong_property(alg):
    """'sphn:p is not a property of sphn:X: use one of ...' for ?x sphn:p ... on a node typed sphn:X. A class has its
    own, inherited and its subclasses' properties (domains.py); types under NOT EXISTS or MINUS do not count outside."""
    from .domains import load  # here: python -m nl2q.compliance.domains runs before the package imports it
    props, sub = load()
    bgps = list(_bgps(alg))
    sure = defaultdict(set)
    for ts, neg in bgps:
        for t in ts if not neg else ():
            if c := _type(*t):
                sure[t[0]].add(c)
    for ts, _ in bgps:
        here = defaultdict(set)
        for t in ts:
            if c := _type(*t):
                here[t[0]].add(c)
        for t in ts:
            node, p = _first(*t) or (None, None)
            if p is None or str(p) not in SC.TERMS or str(p) in SILENT or not str(p).startswith(SC.NS):
                continue
            name, cs = str(p)[len(SC.NS):], sure[node] | here[node]
            if not cs or cs - props.keys() or any(name in props[d] for c in cs for d in [c, *sub.get(c, ())]):
                continue  # untyped, typed outside the map (sphn:SPHNConcept), or the property is there
            near = sorted(set().union(*(props[c] for c in cs)), key=lambda x: (-len(_words(x) & _words(name)), x))
            text = f"sphn:{name} is not a property of {' / '.join(f'sphn:{c}' for c in sorted(cs))}"
            for k in range(min(len(near), 6), 0, -1):
                if len(out := f"{text}: use one of {', '.join(f'sphn:{x}' for x in near[:k])}") <= 300:
                    return out
            return text[:300]


# ---------------------------------------------------------------- 3. a code used as a value
def codes(alg):
    """In the data a code is a node typed with its terminology class (?r sphn:hasCode ?c . ?c a icd-10-gm:E11): a
    constant as the object of a code property (any path ending with one), or a code node compared with =, !=, IN or
    sameTerm to another or to a constant. Class variables (objects of rdf:type) are what to compare instead."""
    triples = _both_ways([t for n in _walk(alg) if isinstance(n, CompValue) and n.name == "BGP" for t in n["triples"]])
    code = lambda p: not isinstance(p, Variable) and _end_step(p, True) == "fwd" and bool(_steps(p)[1] & SC.CODE_PROPS)
    for s, p, o in triples:
        if isinstance(o, URIRef) and o != NOBODY and code(p):
            path, c = _short(p), _short(o)
            return f"codes are nodes: write {_short(s) if isinstance(s, Variable) else '?x'} {path} ?c . ?c a {c} (not {path} {c})"
    classes = {o for s, p, o in triples if not isinstance(p, Variable) and _steps(p)[2] & {RDF.type, RDFS.subClassOf}}
    nodes = {o for s, p, o in triples if isinstance(o, Variable) and code(p)} - classes
    for n in _walk(alg):
        if isinstance(n, CompValue) and (n.name == "Builtin_sameTerm" or n.name == "RelationalExpression" and n["op"] in ("=", "!=", "IN", "NOT IN")):
            sides = [n["arg1"], n["arg2"]] if n.name == "Builtin_sameTerm" else [n["expr"], *(n["other"] if isinstance(n["other"], list) else [n["other"]])]
            op = "!=" if n.get("op") in ("!=", "NOT IN") else "="
            vs, cs = [x for x in sides if isinstance(x, Variable) and x in nodes], [x for x in sides if isinstance(x, (URIRef, Literal))]
            if len(vs) > 1:
                return (f"?{vs[0]} and ?{vs[1]} are code nodes (one per record): compare their classes "
                        f"(?{vs[0]} rdf:type ?ca . ?{vs[1]} rdf:type ?cb, FILTER(?ca {op} ?cb))")
            if vs and cs:
                c = _short(cs[0]) if isinstance(cs[0], URIRef) else "<terminology class>"
                return f"?{vs[0]} is a code node (one per record), not a code: compare its class (?{vs[0]} rdf:type ?ca, FILTER(?ca {op} {c}))"


# ---------------------------------------------------------------- 4. a variable bound nowhere
def unbound(alg):
    """'?v is grouped/returned but never bound in WHERE' for a GROUP BY key, a projected variable or one read by a
    projected expression or aggregate, that no triple pattern, GRAPH, BIND, AS, VALUES or SERVICE binds anywhere in
    the query (subqueries included: lenient, a variable of the same name anywhere counts)."""
    levels, keys = [], set()
    for proj in (n for n in _walk(alg) if isinstance(n, CompValue) and n.name == "Project"):
        level = [n for n in _walk(proj["p"], into_sub=False) if isinstance(n, CompValue)]
        aggs = {a["res"]: a for n in level if n.name == "AggregateJoin" for a in n["A"]}
        # rdflib projects a non-aggregated variable of a grouped query as SAMPLE(?v) AS ?v: that binds nothing
        keys |= {id(n) for n in level if n.name == "Extend" and isinstance(n["expr"], Variable)
                 and (a := aggs.get(n["expr"])) is not None and a.name == "Aggregate_Sample" and a["vars"] == n["var"]}
        levels.append((proj, level, aggs))
    bound = set()
    for n in _walk(alg):
        if isinstance(n, CompValue):
            if n.name == "BGP":
                bound |= {x for t in n["triples"] for x in t}
            elif n.name == "Graph":
                bound.add(n["term"])
            elif n.name == "ServiceGraphPattern":  # its pattern stays a parse tree: any variable in it counts
                bound |= {x for x in _walk(n) if isinstance(x, Variable)}
            elif n.name == "Extend" and id(n) not in keys:
                bound.add(n["var"])
            elif n.name == "values":
                bound |= {x for row in n["res"] for x in row}
    variables = lambda e: [x for x in _walk(e) if isinstance(x, Variable)]
    for proj, level, aggs in levels:
        grouped = list(dict.fromkeys(x for n in level if n.name == "Group" for x in variables(n.get("expr") or [])))
        used = [(x, None) for x in [*grouped, *proj["PV"]]]
        for n in level:  # the expressions (... AS ?v) of a projected or grouped ?v, aggregates included
            if n.name == "Extend" and id(n) not in keys and (n["var"] in proj["PV"] or n["var"] in grouped):
                where = "used in GROUP BY" if n["var"] in grouped else "used in SELECT"
                for x in variables(n["expr"]):
                    a = aggs.get(x)
                    used += [(y, where) for y in ([x] if a is None else variables(a["vars"]) if a["vars"] != "*" else [])]
        for x, where in used:
            if not str(x).startswith("__agg_") and x not in bound:
                role = where or " and ".join(r for r, xs in (("grouped", grouped), ("returned", proj["PV"])) if x in xs)
                return f"?{x} is {role} but never bound in WHERE: bind it (triple pattern, BIND or VALUES) or remove it"
