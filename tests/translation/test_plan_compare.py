"""The compare condition of a plan (cohort {"compare": {"left": "a.attr", "op": "=" | "!=", "right": "b.attr"}}) and
its training data, without a database or a model."""
import random
import pytest
from nl2q.training.shapes import compare as T
from nl2q.tools.query_check import valid
from nl2q.translation import plan as P
from nl2q.translation.prompt import finish

PATIENTS = [{"name": "patients", "agg": "patients"}]


def plan(records, cohort, output=PATIENTS):
    return {"route": "plan", "records": records, "cohort": cohort, "output": output}


def cmp(left, op, right):
    return {"compare": {"left": left, "op": op, "right": right}}


BORN_ABROAD = plan({"birth": {"type": "Birth"}, "nat": {"type": "Nationality"}},
                   {"and": [{"has": "nat"}, cmp("birth.country", "!=", "nat.country")]})


def test_o04_plan_is_checked_and_compiles_to_the_flag_pattern():
    assert P.check(BORN_ABROAD) == []
    q = finish(P.compile(BORN_ABROAD))
    assert valid(q)[0], valid(q)
    assert q.startswith('DEFINE sql:select-option "order"')
    assert "?birth sphn:hasCountry/sphn:hasCode ?birth_country_code ." in q
    assert "?nat sphn:hasAssociatedCountry/sphn:hasCode ?nat_country_code ." in q
    assert "FILTER NOT EXISTS {" in q and "?nat_country_code rdf:type ?birth_country ." in q  # join on a's class
    assert "rdfs:subClassOf* ?birth_country_root" in q and "223369002" in q and "223860005" in q
    assert "GROUP BY ?pseudo_id" in q and "BOUND(?cmp_birth_country_ne_nat_country_alias)" in q
    assert "BIND (IF" not in q


def test_no_inference_and_no_generic_filter_inside_not_exists():
    q = P.compile(BORN_ABROAD)
    inside = q.split("FILTER NOT EXISTS {", 1)[1].split("}\n", 2)
    block = "}\n".join(inside[:2])
    assert "OPTION (inference" not in block and "STRSTARTS" not in block


def test_equal_compare_joins_in_place_and_drops_generic_classes():
    q = P.compile(plan({"birth": {"type": "Birth"}, "nat": {"type": "Nationality"}}, cmp("birth.country", "=", "nat.country")))
    assert "NOT EXISTS" not in q and "STRSTARTS(STR(?birth_country)" in q
    assert "?nat_country_code rdf:type ?birth_country ." in q


def test_codes_of_versioned_systems_compare_without_the_year():
    p = plan({"dx": {"type": "BilledDiagnosis", "code": ["icd-10-gm:E11"]}, "nursing": {"type": "NursingDiagnosis"}},
             cmp("dx.code", "=", "nursing.code"))
    assert P.check(p) == []
    q = P.compile(p)
    assert 'REPLACE(STR(?dx_code_class), "/(19|20)[0-9][0-9]/", "/") = REPLACE(STR(?nursing_code_class)' in q
    assert q.index("<https://biomedit.ch/rdf/sphn-resource/icd-10-gm/E11> OPTION (inference") < q.index("?nursing")
    assert valid(finish(q))[0]


def test_dates_compare_by_day_and_same_type_records_are_two_records():
    p = plan({"lab": {"type": "LabTestEvent", "code": ["loinc:718-7"]}, "lab2": {"type": "LabTestEvent"}},
             cmp("lab.date", "!=", "lab2.date"))
    assert P.check(p) == []
    q = P.compile(p)
    assert "SUBSTR(STR(?lab_date), 1, 10) = SUBSTR(STR(?lab2_date), 1, 10) && ?lab != ?lab2" in q
    assert valid(finish(q))[0]


def test_values_and_not_compare():
    p = plan({"weight": {"type": "BodyWeightMeasurement"}, "weight2": {"type": "BodyWeightMeasurement"}},
             {"not": cmp("weight.value", "=", "weight2.value")})
    assert P.check(p) == []
    q = P.compile(p)
    assert "?weight_value = ?weight2_value && ?weight != ?weight2" in q
    assert "everyone_alias" in q  # a patient without the records passes "not"
    assert valid(finish(q))[0]


@pytest.mark.parametrize("cohort,records,why", [
    (cmp("birth.country", "!=", "dx.code"), {"birth": {"type": "Birth"}, "dx": {"type": "BilledDiagnosis"}}, "share no code system"),
    (cmp("dx.date", "=", "lab.value"), {"dx": {"type": "BilledDiagnosis"}, "lab": {"type": "LabTestEvent"}}, "same kind"),
    (cmp("dx.code", "=", "dx.code"), {"dx": {"type": "BilledDiagnosis"}}, "two different records"),
    (cmp("dx.country", "=", "nat.country"), {"dx": {"type": "BilledDiagnosis"}, "nat": {"type": "Nationality"}}, "no country"),
    (cmp("dx.code", "=", "x.code"), {"dx": {"type": "BilledDiagnosis"}}, "names no record"),
    (cmp("dx.code", "=", "proc.code"), {"dx": {"type": "BilledDiagnosis"}, "proc": {"type": "BilledProcedure"}}, "share no code system"),
])
def test_check_rejects_wrong_compares(cohort, records, why):
    bad = P.check(plan(records, cohort))
    assert any(why in b for b in bad), bad


def test_compare_counts_patients_only():
    p = {**BORN_ABROAD, "output": [{"name": "n", "agg": "share", "if": {"has": "nat"}}]}
    assert any("counts patients" in b for b in P.check(p))


def test_generated_plans_pass_check_and_compile_and_are_never_o04():
    inv = {t: {"patients": 10, "codes": {}, "dates": None, "pairs": [], "data": {"code", "date", "value", "age"}}
           for t in P.RECORDS}
    pool = {"BilledDiagnosis": [("icd-10-gm:E11", 50), ("icd-10-gm:I10", 40)], "BilledProcedure": [("chop:39.61", 9)],
            "LabTestEvent": [("loinc:718-7", 30)], "NursingDiagnosis": [("icd-10-gm:E11", 5)]}
    items = T.sample(80, 3, inv, pool, {})
    assert len(items) >= 40 and {it["shape"] for it in items} == set(T.MAKERS)
    for it in items:
        assert T.problems(it["plan"]) == [], it
        assert P._canonical(it["plan"]) != P._canonical(T.O04)
        assert P._compares(it["plan"]["cohort"])
    assert T.problems(T.O04) == ["the right plan of the organizers' example 4"]


def test_rows_keep_the_template_when_no_rewrite_passes():
    item = {"id": "c00001", "plan": BORN_ABROAD, "template": "How many patients were born abroad?",
            "keep": {"exact": [], "numbers": [], "dates": [], "names": [], "cues": [], "forbid": []}}
    out = T.rows([item], {"c00001": [("terse", "{bad}")]})
    lines = out["train"] + out["val"]
    assert len(lines) == 1 and lines[0]["messages"][0]["content"] == P.TRAINED
    assert lines[0]["messages"][1]["content"] == item["template"]
