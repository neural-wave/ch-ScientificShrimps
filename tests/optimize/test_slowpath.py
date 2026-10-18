"""The slow path (nl2q/optimize/slowpath.py) can only swap a compliant query for a compliant one the cost model predicts
5x cheaper; anything else keeps the query it started from. A stub stands in for the generator. No database."""
import pytest
from nl2q import settings as S
from nl2q.compliance import judge
from nl2q.optimize.cost import cost
from nl2q.optimize.slowpath import slowpath
from nl2q.translation.prompt import finish

INF = 'OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global")'
SLOW = finish("SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?e a sphn:DrugAdministrationEvent . "
              "OPTIONAL { ?x a sphn:OncologyDiagnosis . } } }")
FAST = f"SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ ?c a icd-10-gm:E11 {INF} . ?d sphn:hasCode ?c . }} }}"
LISTING = f"SELECT ?g WHERE {{ GRAPH ?g {{ ?c a icd-10-gm:E11 {INF} . ?d sphn:hasCode ?c . }} }}"


def reply(body):
    calls = []

    def llm(messages, usage):
        calls.append(messages[-1]["content"])
        return f"```sparql\n{body}\n```"
    return llm, calls


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(S, "SLOWPATH", True)


def test_the_queries():
    assert judge(SLOW) is None and cost(SLOW) > S.SLOWPATH_T and cost(finish(FAST)) < S.SLOWPATH_T


def test_off_by_default():
    llm, calls = reply(FAST)
    assert slowpath("q", SLOW, None, [], llm) == (SLOW, []) and not calls


def test_cheaper_compliant_rewrite_is_kept(on):
    llm, calls = reply(FAST)
    q, passes = slowpath("q", SLOW, None, [], llm)
    assert passes[-1] == "slowpath" and "icd-10-gm:E11" in q and judge(q) is None
    assert "OPTIONAL over sphn:OncologyDiagnosis" in calls[0]  # the feedback names the costly pattern


@pytest.mark.parametrize("body", [LISTING, "not a query {", ""])
def test_anything_else_keeps_the_query(on, body):
    llm, _ = reply(body)
    assert slowpath("q", SLOW, None, [], llm) == (SLOW, ["slowpath-kept"])


def test_never_for_a_non_compliant_or_cheap_query(on):
    llm, calls = reply(FAST)
    assert slowpath("q", SLOW, "R2 not aggregated", [], llm)[0] == SLOW
    assert slowpath("q", finish(FAST), None, [], llm)[0] == finish(FAST)
    assert not calls
