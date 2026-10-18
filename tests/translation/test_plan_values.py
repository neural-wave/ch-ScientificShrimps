"""Coded-detail values the SPHN 2026.1 schema does not allow (plan._value_problems, resources/coded_values.json): a wrong
value goes back to the plan writer with the allowed ones. No database, no model."""
import json
import pytest
from nl2q.translation import plan as P


def events(*tests, where=()):
    return {"route": "plan", "records": {"event": {"type": "AdverseEvent", **({"where": list(where)} if where else {})}},
            "group": [{"code_class": "event.consequences"}],
            "output": [{"name": "patients", "agg": "patients"}] +
                      [{"name": f"n{i}", "agg": "patients_if", "if": t} for i, t in enumerate(tests)]}


def cond(attr, value, op="="):
    return {"attr": attr, "op": op, "value": value}


def stored(id_):
    return next(json.loads(line)["plan"] for line in (P.HERE / "resources" / "plans.jsonl").read_text().splitlines()
                if json.loads(line)["id"] == id_)


def test_o09_values_the_schema_does_not_have_are_rejected_with_the_allowed_ones():
    plan = events(cond("event.consequences", "Death"), cond("event.consequences", "FullRecovery"),
                  where=[cond("event.outcome", "Serious")])
    bad = P.check(plan)
    assert len(bad) == 2
    serious, full = sorted(bad, key=lambda b: "Serious" not in b)
    assert "'Serious'" in serious and "Fatal" in serious and "Recovered" in serious
    assert "'FullRecovery'" in full and "Death" in full and "LifeThreatening" in full


def test_o09_right_plan_passes():
    assert P.check(stored("org-09")) == []


@pytest.mark.parametrize("id_", ["org-06", "org-07", "org-08", "org-09"])
def test_stored_plans_on_coded_details_pass(id_):
    assert P._value_problems(stored(id_)) == []


def test_a_value_of_another_detail_says_where_it_belongs():
    bad = P.check(events(cond("event.consequences", "Fatal")))
    assert bad and "outcome" in bad[0]


@pytest.mark.parametrize("value", ["Fatal", "sphn-individual:Fatal"])
def test_a_value_set_individual_with_or_without_prefix_passes(value):
    assert P.check(events(cond("event.outcome", value, op="!="))) == []


def test_a_code_for_a_value_set_detail_is_rejected():
    assert P.check(events(cond("event.outcome", "snomed:399166001")))


def variants(attr, value):
    return {"route": "plan", "records": {"vd": {"type": "VariantDescriptor"}}, "group": [{"code_class": "vd.variation"}],
            "output": [{"name": "patients", "agg": "patients"}, {"name": "n", "agg": "patients_if", "if": cond(attr, value)}]}


@pytest.mark.parametrize("attr,value", [("vd.zygosity", "geno:0000136"), ("vd.zygosity", "geno:0000135"),
                                        ("vd.type", "so:0001019"), ("vd.variation", "sphn:GenomicDeletion")])
def test_allowed_codes_and_classes_pass(attr, value):
    assert P.check(variants(attr, value)) == []


@pytest.mark.parametrize("attr,value,shown", [("vd.zygosity", "homozygous", "geno"), ("vd.zygosity", "snomed:123456", "geno"),
                                              ("vd.type", "geno:0000345", "so"),
                                              ("vd.variation", "GenomicDeletion", "sphn:GenomicDeletion"),
                                              ("vd.variation", "sphn:GenomicVariation", "sphn:GenomicDeletion")])
def test_a_code_of_another_system_or_a_word_is_rejected_with_what_is_allowed(attr, value, shown):
    bad = P.check(variants(attr, value))
    assert len(bad) == 1 and shown in bad[0]


def allergies(value):
    return {"route": "plan", "records": {"a": {"type": "Allergy", "where": [cond("a.severity", value)]}},
            "cohort": {"has": "a"}, "output": [{"name": "patients", "agg": "patients"}]}


def test_a_closed_value_set_takes_only_its_codes():
    allowed = P.CODED_VALUES["Allergy"]["severity"]["codes"]
    assert allowed and P.check(allergies(allowed[0])) == []
    bad = P.check(allergies("snomed:24484000"))
    assert len(bad) == 1 and all(c in bad[0] for c in allowed)


@pytest.mark.parametrize("value,ok", [("main", True), (P.MAIN, True), ("snomed:24484000", False)])
def test_rank_takes_main_or_a_rank_code(value, ok):
    plan = {"route": "plan", "records": {"dx": {"type": "BilledDiagnosis", "code": ["icd-10-gm:E11"],
                                                "where": [cond("dx.rank", value)]}},
            "cohort": {"has": "dx"}, "output": [{"name": "patients", "agg": "patients"}]}
    assert (P.check(plan) == []) is ok


def test_every_detail_of_coded_json_has_its_allowed_values():
    for t in P.CURATED:  # generic details carry their own (schema_records.json, tests/translation/test_plan_generic.py)
        for alias in P.coded(t):
            if alias not in P.GENERIC.get(t, {}):
                assert alias in P.CODED_VALUES.get(t, {}), (t, alias)


@pytest.mark.skipif(not __import__("nl2q.tools.check_examples", fromlist=["SCHEMA_TTL"]).SCHEMA_TTL.exists(),
                    reason="needs the 2026.1 schema file")
def test_the_resource_is_what_the_schema_says():
    from nl2q.tools import coded_values
    assert coded_values.build() == P.CODED_VALUES


@pytest.mark.skipif(not (P.S.DATA / "inventory" / "inventory.json").exists(), reason="needs the data inventory")
def test_every_look_alike_pair_has_two_different_valid_plans():
    from nl2q.training.shapes import pairs as T
    pairs, _ = T.sample(2 * len(T.KINDS), seed=3)
    assert {p[0]["pair"] for p in pairs} == set(T.KINDS)
    banned = T.official()
    for a, b in pairs:
        assert T.canonical(a["plan"]) != T.canonical(b["plan"])
        for it in (a, b):
            assert P.check(it["plan"]) == [] and P.compile(it["plan"])
            assert T.canonical(it["plan"]) not in banned
