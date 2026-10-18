"""The worked examples (config/examples.md, written by tools/make_examples): the file reads back as written, every example passes the automatic
checks, and each check catches what it is meant to catch. No model, no database; the links check needs the 2026.1 schema
file (NL2Q_SCHEMA_TTL), else skipped."""
import pytest
from nl2q.tools import check_examples as B
from nl2q.translation.examples import load

EXAMPLES = load()
SCHEMA = B.Schema(B.SCHEMA_TTL) if B.SCHEMA_TTL.exists() else None
INF = 'OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global")'
COUNT = "SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ {} }} }}"


@pytest.mark.parametrize("e", EXAMPLES, ids=[e["id"] for e in EXAMPLES])
def test_example_passes_the_checks(e):
    assert B.check(e, SCHEMA) == []


def test_ids_unique_and_families_known():
    ids = [e["id"] for e in EXAMPLES]
    assert len(ids) == len(set(ids))
    assert {e["family"] for e in EXAMPLES} <= set(ids)


def test_the_file_reads_back_as_written():
    org3 = next(e for e in EXAMPLES if e["id"] == "org-03")
    assert org3["compliant"] and org3["original_question"] and "hasRankCode" in org3["sparql"] and "hasRank " in org3["sparql_mock"]
    twin = next(e for e in EXAMPLES if e["id"] == "sphn-and-r5")
    assert twin["rule"] == "R5" and not twin["compliant"] and twin["family"] == "sphn-and" and twin["reason"]
    assert all(e["source"] for e in EXAMPLES)
    assert [e["id"] for e in EXAMPLES if e["always"]] == ["org-00", "ex-00"]


def ex(sparql, compliant=True, rule=None):
    return {"id": "t", "family": "t", "topic": "t", "kind": "t", "compliant": compliant, "rule": rule, "question": "q",
            "sparql": sparql}


def problems(e, schema=None):
    return {k for k, _ in B.check(e, schema)}


def test_catches_terms_outside_2026_1():
    assert "terms" in problems(ex(COUNT.format("?dx a sphn:BilledDiagnosis ; sphn:hasRank ?r .")))


def test_catches_year_coded_terminology_without_inference():
    assert "inference" in problems(ex(COUNT.format("?dx a sphn:BilledDiagnosis ; sphn:hasCode ?c . ?c a icd-10-gm:E11 .")))
    assert "inference" not in problems(ex(COUNT.format(f"?dx a sphn:BilledDiagnosis ; sphn:hasCode ?c . ?c a icd-10-gm:E11 {INF} .")))


def test_catches_a_wrong_label():
    assert "verdict" in problems(ex("SELECT ?dx WHERE { GRAPH ?g { ?dx a sphn:BilledDiagnosis } }"))
    assert "verdict" in problems(ex(COUNT.format("?dx a sphn:BilledDiagnosis ."), compliant=False, rule="R5"))


def test_catches_invalid_queries_prefix_lines_and_bad_fields():
    assert "valid" in problems(ex("SELECT (COUNT(* AS ?n) WHERE { ?s a ?t }"))
    assert "style" in problems(ex("PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>\n" + COUNT.format("?dx a sphn:BilledDiagnosis .")))
    assert "fields" in problems(ex(COUNT.format("?dx a sphn:BilledDiagnosis ."), compliant=False, rule=None))


@pytest.mark.skipif(SCHEMA is None, reason="no 2026.1 schema file (NL2Q_SCHEMA_TTL)")
def test_catches_a_property_on_the_wrong_record_type():
    assert "links" in problems(ex(COUNT.format("?dx a sphn:BilledDiagnosis ; sphn:hasAdmission ?a .")), SCHEMA)
    assert "links" in problems(ex(COUNT.format("?c a sphn:AdministrativeCase ; sphn:hasAdmission ?a . ?a sphn:hasRankCode ?r .")), SCHEMA)
    assert "links" not in problems(ex(COUNT.format("?c a sphn:AdministrativeCase ; sphn:hasAdmission/sphn:hasDateTime ?t .")), SCHEMA)


def test_explain_in_plain_words():
    text = B.explain(ex(COUNT.format(f"?c a icd-10-gm:E11 {INF} . ?dx sphn:hasCode ?c ; a sphn:BilledDiagnosis .")))
    assert "number of patients" in text and "Billed Diagnosis" in text and "icd-10-gm:E11 (and its sub-codes)" in text
    assert "ONE patient" in B.explain(ex("SELECT (COUNT(?e) AS ?n) WHERE { GRAPH <http://example.org/graph/p1> { ?e a sphn:LabTestEvent } }"))



def test_examples_come_only_from_official_material_or_rewrites_of_it():
    official = [e for e in EXAMPLES if e["source"].startswith("docs/official/")]
    made = [e for e in EXAMPLES if e["source"].startswith("made from ")]
    assert [e["id"] for e in EXAMPLES if e not in official + made] == ["ex-00"]
    assert {e["family"] for e in made} <= {e["id"] for e in official}


def test_the_file_is_what_the_generator_writes(tmp_path):
    from nl2q.tools import make_examples as M
    org = list(M.organizers())
    fresh = M.write(org[:1] + [M.TEAM] + org[1:] + list(M.templated()), tmp_path / "examples.md")
    assert fresh.read_text() == M.OUT.read_text(), "run: python -m nl2q.tools.make_examples"
