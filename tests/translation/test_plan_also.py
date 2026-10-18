"""The "also has" shape of plan.py ("patients_if": {"has": b}): one UNION branch per patients count, none with an
OPTIONAL (organizers' example 8 took over 30 s on 5k patients with the tested detail OPTIONAL). No database."""
import pytest
from nl2q.compliance import rules as C
from nl2q.translation import plan as P
from nl2q.translation.prompt import finish
from nl2q.tools.check_examples import SCHEMA_TTL, Schema, links

ORG08 = {"route": "plan", "records": {"vd": {"type": "VariantDescriptor"}, "onco": {"type": "OncologyDiagnosis"}},
         "group": [{"code_class": "vd.variation"}],
         "output": [{"name": "patients", "agg": "patients"},
                    {"name": "het", "agg": "patients_if", "if": {"attr": "vd.zygosity", "op": "=", "value": "geno:0000135"}},
                    {"name": "hom", "agg": "patients_if", "if": {"attr": "vd.zygosity", "op": "=", "value": "geno:0000136"}},
                    {"name": "cancer", "agg": "patients_if", "if": {"has": "onco"}}],
         "order": [{"by": "patients", "desc": True}]}
HR_DRUG = {"route": "plan", "records": {"hr": {"type": "HeartRateMeasurement"},
                                        "drug": {"type": "DrugAdministrationEvent",
                                                 "where": [{"attr": "drug.hours", "op": ">", "value": 2}]}},
           "group": [{"sex": True}],
           "output": [{"name": "patients", "agg": "patients"}, {"name": "with_drug", "agg": "patients_if", "if": {"has": "drug"}}]}
CODED = {"route": "plan", "records": {"vd": {"type": "VariantDescriptor"},
                                      "dx": {"type": "BilledDiagnosis", "code": ["icd-10-gm:C50"]}},
         "group": [{"code_class": "vd.variation"}],
         "output": [{"name": "patients", "agg": "patients"}, {"name": "breast", "agg": "patients_if", "if": {"has": "dx"}}]}


@pytest.fixture(scope="module")
def schema():
    if not SCHEMA_TTL.exists():
        pytest.skip(f"no SPHN 2026.1 schema file at {SCHEMA_TTL}")
    return Schema(SCHEMA_TTL)


def branches(q):
    return q.split("\n    UNION\n")


@pytest.mark.parametrize("plan", [ORG08, HR_DRUG, CODED], ids=["org08", "hr_drug", "coded_other"])
def test_also_has_is_valid_and_has_no_optional_or_bind(plan, schema):
    assert P.check(plan) == []
    q = finish(P.compile(plan))
    assert "OPTIONAL" not in q and "BIND" not in q
    parsed, _ = C.parse(q)
    assert parsed is not None and links(parsed, schema) == [] and C.analyse(q)[1] == set()


def test_every_patients_if_next_to_a_has_is_its_own_branch():
    q = P.compile(ORG08)
    rows, het, hom, cancer = branches(q)
    assert "SELECT DISTINCT ?g ?vd_variation\n" in rows and "Zygosity" not in rows and "OncologyDiagnosis" not in rows
    for b, code in ((het, "GENO_0000135"), (hom, "GENO_0000136")):  # the tested detail required, then its test
        assert "?vd sphn:hasZygosityCode ?vd_zygosity_code . ?vd_zygosity_code rdf:type ?vd_zygosity ." in b
        assert f"FILTER (?vd_zygosity = <http://purl.obolibrary.org/obo/GENO_{code[5:]}>)" in b
        assert "OncologyDiagnosis" not in b
    assert "SELECT DISTINCT ?vd_variation (?g AS ?het_patient)" in het and "(?g AS ?hom_patient)" in hom
    assert "Zygosity" not in cancer  # the other record's patients first, then the rows
    assert cancer.index("SELECT DISTINCT ?g\n") < cancer.index("sphn:OncologyDiagnosis") < cancer.index("sphn:VariantDescriptor")
    for n in ("het", "hom", "cancer"):
        assert f"(COUNT(DISTINCT ?{n}_patient) AS ?{n})" in q
    assert "_none" not in q
    assert q.count('FILTER (STRSTARTS(STR(?vd_variation), "https://biomedit.ch/rdf/sphn-schema/sphn#")') == 4
    assert q.endswith('GROUP BY (REPLACE(STR(?vd_variation), "^.*[/#]", "") AS ?vd_variation_id)\nORDER BY DESC(?patients)')


def test_group_keys_and_a_coded_other_record_in_every_branch():
    q = P.compile(HR_DRUG)
    rows, drug = branches(q)
    for b in (rows, drug):
        assert "VALUES (?sex_class ?sex)" in b and "sphn:HeartRateMeasurement" in b
    assert "SELECT DISTINCT ?g ?sex\n" in rows and "SELECT DISTINCT ?sex (?g AS ?with_drug_patient)" in drug
    assert 'FILTER (bif:datediff("hour", ?drug_start, ?drug_end) > 2)' in drug and "?drug_start" not in rows
    coded = P.compile(CODED)
    rows, dx = branches(coded)
    assert "icd-10-gm" not in rows and dx.index("icd-10-gm/C50") < dx.index("sphn:VariantDescriptor")


def test_a_test_on_a_required_detail_keeps_it_in_every_branch():
    plan = {**HR_DRUG, "output": HR_DRUG["output"] + [{"name": "fast", "agg": "patients_if",
                                                        "if": {"attr": "hr.value", "op": ">", "value": 100}}]}
    assert P.check(plan) == []
    q = P.compile(plan)
    rows, drug, fast = branches(q)
    assert all("?hr_quantity sphn:hasValue ?hr_value ." in b for b in (rows, drug, fast))
    assert "FILTER (?hr_value > 100)" in fast and "FILTER (?hr_value" not in rows + drug
    assert "(COUNT(DISTINCT ?fast_patient) AS ?fast)" in q


def test_without_a_has_patients_if_stays_one_pattern():
    plan = {**ORG08, "records": {"vd": ORG08["records"]["vd"]}, "output": ORG08["output"][:3]}
    q = P.compile(plan)
    assert "UNION" not in q and "OPTIONAL { ?vd sphn:hasZygosityCode" in q
    assert "(COUNT(DISTINCT IF((BOUND(?vd_zygosity) && ?vd_zygosity = <http://purl.obolibrary.org/obo/GENO_0000135>), ?g, ?het_none)) AS ?het)" in q


def test_a_has_next_to_record_measures_is_left_to_free_generation():
    plan = {**HR_DRUG, "output": HR_DRUG["output"] + [{"name": "n", "agg": "records", "of": "hr"}]}
    with pytest.raises(P.Unsupported):
        P.compile(plan)
