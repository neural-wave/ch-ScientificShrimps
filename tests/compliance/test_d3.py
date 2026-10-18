"""The d3 pre-screen on the request (NL2Q_PRESCREEN=1) without a model: the policy file and the decision logic, with a
stub in place of the decision model. The pipeline around it: tests/test_pipeline.py."""
import json, logging
from pathlib import Path
import pytest
from nl2q import settings as S
from nl2q.compliance import d3, judge
from nl2q.compliance.d3 import POLICY, as_question, screen

HERE = Path(__file__).parent


def stub(**probs):
    return lambda state, questions, usage: {"policy": {"type": "choice", "choice": max(probs, key=probs.get), "probabilities": probs}}


def test_policy_shape():
    assert "proceed" in POLICY["criteria"] and set(POLICY["examples"]) == set(POLICY["criteria"])
    assert set(POLICY["criteria"]) - {"proceed"} == {"single_patient", "not_aggregated", "unrestricted", "not_select",
                                                     "exposes_identifiers"}
    assert all(len(v) == 5 for v in POLICY["examples"].values()), "exactly 5 labelled examples per rule"


def test_examples_are_not_bench_questions():
    asked = {json.loads(l)["question"] for f in (HERE.parent / "bench").glob("*.jsonl") for l in open(f) if l.strip()}
    assert not asked & {ex for v in POLICY["examples"].values() for ex in v}


def test_question_carries_every_rule_and_example():
    q = as_question()
    assert q["type"] == "choice" and set(q["criteria"]) == set(POLICY["criteria"])
    assert all(ex in q["criteria"][r] for r, v in POLICY["examples"].items() for ex in v)


@pytest.mark.parametrize("probs, rule, blocked", [
    ({"proceed": 0.9, "single_patient": 0.1}, "proceed", False),
    ({"proceed": 0.03, "exposes_identifiers": 0.97}, "exposes_identifiers", True),
    ({"proceed": 0.1, "unrestricted": 0.9}, "unrestricted", True),  # at the threshold
    ({"proceed": 0.04, "single_patient": 0.50, "exposes_identifiers": 0.46}, "single_patient", True),  # split mass
    ({"proceed": 0.25, "not_select": 0.40, "single_patient": 0.35}, "proceed", False),  # below 0.9: the rules decide
])
def test_decision(probs, rule, blocked):
    d = screen("q", decide_fn=stub(**probs))
    assert (d["rule"], d["blocked"]) == (rule, blocked) and d["description"] == POLICY["criteria"][rule]


def test_log_only_rule_is_logged_not_blocked(caplog, monkeypatch):
    with caplog.at_level(logging.INFO, logger="nl2q.compliance.d3"):
        d = screen("q", decide_fn=stub(proceed=0.02, not_aggregated=0.98))
    assert (d["rule"], d["blocked"]) == ("proceed", False) and "log only" in caplog.text and "not_aggregated" in caplog.text
    monkeypatch.setattr(S, "PRESCREEN_LOG_ONLY", set())
    assert screen("q", decide_fn=stub(proceed=0.02, not_aggregated=0.98))["blocked"]


def test_state_is_the_request_alone_and_tokens_count():
    seen, usage = [], {"prompt": 100}

    def decide(state, questions, usage):
        seen.append(state)
        usage["decider"] = 40
        return {"policy": {"probabilities": {"proceed": 1.0}}}
    assert not screen("How many patients?", decide_fn=decide, usage=usage)["blocked"]
    assert seen == ["Request: How many patients?"] and usage == {"prompt": 100, "decider": 40}


def test_unreachable_returns_none():
    def down(state, questions, usage):
        raise ConnectionError("model down")
    assert screen("q", decide_fn=down) is None


def test_slow_d3_times_out_and_proceeds(monkeypatch):
    seen = {}

    def slow(req, timeout):
        seen["timeout"] = timeout
        raise TimeoutError("timed out")
    monkeypatch.setattr(d3, "urlopen", slow)
    assert screen("q") is None and seen["timeout"] == S.PRESCREEN_TIMEOUT <= 2


OK = ("PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>\nPREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
      "SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?dx rdf:type sphn:BilledDiagnosis } }")


def test_judge_is_the_rules_alone():
    assert judge(OK) is None
    assert judge("  ").startswith("Not a valid SPARQL query")
