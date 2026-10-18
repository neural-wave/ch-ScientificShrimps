"""The judged system is static (organizer, 17 Oct): with the default settings it never contacts a database and never reads
the counts of the 100k mock patients. The optimizer's default knowledge is the schema and SHACL (shacl.py); the data's
counts (NL2Q_OPT_STATS=data), the slow path, the card cues and k-anonymity are optional extras. No database."""
import pytest
import requests
from nl2q import settings as S
from nl2q.optimize import PASSES, optimize, stats
from nl2q.translation.prompt import finish

INF = 'OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global")'
N03 = finish("SELECT ?sex (AVG(?v) AS ?avg) WHERE { GRAPH ?g {\n"
             "  ?dx rdf:type sphn:BilledDiagnosis ; sphn:hasCode ?dx_code ; sphn:hasSubjectAge ?age .\n"
             f"  ?dx_code rdf:type icd-10-gm:E11 {INF} .\n"
             "  ?age sphn:hasQuantity ?q . ?q sphn:hasValue ?v .\n"
             "  ?s rdf:type sphn:AdministrativeSex ; sphn:hasCode ?sex .\n} } GROUP BY ?sex")


@pytest.fixture
def no_data(monkeypatch):
    """Any read of the 100k counts or any call to a SPARQL endpoint fails the test."""
    def fail(*a, **k):
        raise AssertionError("the static system touched the data or a database")
    stats.data.cache_clear()
    monkeypatch.setattr(stats, "data", fail)
    monkeypatch.setattr(requests, "post", fail)
    monkeypatch.setattr(requests, "get", fail)
    yield
    monkeypatch.undo()
    stats.data.cache_clear()


def test_schema_is_the_default():
    assert S.OPT_SOURCE == "schema" and not S.SLOWPATH and not S.CARD_STATS and S.KANON == "off"


def test_static_reorder_puts_the_code_first(no_data):
    o, passes = optimize(N03, PASSES)
    body = [l.strip() for l in o.split("GRAPH ?g {")[1].split("\n") if l.strip()]
    assert "reorder" in passes and body[0] == f"?dx_code rdf:type icd-10-gm:E11 {INF} ."
    assert body[1] == "?dx sphn:hasCode ?dx_code ." and body[2] == "?dx rdf:type sphn:BilledDiagnosis ."


def test_the_default_pipeline_is_static(no_data, monkeypatch):
    """With the optimizer on and every other setting at its default: no database, no counts."""
    from nl2q.pipeline import translate
    monkeypatch.setattr(S, "OPTIMIZE", True)
    body = f"SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ ?d a sphn:BilledDiagnosis . ?d sphn:hasCode ?c . " \
           f"?c a icd-10-gm:E11 {INF} . }} }}"
    r = translate("q", llm_fn=lambda messages, usage: f"```sparql\n{body}\n```")
    assert r["is_compliant"] and "reorder" in r["optimized"] and r["checker"] == "rules"
