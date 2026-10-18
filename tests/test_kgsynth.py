"""kgsynth, offline: every gold template gets its stratum's verdict from the rules, and the question filter catches
schema names and C questions that drop the identifiers."""
import collections
import pytest
from nl2q.compliance import judge
from nl2q.tools import kgsynth as K

G = {"ranges": collections.Counter()}
IND = [("AdverseEvent", "hasOutcome", "individual")]
NUM = [("BodyWeightMeasurement", "hasResult", "BodyWeight"), ("BodyWeight", "hasQuantity", "Quantity"),
       ("Quantity", "hasValue", "xsd:double")]
DATE = [("Death", "hasReportDateTime", "xsd:dateTime")]
CASES = [(IND, "count", {"value": "Fatal"}, None, "A"), (IND, "dist", {}, None, "A"), (NUM, "stat", {}, "AVG", "A"),
         (DATE, "since", {"since": 2015}, None, "A"), (IND, "list_patients", {"value": "Fatal"}, None, "B"),
         (NUM, "list_values", {}, None, "B"), (DATE, "list_records", {"since": 2015}, None, "B"),
         (IND, "count_ids", {"value": "Fatal"}, None, "C"), (DATE, "count_records", {"since": 2015}, None, "C"),
         (NUM, "per_patient", {}, None, "C")]


@pytest.mark.parametrize("path,op,cond,agg,stratum", CASES)
def test_gold_gets_the_stratum_verdict(path, op, cond, agg, stratum):
    assert (judge(K.gold(G, path, op, cond, agg)) is None) == (stratum != "B")


def test_leak():
    item = {"stratum": "C", "path": IND, "cond": {}}
    assert K.leak("How many patients have an AdverseEvent with a fatal outcome?", item)
    assert K.leak("How many patients have an adverse event, by sphn outcome?", item)
    assert K.leak("How many patients died after an adverse event?", item) == "no identifier"
    assert K.leak("How many distinct patient pseudo-identifiers had a fatal adverse event?", item) is None


def test_task_names_a_quantity_by_its_role():
    L = {"hasSystolicPressure": ("has systolic pressure", ""), "BloodPressureMeasurement": ("Blood Pressure Measurement", "")}
    path = [("BloodPressureMeasurement", "hasResult", "BloodPressure"), ("BloodPressure", "hasSystolicPressure", "Quantity"),
            ("Quantity", "hasValue", "xsd:double")]
    assert "maximum systolic pressure" in K.task(L, {"path": path, "cond": {}, "op": "stat", "agg": "MAX"})


def test_task_names_the_owner_of_a_date_on_a_part():
    L = {"Age": ("Age", ""), "hasDeterminationDateTime": ("has determination datetime", "")}
    path = [("OncologyDiagnosis", "hasSubjectAge", "Age"), ("Age", "hasDeterminationDateTime", "xsd:dateTime")]
    assert "age determination datetime on or after 1 January 1998" in K.task(
        L, {"path": path, "cond": {"since": 1998}, "op": "since", "agg": None})
