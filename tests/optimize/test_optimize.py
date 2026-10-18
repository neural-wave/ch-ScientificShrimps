"""The optimizer (nl2q/optimize) must never change what a query means nor its verdict: reorder keeps every BGP's
triple multiset (rdflib algebra, DEFINE and OPTION set aside as the checker does), and judge() gives the same answer
before and after on the checker's cases (mutations, leaks, fuzz), the bench, the examples, the paper's queries and the
traced generated queries.
No model, no database (optbench measures the timings)."""
import glob, json
from pathlib import Path
import pytest
from rdflib.plugins.sparql.algebra import translateGroupGraphPattern
from rdflib.plugins.sparql.parserutils import CompValue
from nl2q import settings as S
from nl2q.compliance import judge
from nl2q.compliance.rules import Invalid, parse
from nl2q.optimize import PASSES, optimize
from nl2q.translation.examples import load
from nl2q.translation.prompt import finish

ROOT = Path(__file__).parents[2]
PRAGMA = 'DEFINE sql:select-option "order"\nPREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n' \
         'PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>\nPREFIX icd-10-gm: <https://biomedit.ch/rdf/sphn-resource/icd-10-gm/>\n' \
         'PREFIX snomed: <http://snomed.info/id/>\n'
INF = 'OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global")'


def queries():
    out = [(f"{f.stem}:{t['id']}", t["sparql"]) for f in sorted((ROOT / "tests/compliance").glob("*.jsonl"))
           for t in map(json.loads, filter(str.strip, open(f)))]  # mutations, leaks and fuzz cases of the checker
    out += [(f"bench:{t['id']}", t["sparql"]) for f in sorted((ROOT / "tests/bench").glob("*.jsonl"))
            for t in map(json.loads, filter(str.strip, open(f))) if t.get("sparql")]
    out += [(f"example:{e['id']}", finish(e["sparql"])) for e in load() if e["sparql"]]
    out += [(f"paper:{f.stem}", f.read_text()) for f in sorted((ROOT / "docs/official/sphn-sparql-optimization").rglob("*.rq"))]
    traces = sorted(glob.glob("/data/scratch/lorenzo.varese@supsi.ch/night/results/**/*trace*.jsonl", recursive=True))
    seen = set()
    for f in traces:  # the overnight runs' generated queries, where this machine has them
        for r in map(json.loads, filter(str.strip, open(f))):
            if (q := r.get("sparql_query")) and q not in seen:
                seen.add(q)
                out.append((f"generated:{r['id']}#{len(seen)}", q))
    return out


QUERIES = queries()


def canon(x):
    """rdflib algebra with each BGP's triples as a sorted multiset."""
    if isinstance(x, CompValue):
        if x.name == "GroupGraphPatternSub":  # an EXISTS body inside BIND or SELECT, which rdflib leaves untranslated
            return canon(translateGroupGraphPattern(x))
        if x.name == "BGP":
            return "BGP", tuple(sorted(tuple(canon(v) for v in t) for t in x["triples"]))
        return x.name, tuple(sorted(((k, canon(v)) for k, v in x.items() if k != "_vars"), key=str))
    if isinstance(x, (list, tuple)):
        return tuple(canon(v) for v in x)
    if isinstance(x, (set, frozenset)):
        return tuple(sorted(map(canon, x), key=str))
    if isinstance(x, dict):
        return tuple(sorted((str(k), canon(v)) for k, v in x.items()))
    return x.n3() if hasattr(x, "n3") else repr(x)


def algebra(q):
    try:
        query, defines = parse(q)
    except Invalid:
        return "invalid"  # not valid SPARQL either way: judged non-compliant (test_verdict_unchanged)
    return canon(query.algebra), defines


@pytest.fixture(params=["schema", "data"])
def mode(request, monkeypatch):
    """Both kinds of knowledge: the schema and SHACL (static, the default) and the 100k counts (NL2Q_OPT_STATS=data)."""
    monkeypatch.setattr(S, "OPT_SOURCE", request.param)
    return request.param


@pytest.fixture
def data(monkeypatch):
    monkeypatch.setattr(S, "OPT_SOURCE", "data")


@pytest.mark.parametrize("name,q", QUERIES, ids=[n for n, _ in QUERIES])
def test_verdict_unchanged(name, q, mode):
    assert judge(optimize(q, PASSES)[0]) == judge(q)


@pytest.mark.parametrize("name,q", QUERIES, ids=[n for n, _ in QUERIES])
def test_reorder_keeps_the_algebra(name, q, mode):
    o = optimize(q, ("reorder",))[0]
    if o != q:
        assert algebra(o) == algebra(q)


def test_off_by_default(monkeypatch):
    monkeypatch.setattr(S, "OPTIMIZE", False)
    q = PRAGMA + f"SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ ?d a sphn:BilledDiagnosis . ?d sphn:hasCode ?c . ?c a icd-10-gm:E11 {INF} . }} }}"
    assert optimize(q) == (q, [])


def test_code_first_n03():
    """The LLM's N03: class first (2.77 s at 100k), the code triple first (0.01 s)."""
    q = PRAGMA + ("SELECT ?sex (AVG(?v) AS ?avg) WHERE { GRAPH ?g {\n"
                  "  ?dx rdf:type sphn:BilledDiagnosis ; sphn:hasCode ?dx_code ; sphn:hasSubjectAge ?age .\n"
                  f"  ?dx_code rdf:type icd-10-gm:E11 {INF} .\n"
                  "  ?age sphn:hasQuantity ?q . ?q sphn:hasValue ?v .\n"
                  "  ?s rdf:type sphn:AdministrativeSex ; sphn:hasCode ?sex .\n} } GROUP BY ?sex")
    o, passes = optimize(q, PASSES)
    body = o.split("GRAPH ?g {")[1].split("\n")
    assert passes == ["reorder"]
    assert body[1].strip() == f"?dx_code rdf:type icd-10-gm:E11 {INF} ."  # the code first, its OPTION kept
    assert body[2].strip() == "?dx sphn:hasCode ?dx_code ."  # then follow the join
    assert body[3].strip() == "?dx rdf:type sphn:BilledDiagnosis ."  # a check on a bound variable before a fan-out
    assert algebra(o) == algebra(q)


def test_paper_steps():
    """The paper's Starting/StepA order (class first) becomes the StepB/C order (code, hasCode, class); StepD, code
    first for both conditions, stays as written, and StepB/C get StepD's order for the sex condition too."""
    d = ROOT / "docs/official/sphn-sparql-optimization/Example_queries"
    for name in ("Starting", "StepA", "StepB", "StepC"):
        o = optimize((d / f"{name}.rq").read_text(), ("reorder",))[0]
        assert o.index("icd-10-gm/E11>") < o.index("?diagnosis sphn:hasCode") < o.index("?diagnosis rdf:type sphn:BilledDiagnosis")
        if name in ("StepB", "StepC"):  # one GRAPH block per condition (Starting/StepA join them through ?pseudo_id)
            assert o.index("snomed:248152002") < o.index("?administrativeSex sphn:hasCode") < o.index("?administrativeSex rdf:type")
    q = (d / "StepD.rq").read_text()
    assert optimize(q, ("reorder",))[0] == q


def test_shorthand_and_filters():
    q = PRAGMA + ("SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?d a sphn:BilledDiagnosis, sphn:Diagnosis ; "
                  f"sphn:hasCode ?c . FILTER(?c != ?d) ?c a icd-10-gm:E11 {INF} . }} }}")
    o = optimize(q, ("reorder",))[0]
    assert "?c a icd-10-gm:E11" in o.split("GRAPH ?g {")[1].split(".")[0]
    assert o.count("?d a sphn:") == 2 and "FILTER(?c != ?d)" in o
    assert algebra(o) == algebra(q)


def test_barriers():
    """Nothing moves across OPTIONAL, VALUES, BIND or a group: each run is ordered on its own."""
    q = PRAGMA + ("SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?d a sphn:BilledDiagnosis . "
                  "OPTIONAL { ?x a sphn:Age } VALUES ?k { 1 } BIND(1 AS ?b) "
                  f"?d sphn:hasCode ?c . ?c a icd-10-gm:E11 {INF} . }} }}")
    o = optimize(q, ("reorder",))[0]
    assert o.index("?d a sphn:BilledDiagnosis") < o.index("OPTIONAL") < o.index("VALUES") < o.index("BIND") < o.index("?c a icd-10-gm")
    assert o.index("?d sphn:hasCode") < o.index("?c a icd-10-gm")  # ?d is bound by the first run: follow it
    assert algebra(o) == algebra(q)


def test_paths():
    q = PRAGMA + "SELECT (AVG(?v) AS ?a) WHERE { GRAPH ?g { ?age a sphn:Age . ?age sphn:hasQuantity/sphn:hasValue ?v . } }"
    o, passes = optimize(q, ("paths",))
    assert passes == ["paths"] and "?age sphn:hasQuantity ?path1 ." in o and "?path1 sphn:hasValue ?v ." in o
    for skip in ("sphn:hasQuantity/sphn:hasValue*", "^sphn:hasQuantity/sphn:hasValue", "(sphn:hasQuantity|sphn:hasValue)"):
        assert optimize(q.replace("sphn:hasQuantity/sphn:hasValue", skip), ("paths",))[0] == q.replace("sphn:hasQuantity/sphn:hasValue", skip)
    star = q.replace("SELECT (AVG(?v) AS ?a)", "SELECT *")  # the fresh variable would be returned
    assert optimize(star, ("paths",))[0] == star


@pytest.mark.parametrize("q", ["not sparql {", "INSERT DATA { <a> <b> <c> }", "SELECT * WHERE { ?s ?p [ ?q ?o ] }",
                               "SELECT * WHERE { SERVICE <x> { ?s ?p ?o } }", "SELECT ?x WHERE { ?x a sphn:A \\. }"])
def test_unchanged_when_not_understood(q):
    assert optimize(q, PASSES) == (q, [])


def test_pipeline_judges_the_optimized_query(monkeypatch):
    from nl2q.pipeline import translate
    monkeypatch.setattr(S, "OPTIMIZE", True)
    body = f"SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ ?d a sphn:BilledDiagnosis . ?d sphn:hasCode ?c . ?c a icd-10-gm:E11 {INF} . }} }}"
    r = translate("q", llm_fn=lambda messages, usage: f"```sparql\n{body}\n```")
    assert r["optimized"] == ["reorder"] and r["is_compliant"] and r["sparql_query"].index("?c a icd-10-gm") < r["sparql_query"].index("?d a sphn")
    assert judge(r["sparql_query"]) is None


def test_zero_first(data):  # the 100k counts (NL2Q_OPT_STATS=data)
    """A pattern the data never matches (a 2026.1 property on the 2024.2 data, a terminology it lacks) goes first."""
    q = PRAGMA + ("SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?c a sphn:AdministrativeCase . "
                  "?c sphn:hasAdmission ?a . ?a sphn:hasDateTime ?t . } }")
    o = optimize(q, ("reorder",))[0]
    assert o.split("GRAPH ?g {")[1].strip().startswith("?c sphn:hasAdmission ?a .")


def test_dead_property_anchors_on_its_class(data):  # the 100k counts (NL2Q_OPT_STATS=data)
    """hasRouteCode exists, but no 2024.2 DrugPrescription has it: scan the class, each row dies at the property."""
    q = PRAGMA + ("SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?p sphn:hasAdministrativeCase ?c . "
                  "?c a sphn:AdministrativeCase . ?p a sphn:DrugPrescription ; sphn:hasRouteCode ?r . } }")
    body = [l.strip() for l in optimize(q, ("reorder",))[0].split("GRAPH ?g {")[1].split(" . ")]
    assert body[:2] == ["?p a sphn:DrugPrescription", "?p sphn:hasRouteCode ?r"]


def test_pragma_policy(data):  # the 100k counts (NL2Q_OPT_STATS=data)
    """OPTIONAL without a pattern the data never matches: the pragma goes (O06, O08: 60 s cap -> 1.7-3 s at 100k);
    with such a pattern (org-03, O08#4) or without OPTIONAL (N03, code first) it stays."""
    base = PRAGMA + "SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?d a sphn:BilledDiagnosis . %s } }"
    optional = "OPTIONAL { ?d sphn:hasAdministrativeCase ?c . }"
    o, passes = optimize(base % optional, PASSES)
    assert "sql:select-option" not in o and passes == ["pragma"]
    for keep in ("?d sphn:hasCode ?c .", optional + " ?d sphn:hasAdmission ?a ."):
        o = optimize(base % keep, PASSES)[0]
        assert 'DEFINE sql:select-option "order"' in o
