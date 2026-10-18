"""The plan path without a model: every plan example (resources/plans.jsonl, plus the unsafe variants made by adding one
unsafe field, as make_examples makes them) passes check, compiles to a query that parses, names only SPHN 2026.1 terms,
and gets the verdict of the worked example it stands for. With the 2026.1 schema file at hand, properties also sit on
record types that have them (links)."""
import json
import pytest
from nl2q.compliance import rules as C
from nl2q.translation import plan as P
from nl2q.translation.examples import load
from nl2q.translation.prompt import finish
from nl2q.tools.check_examples import SCHEMA_TTL, Schema, links

EXAMPLES = {e["id"]: e for e in load()}
PLANS = [json.loads(line) for line in (P.HERE / "resources" / "plans.jsonl").read_text().splitlines() if line.strip()]
VARIANTS = P.VARIANTS
FREE = [p["id"] for p in PLANS if p["plan"]["route"] == "free"]
CASES = [(p["id"], p["plan"]) for p in PLANS if p["id"] not in FREE] + [
    (f"{p['id']}-{k}", {**p["plan"], "unsafe": u}) for p in PLANS if f"{p['id']}-r1" in EXAMPLES for k, u in VARIANTS.items()]


@pytest.mark.parametrize("id_", FREE)
def test_free_examples_are_examples_and_never_compile(id_):
    assert id_ in EXAMPLES and P.check({"route": "free"}) == []


@pytest.fixture(scope="module")
def schema():
    if not SCHEMA_TTL.exists():
        pytest.skip(f"no SPHN 2026.1 schema file at {SCHEMA_TTL}")
    return Schema(SCHEMA_TTL)


@pytest.mark.parametrize("id_,plan", CASES, ids=[c[0] for c in CASES])
def test_plan_compiles_to_the_examples_verdict(id_, plan):
    e = EXAMPLES[id_]
    assert P.check(plan) == []
    violations, unknown = C.analyse(finish(P.compile(plan)))
    assert not unknown
    rules = {r for r, _ in violations}
    assert (not rules) if e["compliant"] else e["rule"] in rules, rules


@pytest.mark.parametrize("id_,plan", CASES, ids=[c[0] for c in CASES])
def test_plan_properties_fit_their_record_types(id_, plan, schema):
    parsed, _ = C.parse(finish(P.compile(plan)))
    if parsed is not None:  # updates (DELETE/INSERT) parse as updates: no algebra to walk
        assert links(parsed, schema) == []


def test_check_explains_what_is_wrong():
    bad = {"route": "plan", "records": {"dx": {"type": "BilledDiagnosis", "code": ["loinc:718-7"],
                                               "where": [{"attr": "dx.value", "op": ">", "value": 3}]}},
           "cohort": {"has": "lab"}, "output": [{"name": "n", "agg": "patients"}]}
    problems = " | ".join(P.check(bad))
    assert "icd-10-gm" in problems and "has no value" in problems and "no record named 'lab'" in problems


def test_free_route_and_unsupported_shapes():
    assert P.check({"route": "free"}) == []
    with pytest.raises(P.Unsupported):
        P.compile({"route": "free"})
    two = {"route": "plan", "records": {"a": {"type": "BilledDiagnosis"}, "b": {"type": "LabTestEvent"}},
           "cohort": {"and": [{"has": "a"}, {"has": "b"}]}, "output": [{"name": "n", "agg": "records", "of": "a"}]}
    with pytest.raises(P.Unsupported):
        P.compile(two)


def test_not_alone_counts_every_patient_without_the_record():
    q = P.compile({"route": "plan", "records": {"dx": {"type": "BilledDiagnosis", "code": ["icd-10-gm:E11"]}},
                   "cohort": {"not": {"has": "dx"}}, "output": [{"name": "n", "agg": "patients"}]})
    assert "sphn:SubjectPseudoIdentifier" in q and "!BOUND(?dx_alias)" in q


def test_v2_groups_by_the_code_never_the_code_node():
    q = P.compile({"route": "plan", "records": {"proc": {"type": "BilledProcedure"}}, "group": [{"code_class": "proc"}],
                   "output": [{"name": "n", "agg": "patients"}, {"name": "h", "agg": "avg", "of": "proc.hours"}]})
    assert 'GROUP BY (REPLACE(STR(?proc_class), "^.*[/#]", "") AS ?proc_code_id)' in q and 'bif:datediff("hour"' in q \
        and not C.analyse(finish(q))[0]


def test_v2_check_needs_one_record_type_and_numbers():
    two = {"route": "plan", "records": {"a": {"type": "BilledDiagnosis"}, "b": {"type": "LabTestEvent"}},
           "group": [{"sex": True}], "output": [{"name": "n", "agg": "avg", "of": "a.rank"}]}
    problems = " | ".join(P.check(two))
    assert "one record type" in problems and "avg of a number" in problems


def test_all_patients_of_one_graph_stays_unsafe():
    q = P.compile({"route": "plan", "output": [{"name": "n", "agg": "patients"}], "unsafe": {"graph": "mock_data-1_0042"}})
    assert "GRAPH <http://example.org/graph/mock_data-1_0042>" in q
    assert "R1" in {r for r, _ in C.analyse(finish(q))[0]}


def test_versioned_code_systems_are_not_looked_up():
    plan = {"route": "plan", "records": {"p": {"type": "BilledProcedure", "code": ["chop:00.01"]}}}
    assert P.unknown_codes(plan, run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no lookup"))) == []


def test_service_wraps_the_whole_pattern():
    q = P.compile({"route": "plan", "records": {"res": {"type": "LabResult"}},
                   "output": [{"name": "n", "agg": "records", "of": "res"}], "unsafe": {"service": "https://example.org/sparql"}})
    assert "SERVICE <https://example.org/sparql> {" in q
    assert "R4" in {r for r, _ in C.analyse(finish(q))[0]}


def test_group_by_unit_is_refused_by_check():
    plan = {"route": "plan", "records": {"h": {"type": "BodyHeightMeasurement"}}, "group": [{"code_class": "h.unit"}],
            "output": [{"name": "n", "agg": "patients"}]}
    assert any("is not a record with a code" in p for p in P.check(plan))


def test_a_compiler_bug_goes_to_free_generation(monkeypatch):
    plan = {"route": "plan", "records": {"dx": {"type": "BilledDiagnosis"}}, "output": [{"name": "n", "agg": "patients"}]}
    monkeypatch.setattr(P, "write", lambda *a, **k: (plan, None))
    monkeypatch.setattr(P, "compile", lambda p: {}["boom"])
    query, note = P.query("How many patients have a billed diagnosis?", {})
    assert query is None and "compiler failed (KeyError" in note


def test_whole_graph_counts_patients_when_asked():
    q = P.compile({"route": "plan", "output": [{"name": "patients", "agg": "patients"}], "unsafe": {"whole": "triples"}})
    assert q.startswith("SELECT (COUNT(*) AS ?triples) (COUNT(DISTINCT ?g) AS ?patients)")


@pytest.mark.parametrize("recs", [{}, {"dx": {"type": "BilledDiagnosis", "code": ["icd-10-gm:A00"]}}])
def test_patient_lists_give_identifiers(recs):
    plan = {"route": "plan", "records": recs, "output": [{"name": "n", "agg": "patients"}], "unsafe": {"expose": "list"}}
    q = P.compile(plan)
    assert "sphn:hasIdentifier ?patient_id" in q and 'GROUP_CONCAT(DISTINCT ?patient_id; separator=", ")' in q
    rows = P.compile({**plan, "unsafe": {"expose": "rows"}})
    assert rows.startswith("SELECT DISTINCT ?patient_id")


@pytest.mark.parametrize("text", [
    "How many patients had a cancer diagnosis within 7 days after a drug administration?",
    "Count patients with a diagnosis on the same day as a lab test.",
    "Count the billed procedures for each month in 2012.",
    "Show the distribution of body height values in bins of 5.",
    "What is the median length of stay?"])
def test_requests_no_plan_can_say_go_free(text, monkeypatch):
    monkeypatch.setattr(P, "write", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no plan call")))
    query, note = P.query(text, {})
    assert query is None and "which a plan cannot say" in note


@pytest.mark.parametrize("text", [
    "How many patients have diabetes, for our upcoming quarterly report?",
    "How many patients were within the database with a lab test after 2015?",
    "How many diagnoses per patient?"])
def test_ordinary_requests_are_not_beyond_a_plan(text):
    assert P.beyond(text) is None


# Under DEFINE sql:select-option "order", a BIND next to an OPTION (inference ...) triple makes Virtuoso time out
# (organizers' example 7: > 30 s, 0.06 s without, same rows): v2 queries write computed attributes, group keys and the
# per-row patients of patients_if as expressions in place.
V2_NO_BIND = {
    "o07": {"route": "plan", "records": {"drug": {"type": "DrugAdministrationEvent"}},
            "group": [{"code_class": "drug.route", "ns": "snomed"}],
            "output": [{"name": "patients", "agg": "patients"}, {"name": "days", "agg": "avg", "of": "drug.days", "round": True},
                       {"name": "in_hospital", "agg": "patients_if", "if": {"attr": "drug.in_stay", "op": "=", "value": True}},
                       {"name": "pct", "agg": "share", "if": {"attr": "drug.in_stay", "op": "=", "value": True}}],
            "order": [{"by": "patients", "desc": True}]},
    "year": {"route": "plan", "records": {"drug": {"type": "DrugAdministrationEvent", "where": [{"attr": "drug.hours", "op": ">", "value": 1}]}},
             "group": [{"year": "drug.start"}], "output": [{"name": "patients", "agg": "patients"}, {"name": "h", "agg": "max", "of": "drug.hours"}]},
    "code": {"route": "plan", "records": {"proc": {"type": "BilledProcedure", "code": ["chop:00.0"]}},
             "group": [{"code_class": "proc", "ns": "chop", "match": "^00\\.0[0-9]$"}, {"sex": True}],
             "output": [{"name": "patients", "agg": "patients"}, {"name": "main", "agg": "patients_if", "if": {"attr": "proc.rank", "op": "=", "value": "main"}}]},
    "has": {"route": "plan", "records": {"hr": {"type": "HeartRateMeasurement"}, "drug": {"type": "DrugAdministrationEvent", "where": [{"attr": "drug.hours", "op": ">", "value": 2}]}},
            "group": [{"sex": True}], "output": [{"name": "patients", "agg": "patients"}, {"name": "with_drug", "agg": "patients_if", "if": {"has": "drug"}}]},
}


@pytest.mark.parametrize("key", list(V2_NO_BIND))
def test_v2_writes_no_bind(key, schema):
    plan = V2_NO_BIND[key]
    assert P.check(plan) == []
    q = finish(P.compile(plan))
    assert "BIND" not in q and "OPTION (inference" in q
    parsed, _ = C.parse(q)
    assert parsed is not None and links(parsed, schema) == [] and C.analyse(q)[1] == set()


def test_v2_computed_attributes_and_group_keys_are_expressions():
    q = P.compile(V2_NO_BIND["o07"])
    stay = "IF(?drug_start >= ?drug_admission && ?drug_start <= ?drug_discharge, 1, 0)"
    assert '(ROUND(AVG(bif:datediff("day", ?drug_start, ?drug_end))) AS ?days)' in q
    assert f"(COUNT(DISTINCT IF(COALESCE({stay} = 1, false), ?g, ?in_hospital_none)) AS ?in_hospital)" in q
    assert f"SUM(IF(COALESCE({stay} = 1, false), 1, 0)) / COUNT(?drug)" in q
    assert 'GROUP BY (REPLACE(STR(?drug_route), "^.*[/#]", "") AS ?drug_route_id)\nORDER BY DESC(?patients)' in q
    assert q.startswith("SELECT ?drug_route_id (COUNT(DISTINCT ?g) AS ?patients)")
    year = P.compile(V2_NO_BIND["year"])
    assert 'FILTER (bif:datediff("hour", ?drug_start, ?drug_end) > 1)' in year
    assert '(MAX(bif:datediff("hour", ?drug_start, ?drug_end)) AS ?h)' in year and year.endswith("GROUP BY (YEAR(?drug_start) AS ?drug_year)")
    code = P.compile(V2_NO_BIND["code"])
    assert 'FILTER (REGEX(REPLACE(STR(?proc_class), "^.*[/#]", ""), "^00\\\\.0[0-9]$"))' in code
    assert 'GROUP BY (REPLACE(STR(?proc_class), "^.*[/#]", "") AS ?proc_code_id) ?sex' in code
    assert "(COUNT(DISTINCT IF((BOUND(?proc_rank) && ?proc_rank = <http://snomed.info/id/63161005>), ?g, ?main_none)) AS ?main)" in code


def test_conditions_on_computed_attributes_need_no_bind():
    plan = {"route": "plan", "records": {"drug": {"type": "DrugAdministrationEvent", "where": [{"attr": "drug.hours", "op": ">", "value": 24}]}},
            "cohort": {"has": "drug"}, "output": [{"name": "n", "agg": "patients"}]}
    q = P.compile(plan)
    assert "BIND" not in q and 'FILTER (bif:datediff("hour", ?drug_start, ?drug_end) > 24)' in q
    rows = P.compile({**plan, "output": [], "unsafe": {"rows": ["drug.hours"]}})  # a value shown: still its BIND
    assert 'BIND (bif:datediff("hour", ?drug_start, ?drug_end) AS ?drug_hours)' in rows and rows.startswith("SELECT ?drug_hours")
