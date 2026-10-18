"""NL2Q_CODE_CLASS: a code node the query groups by, returns or compares becomes its class; everything else stays as
written. Verdicts from the unchanged rules. No model, no database."""
import pytest
from nl2q import settings as S
from nl2q.compliance import judge
from nl2q.translation import codeclass
from nl2q.translation.codeclass import to_class
from nl2q.translation.prompt import finish

PIN = 'OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global")'
GROUPED = finish(f"""SELECT ?c (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{
  ?c rdf:type icd-10-gm:E11 {PIN} .
  ?dx sphn:hasCode ?c ; rdf:type sphn:BilledDiagnosis .
}} }} GROUP BY ?c""")


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setattr(S, "CODE_CLASS", True)


def test_grouped_code_node_becomes_its_class_after_the_binding_triple():
    out = to_class(GROUPED)
    assert judge(GROUPED) is not None and judge(out) is None
    assert "SELECT ?c_class" in out and "GROUP BY ?c_class" in out
    assert out.index("sphn:hasCode ?c") < out.index("?c a ?c_class") and f"icd-10-gm:E11 {PIN}" in out  # pin kept


def test_compared_nodes_compare_their_classes():
    q = finish("""SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g {
  ?b a sphn:Birth ; sphn:hasCountry ?bc . ?bc sphn:hasCode ?x .
  ?n a sphn:Nationality ; sphn:hasAssociatedCountry ?nc . ?nc sphn:hasCode ?y .
  FILTER (?x != ?y && STR(?x) != "")
} }""")
    out = to_class(q)
    assert "FILTER (?x_class != ?y_class && STR(?x_class)" in out and "?x a ?x_class ." in out and "?y a ?y_class ." in out
    assert judge(out) is None


def test_node_projected_by_a_subquery_and_grouped_outside():
    q = finish("""SELECT ?route (COUNT(?g) AS ?patients) WHERE {
  { SELECT DISTINCT ?g ?route WHERE { GRAPH ?g { ?e a sphn:DrugAdministrationEvent ; sphn:hasAdministrationRouteCode ?route } } }
} GROUP BY ?route""")
    out = to_class(q)
    assert "SELECT DISTINCT ?g ?route_class" in out and "GROUP BY ?route_class" in out and judge(out) is None


def test_existing_class_variable_is_reused():
    q = GROUPED.replace("?dx sphn:hasCode ?c ;", "?dx sphn:hasCode ?c . ?c a ?concept . ?dx")
    out = to_class(q)
    assert "GROUP BY ?concept" in out and "?c_class" not in out


@pytest.mark.parametrize("q", [
    # counting the nodes counts records
    finish("SELECT (COUNT(DISTINCT ?c) AS ?n) WHERE { GRAPH ?g { ?dx a sphn:BilledDiagnosis ; sphn:hasCode ?c } }"),
    # no aggregation: rows stay rows, the rules refuse them as written
    finish("SELECT ?c WHERE { GRAPH ?g { ?dx a sphn:BilledDiagnosis ; sphn:hasCode ?c } }"),
    # a node projected out of a subquery and joined again outside keeps its name (review case L16)
    finish('SELECT ?id (COUNT(DISTINCT ?g) AS ?n) WHERE { { SELECT ?g ?c WHERE { GRAPH ?g { ?dx a sphn:Diagnosis ; '
           'sphn:hasCode ?c . ?c sphn:hasCodingSystemAndVersion "ICD-10-GM 2024" } } } GRAPH ?g { ?c sphn:hasIdentifier ?id } } GROUP BY ?id'),
    "SELECT ?c WHERE { not sparql",
])
def test_left_as_written(q):
    assert to_class(q) == q


def test_request_that_names_nodes_or_identifiers_keeps_its_query():
    assert to_class(GROUPED, "Patient count per code node (a node of the patient data, not the code class)") == GROUPED
    assert to_class(GROUPED, "How many patients per type 2 diabetes code?") != GROUPED


def test_off_by_flag(monkeypatch):
    monkeypatch.setattr(S, "CODE_CLASS", False)
    assert to_class(GROUPED) == GROUPED


def test_a_bug_never_costs_the_query(monkeypatch):
    monkeypatch.setattr(codeclass, "_rewrite", lambda q: 1 / 0)
    assert to_class(GROUPED) == GROUPED
