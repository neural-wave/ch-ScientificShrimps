"""The schema attributes of the plan (plan.MEASURES, the stay's insurance): checked and compiled, no database, no model."""
import pytest
from rdflib import URIRef
from nl2q import schema as SC
from nl2q.translation import plan as P
from nl2q.translation.prompt import finish

BIRTHS = {"route": "plan", "records": {"birth": {"type": "Birth", "where": [
              {"attr": "birth.gestational_age_unit", "op": "=", "value": "ucum:d"}]}},
          "group": [{"sex": True}],
          "output": [{"name": "births", "agg": "patients"},
                     {"name": "early", "agg": "patients_if", "if": {"attr": "birth.gestational_age", "op": "<", "value": 245}},
                     {"name": "avg_days", "agg": "avg", "of": "birth.gestational_age", "round": True}]}
ROWS = {"route": "plan", "records": {"dx": {"type": "BilledDiagnosis", "code": ["icd-10-gm:E11"]}},
        "output": [{"name": "n", "agg": "records", "of": "dx"}], "unsafe": {"rows": ["dx.date", "dx.age", "dx.insurance"]}}


def test_quantity_attribute_compiles_to_its_value_and_unit_without_bind():
    assert P.check(BIRTHS) == []
    q = finish(P.compile(BIRTHS))
    assert "?birth sphn:hasGestationalAge/sphn:hasQuantity ?birth_gestational_age_quantity ." in q
    assert "?birth_gestational_age_quantity sphn:hasValue ?birth_gestational_age ." in q
    assert "?birth_gestational_age_unit rdf:type <https://biomedit.ch/rdf/sphn-resource/ucum/d> ." in q
    assert "ROUND(AVG(?birth_gestational_age))" in q and "BIND" not in q
    assert q.count("sphn:hasGestationalAge") == 1  # the quantity is reached once for value and unit


def test_insurance_is_the_coverage_of_the_records_stay():
    assert P.check(ROWS) == []
    q = P.compile(ROWS)
    assert q.startswith("SELECT ?dx_date ?dx_age ?dx_insurance")
    assert ("?dx sphn:hasAdministrativeCase ?dx_case . ?dx_insurance_status sphn:hasAdministrativeCase ?dx_case . "
            "?dx_insurance_status rdf:type sphn:InsuranceStatus . ?dx_insurance_status sphn:hasCoverageType ?dx_insurance .") in q
    assert q.index("sphn:hasCode") < q.index("InsuranceStatus")  # the code triple stays first


def test_insurance_condition_and_grouping():
    plan = {"route": "plan", "records": {"stay": {"type": "AdministrativeCase"}}, "group": [{"code_class": "stay.insurance"}],
            "output": [{"name": "patients", "agg": "patients"}]}
    assert P.check(plan) == []
    q = P.compile(plan)
    assert "?stay_insurance_status sphn:hasAdministrativeCase ?stay ." in q
    assert 'GROUP BY (REPLACE(STR(?stay_insurance), "^.*[/#]", "") AS ?stay_insurance_id)' in q
    share = {"route": "plan", "records": {"dx": {"type": "BilledDiagnosis"}},
             "output": [{"name": "pct", "agg": "share", "if": {"attr": "dx.insurance", "op": "=", "value": "Private"}}]}
    assert P.check(share) == []
    assert "<https://biomedit.ch/rdf/sphn-schema/sphn/individual#Private>" in P.compile(share)


def test_date_attribute():
    plan = {"route": "plan", "records": {"onco": {"type": "OncologyDiagnosis", "where": [
        {"attr": "onco.incidence_date", "op": ">=", "value": "2020-01-01"}]}}, "cohort": {"has": "onco"},
        "output": [{"name": "patients", "agg": "patients"}]}
    assert P.check(plan) == []
    q = P.compile(plan)
    assert "?onco sphn:hasIncidenceDateTime ?onco_incidence_date ." in q
    assert 'FILTER (?onco_incidence_date >= "2020-01-01T00:00:00"^^xsd:dateTime)' in q


@pytest.mark.parametrize("record, test, why", [
    ({"type": "Birth"}, {"attr": "b.gestational_age", "op": "<", "value": "early"}, "compares with a number"),
    ({"type": "Birth"}, {"attr": "b.gestational_age_unit", "op": "=", "value": "d"}, "ucum"),
    ({"type": "OncologyDiagnosis"}, {"attr": "b.incidence_date", "op": ">", "value": "last year"}, "YYYY-MM-DD"),
    ({"type": "BilledDiagnosis"}, {"attr": "b.insurance", "op": "<", "value": "Private"}, '"="'),
    ({"type": "Birth"}, {"attr": "b.insurance", "op": "=", "value": "Private"}, "has no insurance"),
    ({"type": "BilledDiagnosis"}, {"attr": "b.gestational_age", "op": "<", "value": 259}, "has no gestational_age")])
def test_check_rejects_wrong_uses(record, test, why):
    plan = {"route": "plan", "records": {"b": {**record, "where": [test]}}, "cohort": {"has": "b"},
            "output": [{"name": "n", "agg": "patients"}]}
    assert any(why in p for p in P.check(plan)), P.check(plan)


def test_averages_need_a_number():
    plan = {"route": "plan", "records": {"dx": {"type": "BilledDiagnosis"}},
            "output": [{"name": "a", "agg": "avg", "of": "dx.insurance"}]}
    assert any("of a number" in p for p in P.check(plan))
    ok = {"route": "plan", "records": {"drug": {"type": "DrugAdministrationEvent"}},
          "output": [{"name": "a", "agg": "max", "of": "drug.duration"}]}
    assert P.check(ok) == [] and "MAX(?drug_duration)" in P.compile(ok)


def test_catalog_and_kinds():
    line = next(x for x in P.catalog().splitlines() if x.startswith("- Birth "))
    assert "gestational_age" in line and "gestational_age_unit" in line and "delivery_mode" in line
    assert "insurance" in next(x for x in P.catalog().splitlines() if x.startswith("- BilledDiagnosis "))
    assert P.attr_kind("Birth", "gestational_age") == "number" and P.attr_kind("BilledDiagnosis", "insurance") == "value"
    assert P.attr_kind("Birth", "delivery_mode") == "code" and P.attr_kind("Birth", "nothing") is None
    assert "insurance" not in P.extra("Birth")  # no stay link in the plan's Birth


def test_paths_are_2026_1_properties_of_their_record_type():
    from nl2q.tools.check_examples import SCHEMA_TTL, Schema
    if not SCHEMA_TTL.exists():
        pytest.skip("no 2026.1 schema file")
    s = Schema(SCHEMA_TTL)
    for t, attrs in P.MEASURES.items():
        for alias, (path, kind) in attrs.items():
            first = URIRef(SC.NS + path.split("/")[0].split(":", 1)[1])
            assert s.domain[first] and s.has(URIRef(SC.NS + t), first), (t, alias)
            if kind == "number":
                ranges = s.range[first]
                quantity = URIRef(SC.NS + "Quantity")
                assert quantity in ranges or any(s.has(r, URIRef(SC.NS + "hasQuantity")) for r in ranges), (t, alias)
    status = URIRef(SC.NS + "InsuranceStatus")
    assert s.has(status, URIRef(SC.NS + "hasCoverageType")) and s.has(status, URIRef(SC.NS + "hasAdministrativeCase"))
