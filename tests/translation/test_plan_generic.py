"""Generic record types and details (resources/schema_records.json, python -m nl2q.tools.schema_records): a plan names
any SPHN 2026.1 record type with data and any of its schema properties; curated entries win and compile as before.
The code-IRI switch (NL2Q_CODE_IRI). No database, no model."""
import pytest
from nl2q import settings as S
from nl2q.translation import plan as P
from nl2q.tools import schema_records as B


def count(rec, out=None, **more):
    return {"route": "plan", "records": {"r": rec}, **({"cohort": {"has": "r"}} if out is None else {}),
            "output": out or [{"name": "patients", "agg": "patients"}], **more}


def cond(attr, value, op="="):
    return {"attr": attr, "op": op, "value": value}


# ---- the builder -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("prop, alias", [("hasSystolicPressure", "systolic_pressure"),
                                         ("hasReactionTypeCode", "reaction_type_code"),
                                         ("hasTumorPurity", "tumor_purity"), ("hasDateTime", "date_time")])
def test_an_alias_is_the_property_in_snake_case_without_has(prop, alias):
    assert B.alias(prop) == alias


@pytest.mark.parametrize("rng, kind", [("-> sphn:Quantity", "number"), ("xsd:dateTime", "date"),
                                       ("-> code in {snomed:123}; recorded: snomed:1", "code"), ("-> code", "code"),
                                       ("one of sphn-individual:DuBois, sphn-individual:Other", "value"),
                                       ("-> sphn:Drug", "link:Drug"), ("-> sphn:Drug | sphn:Substance", None),
                                       ("xsd:string", None), ("xsd:double", "literal")])
def test_a_range_gives_the_kind_of_detail(rng, kind):
    assert B.kind(rng) == kind


def test_ranges_follow_the_blocks_indentation():
    block = {"sphn:Pressure": "sphn:Pressure (P): x\n    sphn:hasResult -> sphn:BloodPressure\n"
                              "        sphn:hasSystolicPressure -> sphn:Quantity\n            sphn:hasValue xsd:double\n"
                              "    sphn:hasStartDateTime xsd:dateTime"}
    r = B.ranges(block)
    assert r[("Pressure", "hasResult")] == "-> sphn:BloodPressure"
    assert r[("BloodPressure", "hasSystolicPressure")] == "-> sphn:Quantity"
    assert r[("Quantity", "hasValue")] == "xsd:double"
    assert r[("Pressure", "hasStartDateTime")] == "xsd:dateTime"


@pytest.mark.skipif(not (B.INVENTORY.exists() and B.TERMS.exists()), reason="needs the data inventory (nl2q_data)")
def test_the_resource_is_what_the_builder_writes():
    out, _ = B.build()
    assert B.dump(out) == (P.HERE / "resources" / "schema_records.json").read_text()


# ---- the table in the plan format ------------------------------------------------------------------------------------

def test_curated_types_come_first_unchanged_then_the_generic_ones_alphabetically():
    names = list(P.RECORDS)
    assert names[:len(P.CURATED)] == list(P.CURATED)
    generic = names[len(P.CURATED):]
    assert generic == sorted(generic) and len(generic) > 50
    assert P.SCHEMA["properties"]["records"]["additionalProperties"]["properties"]["type"]["enum"] == names
    assert {"DrugPrescription", "BloodPressure", "TumorSpecimen", "BodySurfaceArea",
            "AntimicrobialSusceptibilityLabTestEvent"} <= set(generic)


def test_generic_details_never_shadow_a_fixed_or_curated_alias():
    for t, details in P.GENERIC.items():
        for a, (path, kind, *_) in details.items():
            assert a not in P.ALIASES and a != "insurance" and a not in P.CODED.get(t, {}), (t, a)
            assert kind in ("number", "date", "code", "value"), (t, a)
            assert path.startswith("sphn:") and "/" not in path


def test_the_catalog_lists_the_generic_types_and_their_details():
    lines = P.catalog().split("\n")
    assert lines[:len(P.CURATED)][0].startswith("- BilledDiagnosis (icd-10-gm;")
    bp = next(line for line in lines if line.startswith("- BloodPressure ("))
    assert "systolic_pressure" in bp and "systolic_pressure_unit" in bp
    assert any(line.startswith("- DrugPrescription (snomed; route") for line in lines)


# ---- generic types and details compile ------------------------------------------------------------------------------

def test_a_drug_prescription_is_reached_through_its_active_ingredient():
    plan = count({"type": "DrugPrescription", "code": ["snomed:63856005"]})
    assert P.check(plan) == [] and P.type_problems("patients prescribed a drug with 63856005", plan) == []
    q = P.compile(plan)
    assert "?r_code rdf:type <http://snomed.info/id/63856005> OPTION" in q
    assert "?r_step1 sphn:hasActiveIngredient ?r_code" not in q  # the chain is written from its end
    assert "?r_step1 sphn:hasCode ?r_code ." in q and "?r sphn:hasDrug ?r_step0 ." in q
    assert "?r rdf:type sphn:DrugPrescription ." in q


def test_a_systolic_pressure_is_a_quantity_with_its_unit():
    plan = count({"type": "BloodPressure", "where": [cond("r.systolic_pressure", 140, ">"),
                                                     cond("r.systolic_pressure_unit", "ucum:mmsblHgsbr")]})
    assert P.check(plan) == []
    q = P.compile(plan)
    assert "?r sphn:hasSystolicPressure ?r_systolic_pressure_quantity ." in q
    assert "?r_systolic_pressure_quantity sphn:hasValue ?r_systolic_pressure ." in q
    assert "FILTER (?r_systolic_pressure > 140)" in q
    assert "?r_systolic_pressure_unit rdf:type <https://biomedit.ch/rdf/sphn-resource/ucum/mmsblHgsbr> ." in q


def test_an_average_of_a_generic_number_and_a_count_of_generic_records():
    avg = count({"type": "BodySurfaceArea", "where": [cond("r.unit", "ucum:m2")]},
                [{"name": "average", "agg": "avg", "of": "r.value"}])
    assert P.check(avg) == [] and "(AVG(?r_value) AS ?average)" in P.compile(avg)
    purity = count({"type": "TumorSpecimen", "where": [cond("r.tumor_purity", 50, ">")]},
                   [{"name": "specimens", "agg": "records", "of": "r"}])
    assert P.check(purity) == [] and "(COUNT(DISTINCT ?r) AS ?specimens)" in P.compile(purity)
    assert P.check(count({"type": "TumorSpecimen", "where": [cond("r.tumor_purity", "high", ">")]})) != []


def test_a_generic_type_with_sub_classes_counts_them_on_a_coded_record_too():
    q = P.compile(count({"type": "Assay", "code": ["obi:0000070"]}))
    assert f'?r rdf:type sphn:Assay OPTION (inference "{S.INFERENCE}") .' in q


def test_generic_values_and_codes_are_checked_against_the_schema():
    calc = lambda v: count({"type": "BodySurfaceArea", "where": [cond("r.calculation_method", v)]})
    assert P.check(calc("DuBois")) == [] and P.check(calc("sphn-individual:Mosteller")) == []
    assert any("one of DuBois, Mosteller, Other" in b for b in P.check(calc("Haycock")))
    status = lambda v: count({"type": "Consent", "where": [cond("r.status_code", v)]})
    assert P.check(status("snomed:385645004")) == []
    assert any("snomed code" in b for b in P.check(status("loinc:1234-5")))


def test_a_generic_type_must_be_named_by_the_request():
    plan = count({"type": "DrugPrescription"})
    assert P.type_problems("How many patients have a drug prescription?", plan) == []
    assert P.type_problems("How many patients were prescribed something?", plan) == []
    assert P.type_problems("How many patients had a billed diagnosis?", plan) != []
    assert P.type_problems("patients with an antimicrobial susceptibility test",
                           count({"type": "AntimicrobialSusceptibilityLabTestEvent"})) == []


def test_a_generic_type_with_a_stay_link_is_a_stay_type():
    assert "TumorSpecimen" in P.STAY_TYPES and "DrugPrescription" not in P.STAY_TYPES
    plan = {"route": "plan", "records": {"a": {"type": "TumorSpecimen"}, "b": {"type": "BilledDiagnosis", "code": ["icd-10-gm:C50"]}},
            "cohort": {"same_stay": ["a", "b"]}, "output": [{"name": "patients", "agg": "patients"}]}
    assert P.check(plan) == []
    assert "sphn:hasAdministrativeCase" in P.compile(plan)


# ---- NL2Q_CODE_IRI: a breakdown by code returns the class IRI ---------------------------------------------------------

ROUTES = {"route": "plan", "records": {"rx": {"type": "DrugPrescription"}}, "group": [{"code_class": "rx.route"}],
          "output": [{"name": "prescriptions", "agg": "records", "of": "rx"}],
          "order": [{"by": "prescriptions", "desc": True}], "limit": 5}


def test_the_switch_is_off_by_default_and_keeps_the_short_name(monkeypatch):
    monkeypatch.setattr(S, "CODE_IRI", False)
    q = P.compile(ROUTES)
    assert 'SELECT (REPLACE(STR(?rx_route), "^.*[/#]", "") AS ?rx_route_id)' in q


def test_with_the_switch_a_code_breakdown_returns_the_class_iri(monkeypatch):
    monkeypatch.setattr(S, "CODE_IRI", True)
    q = P.compile(ROUTES)
    assert q.startswith("SELECT ?rx_route (COUNT(DISTINCT ?rx) AS ?prescriptions)") and "REPLACE" not in q
    assert "GROUP BY ?rx_route\nORDER BY DESC(?prescriptions)\nLIMIT 5" in q
    loinc = {"route": "plan", "records": {"lab": {"type": "LabTestEvent"}}, "group": [{"code_class": "lab"}],
             "output": [{"name": "patients", "agg": "patients"}]}
    assert P.compile(loinc).startswith("SELECT ?lab_class (COUNT(DISTINCT ?g) AS ?patients)")


def test_with_the_switch_icd10_and_chop_keep_the_short_name(monkeypatch):
    monkeypatch.setattr(S, "CODE_IRI", True)
    for t in ("BilledDiagnosis", "BilledProcedure"):
        plan = {"route": "plan", "records": {"r": {"type": t}}, "group": [{"code_class": "r"}],
                "output": [{"name": "patients", "agg": "patients"}]}
        assert 'REPLACE(STR(?r_class), "^.*[/#]", "")' in P.compile(plan), t
    severity = {"route": "plan", "records": {"a": {"type": "AdverseEvent"}}, "group": [{"code_class": "a.outcome"}],
                "output": [{"name": "patients", "agg": "patients"}]}
    assert P.compile(severity).startswith("SELECT ?a_outcome ")  # a value-set individual: its IRI
