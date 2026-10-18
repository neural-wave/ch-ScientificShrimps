"""Pipeline and API with stub models: the two layers together (token accounting, a non-compliant query is never
retried, a query that never parses is not compliant, the optional d3 pre-screen) and the API's response shape. The retry loop and the prompt:
translation/; the verdict: compliance/. No model, no database."""
import pytest
from fastapi.testclient import TestClient
from nl2q import settings as S
from nl2q.pipeline import translate

OK = "```sparql\nSELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?dx rdf:type sphn:BilledDiagnosis } }\n```"
ROWS = "```sparql\nSELECT ?dx WHERE { GRAPH ?g { ?dx rdf:type sphn:BilledDiagnosis } }\n```"
BAD = "```sparql\nSELECT (COUNT(* AS ?n) WHERE { ?s a ?t }\n```"
UNKNOWN = "```sparql\nSELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?dx rdf:type sphn:Nope } }\n```"


def stub(*replies):
    calls = []

    def fn(messages, usage):
        calls.append(messages)
        usage["prompt"] = usage.get("prompt", 0) + 100
        usage["completion"] = usage.get("completion", 0) + 10
        return replies[min(len(calls) - 1, len(replies) - 1)]
    fn.calls = calls
    return fn


def test_compliant_first_try_and_tokens():
    r = translate("q", llm_fn=stub(OK))
    assert r["is_compliant"] and r["tokens_consumed"] == 110 and r["compliance_reason"] is None
    assert r["sparql_query"].startswith('DEFINE sql:select-option "order"') and "PREFIX sphn:" in r["sparql_query"]


def test_non_compliant_is_never_retried():
    llm = stub(ROWS, OK)
    r = translate("list the diagnoses", llm_fn=llm)
    assert not r["is_compliant"] and len(llm.calls) == 1 and "aggregated" in r["compliance_reason"]


def test_never_valid_means_not_compliant():
    r = translate("q", llm_fn=stub(BAD))
    assert not r["is_compliant"] and r["compliance_reason"].startswith("Not a valid SPARQL query")
    assert len(r["attempts"]) == S.MAX_RETRIES + 1


def decider(**probs):
    calls = []

    def fn(state, questions, usage):
        calls.append(state)
        usage["decider"] = usage.get("decider", 0) + 50
        return {"policy": {"probabilities": probs}}
    fn.calls = calls
    return fn


def down(state, questions, usage):
    raise ConnectionError("d3 down")


BLOCK = dict(proceed=0.02, single_patient=0.98)


def test_prescreen_blocks_before_generation(monkeypatch):
    monkeypatch.setattr(S, "PRESCREEN", True)
    llm = stub(OK)
    r = translate("q", llm_fn=llm, decide_fn=decider(**BLOCK))
    assert not llm.calls and r["sparql_query"] == "" and r["attempts"] == [] and not r["is_compliant"]
    assert r["checker"] == "d3" and r["tokens_consumed"] == 50
    assert r["compliance_reason"].startswith("Queries about a single, identifiable patient are not allowed")
    assert r["compliance_reason"].endswith("(judged on the request before translation, 98%)")


def test_prescreen_proceed_goes_through_generation_and_rules(monkeypatch):
    monkeypatch.setattr(S, "PRESCREEN", True)
    d3 = decider(proceed=0.95, not_select=0.05)
    r = translate("q", llm_fn=stub(OK), decide_fn=d3)
    assert len(d3.calls) == 1 and r["is_compliant"] and r["checker"] == "rules" and r["tokens_consumed"] == 160
    r = translate("list the diagnoses", llm_fn=stub(ROWS), decide_fn=d3)  # a miss: the rules still judge the query
    assert not r["is_compliant"] and "aggregated" in r["compliance_reason"] and r["checker"] == "rules"


def test_prescreen_unreachable_falls_back_to_generation(monkeypatch):
    monkeypatch.setattr(S, "PRESCREEN", True)
    llm = stub(OK)
    r = translate("q", llm_fn=llm, decide_fn=down)
    assert len(llm.calls) == 1 and r["is_compliant"] and r["checker"] == "rules" and r["tokens_consumed"] == 110


def test_prescreen_off_never_calls_d3(monkeypatch):
    monkeypatch.setattr(S, "PRESCREEN", False)
    d3 = decider(**BLOCK)
    r = translate("q", llm_fn=stub(OK), decide_fn=d3)
    assert not d3.calls and r["is_compliant"] and r["checker"] == "rules" and r["tokens_consumed"] == 110


def test_kanon_off_by_default_leaves_the_query():
    r = translate("q", llm_fn=stub(OK))
    assert S.KANON == "off" and "HAVING" not in r["sparql_query"] and r["is_compliant"] and r["checker"] == "rules"


def test_kanon_rewrite_drops_small_groups_in_the_query(monkeypatch):
    monkeypatch.setattr(S, "KANON", "rewrite")
    r = translate("q", llm_fn=stub(OK))
    assert r["is_compliant"] and r["checker"] == "rules" and r["sparql_query"].startswith('DEFINE sql:select-option "order"')
    assert r["sparql_query"].rstrip().endswith('GROUP BY ("all") HAVING (COUNT(DISTINCT ?g) >= 10)')
    r = translate("list the diagnoses", llm_fn=stub(ROWS))  # a refused query is never rewritten
    assert not r["is_compliant"] and "HAVING" not in r["sparql_query"]


def test_kanon_flag_runs_the_query_and_keeps_the_rules_verdict_when_it_cannot(monkeypatch):
    from nl2q import store
    monkeypatch.setattr(S, "KANON", "flag")
    monkeypatch.setattr(store, "run", lambda query, timeout: (["kanon_patients", "n"], [("3", "3")]))
    r = translate("q", llm_fn=stub(OK))
    assert not r["is_compliant"] and r["checker"] == "kanon" and "HAVING" not in r["sparql_query"]
    assert r["compliance_reason"].startswith("Groups smaller than 10 patients may identify a patient")

    def virtuoso_down(query, timeout):
        raise ConnectionError("Virtuoso down")
    monkeypatch.setattr(store, "run", virtuoso_down)
    r = translate("q", llm_fn=stub(OK))
    assert r["is_compliant"] and r["checker"] == "rules"


@pytest.fixture
def client(monkeypatch):
    from nl2q import api
    monkeypatch.setattr(api, "translate", lambda text: translate(text, llm_fn=stub(ROWS)))
    monkeypatch.setattr(api, "warm_up", lambda: None)
    with TestClient(api.app) as c:
        yield c


def test_api_shape(client):
    r = client.post("/nl-to-sparql", json={"text": "list the diagnoses"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"sparql_query", "tokens_consumed", "is_compliant", "compliance_reason"} and body["is_compliant"] is False


def test_api_errors(client):
    assert client.post("/nl-to-sparql", json={"text": "  "}).json() == {"error": "'text' must not be empty."}
    r = client.post("/nl-to-sparql", json={})
    assert r.status_code == 400 and "error" in r.json()


def test_api_blocked_request_has_an_empty_query(monkeypatch):
    from nl2q import api
    monkeypatch.setattr(S, "PRESCREEN", True)
    monkeypatch.setattr(api, "translate", lambda text: translate(text, llm_fn=stub(OK), decide_fn=decider(**BLOCK)))
    monkeypatch.setattr(api, "warm_up", lambda: None)
    with TestClient(api.app) as c:
        r = c.post("/nl-to-sparql", json={"text": "Show everything about patient 7"})
    assert r.status_code == 200 and r.json()["sparql_query"] == "" and r.json()["is_compliant"] is False
    assert r.json()["compliance_reason"].startswith("Queries about a single, identifiable patient")
