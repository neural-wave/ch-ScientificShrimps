"""The organizers'-style training plans (nl2q/training/shapes/orgstyle.py): valid, compiled, following the conventions,
and never an organizers' right plan. No database, no model: codes and plain names come from the inventory and the
caches."""
import json, re
import pytest
from nl2q.training.shapes import orgstyle as T
from nl2q.translation import plan as P


@pytest.fixture(scope="module")
def items():
    ctx, _ = T.context()
    return T.sample(400, 11, ctx)


def test_plans_pass_check_and_compile(items):
    assert len(items) == 400
    for it in items:
        assert P.check(it["plan"]) == [], it["plan"]
        P.compile(it["plan"])


def test_conventions(items):
    for it in items:
        plan, out = it["plan"], it["plan"]["output"]
        assert T.conventions(plan) == [], plan
        for m in out:
            if m["agg"] in ("avg", "min", "max"):
                assert not m["of"].endswith(".days"), "a record's duration is in hours"
                assert m.get("round") is True
            if m["agg"] == "gap":
                assert m["in"] == "days" and m.get("round") is True
        if plan.get("group"):
            counts = [m["name"] for m in out if m["agg"] in ("patients", "records")]
            assert plan["order"] == [{"by": counts[0], "desc": True}], "a breakdown is sorted most-first"
            assert 2 <= len(out) <= 5
        text = it["template"]
        if any(str(m.get("of", "")).endswith(".hours") for m in out) and not any(
                str((m.get("if") or {}).get("attr", "")).endswith(".hours") for m in out):
            assert not re.search(r"\bhours?\b", text), text  # the convention says the unit
        if any(m["agg"] == "gap" for m in out):
            assert not re.search(r"\bdays?\b|\bhours?\b", text), text
        if any(m["agg"] == "share" for m in out):
            assert not re.search(r"percent|%", text), text


def test_breakdowns_have_three_to_five_outputs_and_a_shape_mix(items):
    shapes = {it["shape"] for it in items}
    assert {"breakdown", "stay_split", "pathway", "compare"} <= shapes
    for it in items:
        if it["shape"] == "breakdown":
            assert 3 <= len(it["plan"]["output"]) <= 5


def test_no_organizers_plan(items):
    orgs = T.org_plans()
    canon = {P._canonical(p) for p in orgs}
    for it in items:
        assert P._canonical(it["plan"]) not in canon
        assert T.leaks(it["plan"], orgs) is None


def test_leaks_catches_the_organizers_shapes():
    orgs = T.org_plans()
    for org in orgs:
        assert T.leaks(org, orgs)
    o02 = {"route": "plan", "records": {"drug": {"type": "DrugAdministrationEvent"},
                                        "dx": {"type": "BilledDiagnosis", "code": ["icd-10-gm:A00"]}},
           "group": [{"stay_has": "dx"}], "output": [{"name": "patients", "agg": "patients"},
                                                     {"name": "avg_hours", "agg": "avg", "of": "drug.hours", "round": True}],
           "order": [{"by": "patients", "desc": True}]}
    o04 = {"route": "plan", "records": {"birth": {"type": "Birth"}, "nat": {"type": "Nationality"}},
           "cohort": {"and": [{"has": "nat"}, {"compare": {"left": "birth.country", "op": "!=", "right": "nat.country"}}]},
           "output": [{"name": "patients", "agg": "patients"}]}
    assert T.leaks(o02, orgs) and T.leaks(o04, orgs)
    org06 = next(p for p in orgs if (p.get("records") or {}).get("proc", {}).get("type") == "BilledProcedure")
    near = json.loads(json.dumps(org06))  # another code family, the same outputs: still the organizers' breakdown
    near["records"]["proc"]["code"] = ["chop:39.9"]
    near["group"][0]["match"] = r"^39\.9[0-9]$"
    assert T.leaks(near, orgs)


def test_no_official_question():
    official = T.held()
    assert official
    assert all(T.near(q, official) for q in official)


def test_plain_templates_name_no_code():
    ctx, _ = T.context()
    plain = T.sample(200, 5, T.Ctx(ctx.pool, ctx.names, ctx.dvals, plain=True), "orgp")
    for it in plain:
        assert P.check(it["plan"]) == []
        P.compile(it["plan"])
        assert not re.search(r"\b(?:ICD-10-GM|SNOMED CT|CHOP|LOINC|GENO|SO) code", it["template"]), it["template"]
        assert not it["keep"]["exact"]
