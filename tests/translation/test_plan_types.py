"""Record types a request must announce (plan.type_problems): a wrong one goes back to the plan writer with the reason."""
import pytest
from nl2q.translation import plan as P


def one(type_, code=None):
    return {"route": "plan", "records": {"r": {"type": type_, **({"code": [code]} if code else {})}},
            "output": [{"name": "n", "agg": "patients"}]}


@pytest.mark.parametrize("text,plan", [
    ("How many patients have cholera, and which ones are they?", one("OncologyDiagnosis", "icd-10-gm:A00")),
    ("How many patients had a procedure with CHOP code 00.0?", one("RadiotherapyProcedure")),
    ("How many patients have a diagnosis with ICD-10-GM code E11?", one("NursingDiagnosis", "icd-10-gm:E11"))])
def test_a_type_the_request_does_not_announce_is_a_problem(text, plan):
    assert P.type_problems(text, plan)


@pytest.mark.parametrize("text,plan", [
    ("How many patients have breast cancer?", one("OncologyDiagnosis")),
    ("Count patients with ICD-10-GM C18 or C50.9.", one("OncologyDiagnosis", "icd-10-gm:C50.9")),
    ("How many radiotherapy sessions started in 2020?", one("RadiotherapyProcedure")),
    ("How many patients have cholera?", one("BilledDiagnosis", "icd-10-gm:A00"))])
def test_announced_types_pass(text, plan):
    assert P.type_problems(text, plan) == []


def test_the_writer_gets_the_reason_and_one_retry():
    calls = []

    def llm_fn(msgs, usage, **kw):
        calls.append(msgs)
        return '{"route":"plan","records":{"dx":{"type":"%s","code":["icd-10-gm:A00"]}},"output":[{"name":"n","agg":"patients"}]}' % (
            "OncologyDiagnosis" if len(calls) == 1 else "BilledDiagnosis")
    plan, note = P.write("How many patients have cholera?", {}, llm_fn)
    assert note is None and plan["records"]["dx"]["type"] == "BilledDiagnosis"
    assert "does not ask for a OncologyDiagnosis" in calls[1][-1]["content"]
