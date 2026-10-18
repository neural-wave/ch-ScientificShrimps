"""validate()'s feedback with schema linking on (NL2Q_LINK=1, compliance/feedback.py): a property the class does not
have, SQL words, a code used as a value, an unbound variable; silent on every reference query (2026.1) and on a query the rules refuse; with
NL2Q_LINK off exactly the feedback of before. No model, no database."""
import json, re
from pathlib import Path
import pytest
from nl2q import schema as SC
from nl2q import settings as S
from nl2q.compliance import Invalid, analyse, validate
from nl2q.compliance.domains import load

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
P = ("PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>\nPREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
     "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\nPREFIX ucum: <https://biomedit.ch/rdf/sphn-resource/ucum/>\n"
     "PREFIX icd-10-gm: <https://biomedit.ch/rdf/sphn-resource/icd-10-gm/>\n")
DAE = "?e rdf:type sphn:DrugAdministrationEvent"
COUNT = "SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { %s } }"
FEEDBACK = {  # query, what the feedback must contain
    "route_code": (P + COUNT % f"{DAE} ; sphn:hasRouteCode ?c", "sphn:hasRouteCode is not a property of sphn:DrugAdministrationEvent: use one of sphn:hasAdministrationRouteCode,"),
    "route_code_subclass_path": (P + COUNT % "?e rdf:type/rdfs:subClassOf* sphn:DrugAdministrationEvent ; sphn:hasRouteCode/sphn:hasCodingSystemAndVersion ?v", "sphn:hasRouteCode is not"),
    "route_code_inverse": (P + COUNT % f"{DAE} . ?c ^sphn:hasRouteCode ?e", "sphn:hasRouteCode is not"),
    "route_code_in_exists": (P + COUNT % f"{DAE} FILTER EXISTS {{ ?e sphn:hasRouteCode ?c }}", "sphn:hasRouteCode is not"),
    "abstract_date": (P + COUNT % "?dx rdf:type sphn:OncologyDiagnosis ; sphn:hasDateTime ?t", "use one of sphn:hasIncidenceDateTime"),
    "case_when": (P + "SELECT (SUM(CASE WHEN ?v > 1 THEN 1 ELSE 0 END) AS ?n) WHERE { GRAPH ?g { ?a rdf:type sphn:Age ; sphn:hasQuantity/sphn:hasValue ?v } }",
                  "use IF(cond, a, b) instead of CASE WHEN"),
    "is_null": (P + COUNT % "?d rdf:type sphn:Death OPTIONAL { ?d sphn:hasDate ?t } FILTER(?t IS NULL)", "!BOUND(?x) instead of IS NULL"),
    "is_not_null": (P + COUNT % "?d rdf:type sphn:Death OPTIONAL { ?d sphn:hasDate ?t } FILTER(?t is not null)", "BOUND(?x) instead of IS NOT NULL"),
    "least": (P + "SELECT (MIN(LEAST(?a, ?b)) AS ?n) WHERE { GRAPH ?g { ?x rdf:type sphn:Age ; sphn:hasQuantity/sphn:hasValue ?a, ?b } }", "IF(?a < ?b, ?a, ?b) instead of LEAST"),
    "window_function": (P + "SELECT ?y (SUM(COUNT(?x)) OVER () AS ?t) WHERE { GRAPH ?g { ?x rdf:type sphn:Diagnosis ; sphn:hasRecordDateTime ?y } } GROUP BY ?y",
                        "no window functions in SPARQL: compute the total in a subquery and join it"),
    "code_constant_through_path": (P + COUNT % "?a rdf:type sphn:GestationalAgeAtBirth ; sphn:hasQuantity ?q . ?q sphn:hasUnit/sphn:hasCode ucum:wk",
                                   "codes are nodes: write ?q sphn:hasUnit/sphn:hasCode ?c . ?c a ucum:wk (not sphn:hasUnit/sphn:hasCode ucum:wk)"),
    "code_constant": (P + COUNT % "?dx rdf:type sphn:Diagnosis ; sphn:hasCode icd-10-gm:E11", "codes are nodes: write ?dx sphn:hasCode ?c . ?c a icd-10-gm:E11"),
    "code_constant_inverse": (P + COUNT % "?dx rdf:type sphn:Diagnosis . icd-10-gm:E11 ^sphn:hasCode ?dx", "codes are nodes"),
    "code_nodes_compared": (P + COUNT % "?d1 rdf:type sphn:Diagnosis ; sphn:hasCode ?a . ?d2 rdf:type sphn:Diagnosis ; sphn:hasCode ?b FILTER(?a != ?b)",
                            "?a and ?b are code nodes (one per record): compare their classes (?a rdf:type ?ca . ?b rdf:type ?cb, FILTER(?ca != ?cb))"),
    "code_node_vs_constant": (P + COUNT % "?dx rdf:type sphn:Diagnosis ; sphn:hasCode ?c FILTER(?c = icd-10-gm:E11)", "?c is a code node (one per record)"),
    "code_node_in": (P + COUNT % "?dx rdf:type sphn:Diagnosis ; sphn:hasCode ?c FILTER(?c IN (icd-10-gm:E11, icd-10-gm:E10))", "?c is a code node"),
    "code_node_same_term": (P + "SELECT (SUM(IF(sameTerm(?a, ?b), 1, 0)) AS ?n) WHERE { GRAPH ?g { ?d1 rdf:type sphn:Diagnosis ; sphn:hasCode ?a . "
                            "?d2 rdf:type sphn:Diagnosis ; sphn:hasCode ?b } }", "?a and ?b are code nodes"),
    "unbound_group_by": (P + "SELECT ?y (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?dx rdf:type sphn:Diagnosis ; sphn:hasRecordDateTime ?t } } GROUP BY ?y",
                         "?y is grouped and returned but never bound in WHERE"),
    "unbound_in_count": (P + "SELECT (COUNT(DISTINCT ?patient) AS ?n) WHERE { GRAPH ?g { ?dx rdf:type sphn:Diagnosis } }", "?patient is used in SELECT but never bound"),
    "unbound_in_group_key": (P + "SELECT ?y (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?dx rdf:type sphn:Diagnosis ; sphn:hasRecordDateTime ?t } } GROUP BY (YEAR(?d) AS ?y)",
                             "?d is used in GROUP BY but never bound"),
    "unbound_in_subquery": (P + "SELECT (COUNT(?p) AS ?n) WHERE { { SELECT DISTINCT ?p WHERE { GRAPH ?g { ?dx rdf:type sphn:Diagnosis } } } }", "?p is"),
}
SILENT = {  # valid 2026.1 queries: no feedback
    "route_code_right": P + COUNT % f"{DAE} ; sphn:hasAdministrationRouteCode ?c",
    "inherited": P + COUNT % "?dx rdf:type sphn:BilledDiagnosis ; sphn:hasRecordDateTime ?t ; sphn:hasCode ?c",
    "subclass_property_on_superclass": P + COUNT % "?dx rdf:type sphn:Diagnosis ; sphn:hasIncidenceDateTime ?t",
    "union_of_types": P + COUNT % "{ ?e rdf:type sphn:DrugAdministrationEvent ; sphn:hasDrug ?c } UNION { ?e rdf:type sphn:Diagnosis ; sphn:hasCode ?c }",
    "type_only_under_not_exists": P + COUNT % "?e sphn:hasRouteCode ?c FILTER NOT EXISTS { ?e rdf:type sphn:DrugAdministrationEvent }",
    "untyped": P + COUNT % "?e sphn:hasRouteCode ?c",
    "sql_words_in_strings_and_comments": P + COUNT % '?dx rdf:type sphn:Diagnosis ; sphn:hasCode/sphn:hasName ?l FILTER(CONTAINS(?l, "case when is null least("))' + " # GREATEST(",
    "variables_named_like_sql": P + "SELECT (COUNT(DISTINCT ?case) AS ?n) WHERE { GRAPH ?g { ?dx rdf:type sphn:Diagnosis ; sphn:hasAdministrativeCase ?case } }",
    "code_node_typed": P + COUNT % "?dx rdf:type sphn:Diagnosis ; sphn:hasCode ?c . ?c rdf:type icd-10-gm:E11",
    "code_classes_compared": P + COUNT % "?d1 rdf:type sphn:Diagnosis ; sphn:hasCode ?a . ?a rdf:type ?ca . ?d2 rdf:type sphn:Diagnosis ; sphn:hasCode ?b . ?b rdf:type ?cb FILTER(?ca != ?cb)",
    "code_class_vs_constant": P + COUNT % "?dx rdf:type sphn:Diagnosis ; sphn:hasCode/rdf:type ?k FILTER(?k IN (icd-10-gm:E11, icd-10-gm:E10))",
    "code_class_strstarts": P + COUNT % '?dx rdf:type sphn:Diagnosis ; sphn:hasCode ?c . ?c rdf:type ?k FILTER(STRSTARTS(STR(?k), "https://biomedit.ch/rdf/sphn-resource/icd-10-gm/"))',
    "bound_by_bind": P + "SELECT ?y (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?dx rdf:type sphn:Diagnosis ; sphn:hasRecordDateTime ?t } BIND(YEAR(?t) AS ?y) } GROUP BY ?y",
    "bound_by_group_as": P + "SELECT ?y (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?dx rdf:type sphn:Diagnosis ; sphn:hasRecordDateTime ?t } } GROUP BY (YEAR(?t) AS ?y)",
    "bound_by_values": P + "SELECT ?k (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?dx rdf:type sphn:Diagnosis } } GROUP BY ?k VALUES ?k { 1 }",
    "bound_by_subquery_as": P + "SELECT (COUNT(?p) AS ?n) WHERE { { SELECT DISTINCT (?g AS ?p) WHERE { GRAPH ?g { ?dx rdf:type sphn:Diagnosis } } } }",
    "aggregate_alias_reused": P + "SELECT (COUNT(DISTINCT ?g) AS ?n) (?n * 2 AS ?m) WHERE { GRAPH ?g { ?dx rdf:type sphn:Diagnosis } }",
    "virtuoso_extensions": 'DEFINE sql:select-option "order"\n' + P + COUNT % '?c rdf:type <https://biomedit.ch/rdf/sphn-resource/icd-10-gm/E11> OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global") . ?dx sphn:hasCode ?c ; rdf:type sphn:Diagnosis',
    # refused by the rules (R1, one fixed graph): "?g is never bound" would get GRAPH ?g back, a compliant query
    "non_compliant_query": P + "SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH <http://example.org/graph/p1> { ?dx rdf:type sphn:Diagnosis } }",
    "non_compliant_wrong_property": P + "SELECT ?e WHERE { GRAPH ?g { ?e rdf:type sphn:DrugAdministrationEvent ; sphn:hasRouteCode ?c } }",
}


@pytest.fixture
def link(monkeypatch):
    monkeypatch.setattr(S, "LINK", True)


@pytest.mark.parametrize("q, says", FEEDBACK.values(), ids=FEEDBACK.keys())
def test_feedback(link, q, says):
    out = validate(q)
    assert out and says in out and len(out) <= 300 and "complian" not in out.lower(), out


@pytest.mark.parametrize("q", SILENT.values(), ids=SILENT.keys())
def test_silent(link, q):
    assert validate(q) is None


def test_domains_from_the_schema():
    props, sub = load()
    assert "hasAdministrationRouteCode" in props["DrugAdministrationEvent"] and "hasRouteCode" not in props["DrugAdministrationEvent"]
    assert "hasRecordDateTime" in props["BilledDiagnosis"] and "BilledDiagnosis" in sub["Diagnosis"]


def _prefixed(body):
    return "".join(f"PREFIX {k}: <{v}>\n" for k, v in SC.PREFIXES.items()) + body


def _references():
    """Every reference query in 2026.1: the bench files, and each ```sparql block of the examples (not the mock ones)."""
    out = [(f"{f.stem}:{d['id']}", d["sparql"]) for f in sorted((ROOT / "tests" / "bench").glob("*.jsonl"))
           for d in map(json.loads, filter(str.strip, open(f))) if d.get("sparql")]
    for path in (ROOT / "nl2q" / "config" / "examples.md", ROOT / "nl2q" / "translation" / "resources" / "examples.md"):
        for block in re.split(r"(?m)^## ", path.read_text())[1:] if path.exists() else []:
            out += [(f"{path.parent.name}:{block.split('|')[0].strip()}", _prefixed(q)) for q in re.findall(r"```sparql\n(.*?)```", block, re.S)]
    return out


REFERENCES = _references()
SPHN_2024_2 = {"official:O05"}  # the organizers' query for the mock data: sphn:hasAdministrativeCase on a DrugPrescription


def _before(query):
    """validate() before NL2Q_LINK."""
    try:
        _, unknown = analyse(query)
    except Invalid as e:
        return str(e)[:300]
    return f"not in the SPHN schema: {', '.join(sorted(unknown))}" if unknown else None


@pytest.mark.parametrize("i, q", REFERENCES, ids=[i for i, _ in REFERENCES])
def test_references_get_no_new_feedback(link, i, q):
    if i in SPHN_2024_2:
        assert "is not a property of" in validate(q)
    else:
        assert validate(q) == _before(q)


@pytest.mark.parametrize("q", [q for q, _ in FEEDBACK.values()] + list(SILENT.values()) + [q for _, q in REFERENCES])
def test_link_off_is_as_before(monkeypatch, q):
    monkeypatch.setattr(S, "LINK", False)
    assert validate(q) == _before(q)
