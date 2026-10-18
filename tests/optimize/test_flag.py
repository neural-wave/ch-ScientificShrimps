"""The flag pass (nl2q/optimize/flag.py): an OPTIONAL used only through BOUND(?v) becomes EXISTS, on the shapes of the
generated O02 family; anything else stays as written. The verdict guard over all queries is test_optimize.py. No database."""
import glob, json
import pytest
from nl2q.compliance import judge
from nl2q.optimize import optimize
from nl2q.optimize.flag import flag
from nl2q.translation.prompt import finish

INF = 'OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global")'
DX = f"?dx a sphn:BilledDiagnosis ; sphn:hasAdministrativeCase ?case ; sphn:hasCode ?c . ?c a icd-10-gm:A00 {INF} ."
EVENT = "?e a sphn:DrugAdministrationEvent ; sphn:hasAdministrativeCase ?case ; sphn:hasStartDateTime ?s ."
SELECT = "SELECT ?stay (COUNT(*) AS ?n) WHERE {"
OUT = f'{SELECT} GRAPH ?g {{ ?case a sphn:AdministrativeCase . {EVENT} OPTIONAL {{ {DX} }} }} ' \
      f'BIND (IF(BOUND(?dx), "cholera", "other") AS ?stay) }} GROUP BY ?stay'


@pytest.mark.parametrize("body", [
    OUT,  # BIND outside GRAPH (gen O02#2)
    OUT.replace("?case a sphn:AdministrativeCase . ", ""),  # without the case's type
    f'{SELECT} GRAPH ?g {{ {EVENT} OPTIONAL {{ {DX} }} BIND (IF(BOUND(?dx), "cholera", "other") AS ?stay) }} }} GROUP BY ?stay',
    f'SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH ?g {{ {EVENT} OPTIONAL {{ {DX} }} }} FILTER (!BOUND(?dx)) }}',
], ids=["bind-outside", "no-case-type", "bind-inside", "filter-not-bound"])
def test_flag_becomes_exists(body):
    q = finish(body)
    new = flag(q)
    assert new is not None and "OPTIONAL" not in new and "BOUND" not in new
    assert f"EXISTS {{ GRAPH ?g {{ {DX} }} }}" in new  # P as written, its GRAPH and OPTION kept
    assert judge(new) == judge(q)


@pytest.mark.parametrize("body", [
    OUT.replace("(COUNT(*) AS ?n)", "(COUNT(DISTINCT ?dx) AS ?n)"),  # the OPTIONAL's variable is counted
    OUT.replace("GROUP BY ?stay", "GROUP BY ?stay ?dx"),  # grouped by
    OUT.replace(f"OPTIONAL {{ {DX} }} }} BIND (IF(BOUND(?dx)", f'BIND (IF(BOUND(?dx), 1, 0) AS ?early) OPTIONAL {{ {DX} }} }} BIND (IF(BOUND(?dx)'),
    OUT.replace(f"OPTIONAL {{ {DX} }}", f"OPTIONAL {{ {DX} OPTIONAL {{ ?dx sphn:hasRecordDateTime ?t . }} }}"),  # nested
    OUT.replace(f"OPTIONAL {{ {DX} }}", f"OPTIONAL {{ {DX} FILTER (?s != ?dx) }}"),  # a FILTER on an outer variable
    OUT.replace(SELECT, "SELECT * WHERE {").replace("GROUP BY ?stay", ""),
    f'SELECT ?stay (COUNT(*) AS ?n) WHERE {{ {{ SELECT ?g ?case WHERE {{ GRAPH ?g {{ {EVENT} OPTIONAL {{ {DX} }} }} }} }} '
    f'BIND (IF(BOUND(?dx), "cholera", "other") AS ?stay) }} GROUP BY ?stay',  # BOUND in another scope
], ids=["counted", "grouped", "bound-before", "nested-optional", "outer-filter", "select-star", "subselect"])
def test_anything_else_stays(body):
    q = finish(body)
    assert flag(q) is None
    assert "flag" not in optimize(q, ("flag",))[1]


TRACED = sorted({r["sparql_query"] for f in glob.glob("/data/scratch/lorenzo.varese@supsi.ch/night/results/**/*trace*.jsonl",
                                                       recursive=True)
                 for r in map(json.loads, filter(str.strip, open(f)))
                 if r.get("id") == "O02" and r.get("is_compliant") and "OPTIONAL" in (r.get("sparql_query") or "")})


@pytest.mark.skipif(not TRACED, reason="the overnight traces are not on this machine")
@pytest.mark.parametrize("q", TRACED)
def test_traced_o02_variants(q):
    new, passes = optimize(q, ("flag",))
    assert passes == ["flag"] and "OPTIONAL" not in new and "EXISTS" in new and judge(new) == judge(q)
