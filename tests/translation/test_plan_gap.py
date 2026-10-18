"""The gap shape (organizers' example 1): time from a record to the next ones, and which of them follow it. No database,
no model."""
import json
import random
import pytest
from nl2q.translation import plan as P
from nl2q.training.shapes import gap as G

O01 = {"route": "plan",
       "records": {"dx": {"type": "OncologyDiagnosis"}, "surg": {"type": "OncologySurgery"},
                   "radio": {"type": "RadiotherapyProcedure"}},
       "group": [{"after": "dx", "next": ["surg", "radio"]}],
       "output": [{"name": "patients", "agg": "patients"},
                  {"name": "avg_days_to_first_treatment", "agg": "gap", "stat": "avg", "from": "dx.incidence_date",
                   "to": ["surg.date", "radio.date"], "in": "days", "round": True}],
       "order": [{"by": "patients", "desc": True}]}


def gap(**kw):
    return {"name": "wait", "agg": "gap", "stat": "avg", "from": "dx.date", "to": ["proc.date"], "in": "days", **kw}


def two(output, **kw):
    return {"route": "plan", "records": {"dx": {"type": "BilledDiagnosis", "code": ["icd-10-gm:E11"]},
                                         "proc": {"type": "BilledProcedure"}}, "output": output, **kw}


def test_o01_plan_is_org_01_and_compiles_to_the_fast_pattern():
    stored = [json.loads(line)["plan"] for line in (P.HERE / "resources" / "plans.jsonl").read_text().splitlines()
              if json.loads(line)["id"] == "org-01"]
    assert stored == [O01]
    assert P.check(O01) == []
    q = P.compile(O01)
    assert "BIND" not in q and "OPTIONAL" not in q
    assert "?dx sphn:hasIncidenceDateTime ?dx_incidence_date ." in q
    assert "FILTER (?surg_date > ?dx_from)" in q and "FILTER (?radio_date > ?dx_from)" in q
    assert 'IF(BOUND(?surg_next), IF(BOUND(?radio_next), "surg+radio", "surg"), IF(BOUND(?radio_next), "radio", "none")) AS ?next' in q
    assert q.rstrip().endswith("GROUP BY ?next\nORDER BY DESC(?patients)")
    assert 'ROUND(AVG(1.0 * IF(BOUND(?avg_days_to_first_treatment_to) && BOUND(?dx_first), bif:datediff("day", COALESCE(?dx_first, ?avg_days_to_first_treatment_to, ' in q
    # the code / type triple first in every record pattern, with the inference option
    assert q.count('OPTION (inference "') == 5
    assert q.count("UNION") == 2


def test_gap_only_one_row_min_hours():
    p = two([{"name": "patients", "agg": "patients"}, gap(stat="min", **{"in": "hours"})])
    assert P.check(p) == []
    q = P.compile(p)
    assert "?next" not in q and "GROUP BY ?next" not in q
    assert 'COALESCE(?wait_to, ?dx_first, "1900-01-01T00:00:00Z"^^xsd:dateTime)), ?wait_none))' in q
    assert "?dx_code rdf:type <" in q.split("GRAPH ?g {", 1)[1].split("\n")[1]  # code triple first


def test_three_next_records_label_every_combination():
    p = {"route": "plan", "records": {"lab": {"type": "LabTestEvent"}, "drug": {"type": "DrugAdministrationEvent"},
                                      "proc": {"type": "MedicalProcedure"}, "death": {"type": "Death"}},
         "group": [{"after": "lab", "next": ["drug", "proc", "death"]}], "output": [{"name": "patients", "agg": "patients"}]}
    assert P.check(p) == []
    q = P.compile(p)
    for label in ("drug+proc+death", "drug+proc", "drug+death", "proc+death", "drug", "proc", "death", "none"):
        assert f'"{label}"' in q
    assert "?lab sphn:hasReportDateTime ?lab_date ." in q  # a lab test event's date


def test_cohort_flags_join_the_per_patient_rows():
    p = two([gap()], cohort={"and": [{"has": "dx"}, {"not": {"has": "lab"}}]})
    p["records"]["lab"] = {"type": "LabTestEvent", "code": ["loinc:718-7"]}
    assert P.check(p) == []
    q = P.compile(p)
    assert "FILTER (BOUND(?dx_first) && (BOUND(?dx_alias) && !BOUND(?lab_alias)))" in q
    assert "(MAX(?lab_n_) AS ?lab_alias)" in q


@pytest.mark.parametrize("plan, words", [
    (two([gap(**{"from": "dx.value"})]), "not a date"),
    (two([gap(to=["dx.date"])]), "from one record to other records"),
    (two([gap(stat="median")]), '"stat"'),
    (two([gap(), {"name": "n", "agg": "records", "of": "dx"}]), '"patients" and gaps only'),
    (two([gap()], group=[{"after": "proc", "next": ["dx"]}]), 'record named in "after"'),
    (two([gap()], group=[{"after": "dx", "next": ["dx"]}]), "other records, each once"),
    (two([gap()], group=[{"after": "dx", "next": ["proc"]}, {"sex": True}]), "only group key"),
    (two([gap()], order=[{"by": "nobody"}]), "no output named"),
    (two([gap(to=["nothing.date"])]), "no record named"),
])
def test_gap_problems(plan, words):
    assert any(words in p for p in P.check(plan)), P.check(plan)


def test_unsafe_parts_go_to_free_generation():
    with pytest.raises(P.Unsupported):
        P.compile(two([gap()], unsafe={"per_patient": True}))
    q = P.compile(two([gap()], unsafe={"graph": "gen_mock_data-1_1000001"}))
    assert "GRAPH ?g" not in q


def test_plain_cohort_never_loops():
    assert P._plain_cohort({"and": [{"has": "a"}, {"before": {"first": "a.date", "then": "b.date"}}]})
    assert not P._plain_cohort({"same_stay": ["a", "b"]})
    assert not P._plain_cohort({"not": {"compare": {"left": "a.date", "op": "=", "right": "b.date"}}})


def test_training_plans_are_valid_and_never_o01():
    pool = {"BilledDiagnosis": [("icd-10-gm:E11", 5)], "LabTestEvent": [("loinc:718-7", 3)],
            "DrugAdministrationEvent": [("snomed:387207008", 2)]}
    items = G.sample(80, 3, pool)
    assert len(items) == 80
    for it in items:
        assert P.check(it["plan"]) == [] and P.compile(it["plan"])
        assert P._canonical(it["plan"]) != P._canonical(O01)
        assert not {"OncologyDiagnosis", "OncologySurgery", "RadiotherapyProcedure"} <= \
            {r["type"] for r in it["plan"]["records"].values()}
        assert it["template"] and it["keep"]["cues"]
    rows = G.rows(items[:2], {items[0]["id"]: ["How long until the first drug?"]})
    assert rows == [{"messages": [{"role": "system", "content": P.TRAINED},
                                  {"role": "user", "content": "How long until the first drug?"},
                                  {"role": "assistant", "content": json.dumps(items[0]["plan"], separators=(",", ":"))}]}]
