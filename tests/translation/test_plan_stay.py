"""The same-stay shape of plan.py (cohort same_stay, group / "if" stay_has): check() and compile(), no database."""
from nl2q.translation import plan as P

DX = {"type": "BilledDiagnosis", "code": ["icd-10-gm:A00"]}
DRUG = {"type": "DrugAdministrationEvent"}
O02 = {"route": "plan", "records": {"drug": DRUG, "dx": DX}, "group": [{"stay_has": "dx"}],
       "output": [{"name": "administrations", "agg": "records", "of": "drug"},
                  {"name": "avg_hours", "agg": "avg", "of": "drug.hours", "round": True},
                  {"name": "short", "agg": "share", "if": {"attr": "drug.hours", "op": "<=", "value": 72}}]}


def count(cohort, recs=None):
    return {"route": "plan", "records": recs or {"dx": DX, "drug": DRUG}, "cohort": cohort,
            "output": [{"name": "patients", "agg": "patients"}]}


def test_stay_types_come_from_the_schema():
    assert {"BilledDiagnosis", "DrugAdministrationEvent", "LabTestEvent"} <= set(P.STAY_TYPES)
    assert not {"AdministrativeSex", "AdministrativeCase", "Allergy", "LabResult"} & set(P.STAY_TYPES)


def test_same_stay_count_compiles_with_the_coded_record_first():
    plan = count({"same_stay": ["drug", "dx"]})
    assert P.check(plan) == []
    q = P.compile(plan)
    assert q.index("icd-10-gm/A00") < q.index("sphn:DrugAdministrationEvent")
    assert "?dx sphn:hasAdministrativeCase ?drug_dx_case ." in q and "?drug sphn:hasAdministrativeCase ?drug_dx_case ." in q
    assert "FILTER (BOUND(?drug_dx_stay_alias))" in q
    assert q.count("OPTION (inference") == 1  # the second record is checked on its bound stay, without inference


def test_same_stay_combines_with_not_and_has():
    plan = count({"and": [{"has": "dx"}, {"not": {"same_stay": ["dx", "drug"]}}]})
    assert P.check(plan) == []
    q = P.compile(plan)
    assert "BOUND(?dx_alias) && !BOUND(?dx_drug_stay_alias)" in q
    assert "SubjectPseudoIdentifier" not in q  # has dx: no all-patients branch
    q = P.compile(count({"not": {"same_stay": ["dx", "drug"]}}))
    assert "?everyone_alias" in q


def test_same_stay_problems():
    assert any("no hospital stay" in p for p in P.check(count({"same_stay": ["dx", "sex"]},
                                                              {"dx": DX, "sex": {"type": "AdministrativeSex", "code": ["snomed:248152002"]}})))
    assert any("no record named" in p for p in P.check(count({"same_stay": ["dx", "lab"]})))
    assert any("two different" in p for p in P.check(count({"same_stay": ["dx", "dx"]})))
    rec = {**count({"same_stay": ["dx", "drug"]}), "output": [{"name": "n", "agg": "records", "of": "dx"}]}
    assert any("counts patients only" in p for p in P.check(rec))


def test_group_stay_has_compiles_without_bind_or_optional():
    assert P.check(O02) == []
    q = P.compile(O02)
    assert "BIND" not in q and "OPTIONAL" not in q
    assert "GROUP BY (?dx_stays > 0 AS ?stay_has_dx)" in q
    assert "(COUNT(?dx_stay) AS ?dx_stays)" in q and "UNION" in q
    assert "?drug sphn:hasAdministrativeCase ?drug_case ." in q
    assert "ROUND(AVG(bif:datediff(\"hour\", ?drug_start, ?drug_end)))" in q


def test_if_stay_has_share_and_patients():
    plan = {"route": "plan", "records": {"drug": DRUG, "dx": DX},
            "output": [{"name": "share", "agg": "share", "if": {"stay_has": "dx"}},
                       {"name": "pats", "agg": "patients_if", "if": {"stay_has": "dx"}}]}
    assert P.check(plan) == [] and P.units(plan) == ["drug"]
    q = P.compile(plan)
    assert "SUM(IF(?dx_stays > 0, 1, 0)) / COUNT(?drug)" in q
    assert "COUNT(DISTINCT IF(?dx_stays > 0, ?g, ?pats_none))" in q


def test_stay_has_problems():
    one = {"route": "plan", "records": {"drug": DRUG, "dx": DX}, "group": [{"stay_has": "drug"}],
           "output": [{"name": "n", "agg": "records", "of": "drug"}]}
    assert any("row by row" in p for p in P.check(one))
    sex = {"route": "plan", "records": {"drug": DRUG, "sex": {"type": "AdministrativeSex", "code": ["snomed:248152002"]}},
           "group": [{"stay_has": "sex"}], "output": [{"name": "n", "agg": "records", "of": "drug"}]}
    assert any("no hospital stay" in p for p in P.check(sex))
    bad_if = {**O02, "output": [{"name": "n", "agg": "patients", "if": {"stay_has": "dx"}}], "group": None}
    assert any("share or patients_if" in p for p in P.check({k: v for k, v in bad_if.items() if v is not None}))
    also = {**O02, "records": {**O02["records"], "lab": {"type": "LabTestEvent"}},
            "output": [{"name": "p", "agg": "patients_if", "if": {"has": "lab"}}]}
    assert any('"patients_if": {"has"' in p for p in P.check(also))


def test_training_never_writes_the_o02_plan():
    from nl2q.training.shapes import stay as T
    assert T.near_o02(O02)
    assert not T.near_o02({**O02, "records": {"drug": DRUG, "dx": {**DX, "code": ["icd-10-gm:E11"]}}})
