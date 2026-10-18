"""The compliance check on the official examples (queries-example.md, API_specification.md) and on targeted cases,
one or more per official rule. No model, no database. python -m pytest nl2q -q"""
import json
from pathlib import Path
import pytest
from nl2q.compliance import Invalid, analyse, reason

HERE = Path(__file__).parent
BENCH = [json.loads(l) for f in sorted((HERE.parent / "bench").glob("*.jsonl")) for l in open(f) if l.strip()]
P = ("PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>\nPREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
     "PREFIX icd-10-gm: <https://biomedit.ch/rdf/sphn-resource/icd-10-gm/>\n")
INF = 'OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global")'
DX = f"?c rdf:type icd-10-gm:E11 {INF} . ?dx sphn:hasCode ?c . ?dx rdf:type sphn:BilledDiagnosis ."


def rules(q):
    return {r for r, _ in analyse(q)[0]}


@pytest.mark.parametrize("t", BENCH, ids=[t["id"] for t in BENCH])
def test_bench_reference_verdicts(t):
    assert (not rules(t["sparql"])) == t["compliant"], reason(analyse(t["sparql"])[0])


NON_COMPLIANT = {
    "R1_fixed_graph": (f"{P}SELECT (COUNT(?dx) AS ?n) WHERE {{ GRAPH <http://example.org/graph/p1> {{ {DX} }} }}", "R1"),
    "R1_filter_graph": (f'{P}SELECT (COUNT(?dx) AS ?n) WHERE {{ GRAPH ?g {{ {DX} }} FILTER(STR(?g) = "http://example.org/graph/p1") }}', "R1"),
    "R1_values_graph": (f"{P}SELECT (COUNT(?dx) AS ?n) WHERE {{ VALUES ?g {{ <http://x/p1> }} GRAPH ?g {{ {DX} }} }}", "R1"),
    "R1_record_iri": (f"{P}SELECT (COUNT(?c) AS ?n) WHERE {{ GRAPH ?g {{ <https://biomedit.ch/rdf/sphn-resource/CHE-1-sphn-BilledDiagnosis-9> sphn:hasCode ?c }} }}", "R1"),
    "R1_from": (f"{P}SELECT (COUNT(?dx) AS ?n) FROM <http://example.org/graph/p1> WHERE {{ {DX} }}", "R1"),
    "R2_rows": (f"{P}SELECT ?dt WHERE {{ GRAPH ?g {{ {DX} ?dx sphn:hasRecordDateTime ?dt }} }}", "R2"),
    "R2_group_by_patient_hidden": (f"{P}SELECT (COUNT(?dx) AS ?n) WHERE {{ GRAPH ?g {{ {DX} }} }} GROUP BY ?g", "R2"),
    "R3_spo": ("SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }", "R3"),
    "R4_ask": (f"{P}ASK {{ GRAPH ?g {{ {DX} }} }}", "R4"),
    "R4_describe": (f"{P}DESCRIBE ?dx WHERE {{ GRAPH ?g {{ {DX} }} }}", "R4"),
    "R4_insert": (f"{P}INSERT DATA {{ GRAPH <http://x> {{ <http://x/a> rdf:type sphn:BilledDiagnosis }} }}", "R4"),
    "R4_grab": (f'DEFINE input:grab-all "yes"\n{P}SELECT (COUNT(?dx) AS ?n) WHERE {{ GRAPH ?g {{ {DX} }} }}', "R4"),
    "R5_group_concat_graph": (f'{P}SELECT (COUNT(DISTINCT ?g) AS ?n) (GROUP_CONCAT(STR(?g); separator=",") AS ?ids) WHERE {{ GRAPH ?g {{ {DX} }} }}', "R5"),
    "R5_min_of_alias": (f"{P}SELECT (MIN(?p) AS ?first) WHERE {{ GRAPH ?g {{ {DX} }} BIND(?g AS ?p) }}", "R5"),
    "R5_group_by_record": (f"{P}SELECT ?dx (COUNT(?c) AS ?n) WHERE {{ GRAPH ?g {{ {DX} }} }} GROUP BY ?dx", "R5"),
    "R5_str_of_record": (f"{P}SELECT ?k (COUNT(*) AS ?n) WHERE {{ GRAPH ?g {{ {DX} }} BIND(STR(?dx) AS ?k) }} GROUP BY ?k", "R5"),
    "R5_case_identifier": (f"{P}SELECT ?id (COUNT(?dx) AS ?n) WHERE {{ GRAPH ?g {{ {DX} ?dx sphn:hasAdministrativeCase ?case . ?case sphn:hasIdentifier ?id }} }} GROUP BY ?id", "R5"),
    # validated on the mock data (17 Oct): each of these returned exactly one patient's graph, or every typed node
    "R1_identifier_through_path": (f'{P}SELECT (COUNT(?dx) AS ?n) WHERE {{ GRAPH ?g {{ ?dx rdf:type sphn:Diagnosis ; sphn:hasAdministrativeCase/sphn:hasIdentifier "case-7" }} }}', "R1"),
    "R1_record_id_on_code_node": (f'{P}SELECT (COUNT(?dx) AS ?n) WHERE {{ GRAPH ?g {{ ?dp rdf:type sphn:DataProvider ; sphn:hasInstitutionCode ?c . ?c sphn:hasIdentifier "ID-10013960000003" . ?dx rdf:type sphn:Diagnosis ; sphn:hasDataProvider ?dp }} }}', "R1"),
    "R1_uuid_in_string": (f'{P}SELECT (COUNT(?dx) AS ?n) WHERE {{ GRAPH ?g {{ ?dx rdf:type sphn:Diagnosis ; sphn:hasAdministrativeCase ?case }} FILTER(CONTAINS(STR(?case), "c894b788-f635-4c4b-9f27-8f271090b96f")) }}', "R1"),
    "R1_record_iri_vs_string": (f'{P}SELECT (COUNT(?dx) AS ?n) WHERE {{ GRAPH ?g {{ ?dx rdf:type sphn:Diagnosis ; sphn:hasAdministrativeCase ?case }} FILTER(STRENDS(STR(?case), "c894b788")) }}', "R1"),
    "R1_graph_from_string": (f'{P}SELECT (COUNT(?dx) AS ?n) WHERE {{ BIND(IRI("http://example.org/graph/gen_mock_data-1_1001396") AS ?pg) GRAPH ?pg {{ ?dx rdf:type sphn:Diagnosis }} }}', "R1"),
    "R3_detached_pattern": (f"{P}SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH ?g {{ ?x rdf:type sphn:Age . ?s rdf:type ?t }} }}", "R3"),
    "R4_bif_exec": (f'{P}SELECT (COUNT(?dx) AS ?n) (SAMPLE(bif:exec("SELECT 1")) AS ?x) WHERE {{ GRAPH ?g {{ {DX} }} }}', "R4"),
    "R5_unpinned_code_identifier": (f"{P}SELECT ?id (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ ?dx rdf:type sphn:Diagnosis ; sphn:hasCode ?c . ?c sphn:hasIdentifier ?id }} }} GROUP BY ?id", "R5"),
    # only a file hash's algorithm is a value: on an imaging frame (2026.1) it is a DataCompressionAlgorithm node
    "R5_compression_algorithm_node": (f"{P}SELECT ?alg (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ ?fr rdf:type sphn:ImagingFrame ; sphn:hasAlgorithm ?alg }} }} GROUP BY ?alg", "R5"),
    "R5_group_by_hash_node": (f"{P}SELECT ?h (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ ?h rdf:type sphn:Hash ; sphn:hasAlgorithm ?alg }} }} GROUP BY ?h", "R5"),
}
COMPLIANT = {
    "official_api_example": 'DEFINE sql:select-option "order"\n' + P + "PREFIX einstein: <https://biomedit.ch/rdf/sphn-resource/einstein/shortcuts#>\n"
                            f"SELECT (COUNT(?pseudo_id) AS ?countResult) WHERE {{ {{ SELECT DISTINCT(?g AS ?pseudo_id) WHERE {{ GRAPH ?g {{ {DX} }} }} }} }}",
    "count_distinct_graphs_by_year": f"{P}SELECT ?y (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ {DX} ?dx sphn:hasRecordDateTime ?t }} BIND(YEAR(?t) AS ?y) }} GROUP BY ?y",
    "graph_prefix_filter": f'{P}SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ {DX} }} FILTER(STRSTARTS(STR(?g), "http://example.org/graph/")) }}',
    "code_identifier_join": f'{P}SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ ?dx sphn:hasCode ?c . ?c sphn:hasIdentifier "X1" }} }}',
    "subquery_grouped_by_patient": f"{P}SELECT (AVG(?k) AS ?mean) WHERE {{ {{ SELECT ?g (COUNT(?dx) AS ?k) WHERE {{ GRAPH ?g {{ {DX} }} }} GROUP BY ?g }} }}",
    # the mock data is SPHN 2024.2: its dates are values in the verdict, as their 2026.1 successors are
    "dates_2024_2": f"{P}SELECT (MIN(?a) AS ?first) (AVG(bif:datediff('day', ?a, ?d)) AS ?days) WHERE {{ GRAPH ?g {{ ?case rdf:type sphn:AdministrativeCase ; sphn:hasAdmissionDateTime ?a ; sphn:hasDischargeDateTime ?d }} }}",
    "dates_2026_1": f"{P}SELECT (MIN(?t) AS ?first) WHERE {{ GRAPH ?g {{ ?case rdf:type sphn:AdministrativeCase ; sphn:hasAdmission ?a . ?a sphn:hasDateTime ?t }} }}",
    "rank_2024_2": f"{P}PREFIX sphn-individual: <https://biomedit.ch/rdf/sphn-schema/sphn/individual#>\nSELECT (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ {DX} ?dx sphn:hasRank ?r }} FILTER(?r = sphn-individual:Principal) }}",
    # real ICD codes on sphn:Diagnosis exist only as strings on generic code nodes
    "string_icd_code_through_path": f'{P}SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ ?dx rdf:type sphn:Diagnosis ; sphn:hasCode/sphn:hasIdentifier "E78.5" }} }}',
    "code_identifier_filtered": f'{P}SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ ?dx rdf:type sphn:Diagnosis ; sphn:hasCode ?c . ?c sphn:hasIdentifier ?id }} FILTER(?id IN ("E78.5", "I10.90")) }}',
    "code_identifier_pinned_by_coding_system": f'{P}SELECT ?id (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ ?dx rdf:type sphn:Diagnosis ; sphn:hasCode ?c . ?c sphn:hasIdentifier ?id ; sphn:hasCodingSystemAndVersion "ICD-10-GM 2024" }} }} GROUP BY ?id',
    "patterns_joined_by_filter": f"{P}SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ {DX} ?dx sphn:hasRecordDateTime ?t1 . ?e sphn:hasStartDateTime ?t2 }} FILTER(?t2 > ?t1) }}",
    # a file hash's algorithm is one of sphn-individual:MD5, SHA256, SHA512: a category, like a sex or an outcome
    "hash_algorithm": f"{P}SELECT ?alg (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ ?h rdf:type sphn:Hash ; sphn:hasAlgorithm ?alg }} }} GROUP BY ?alg",
    "hash_algorithm_of_file": f"{P}SELECT ?alg (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ ?f rdf:type sphn:DataFile ; sphn:hasHash ?h . ?h sphn:hasAlgorithm ?alg }} }} GROUP BY ?alg",
    "hash_algorithm_path": f"{P}SELECT ?alg (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ ?f rdf:type sphn:DataFile ; sphn:hasHash/sphn:hasAlgorithm ?alg }} }} GROUP BY ?alg",
}


@pytest.mark.parametrize("q, rule", NON_COMPLIANT.values(), ids=NON_COMPLIANT.keys())
def test_non_compliant(q, rule):
    assert rule in rules(q), reason(analyse(q)[0]) or "judged compliant"


@pytest.mark.parametrize("q", COMPLIANT.values(), ids=COMPLIANT.keys())
def test_compliant(q):
    assert not rules(q), reason(analyse(q)[0])


@pytest.mark.parametrize("q", ["SELECT (COUNT(* AS ?n) WHERE { ?s a ?t }",
                               f'{P}SELECT (COUNT(?c) AS ?n) WHERE {{ ?c rdf:type icd-10-gm:E11 OPTION (inference "http://other") }}'])
def test_invalid(q):
    with pytest.raises(Invalid):
        analyse(q)


def test_reason_names_every_broken_rule():
    r = reason(analyse(f"{P}SELECT ?g ?id WHERE {{ GRAPH ?g {{ ?s sphn:hasIdentifier ?id }} }}")[0])
    assert r.startswith("Queries must always return aggregated") and "No identifiers" in r


def test_unknown_terms_are_reported_not_judged():
    v, unknown = analyse(f"{P}SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ ?dx rdf:type sphn:NoSuchThing }} }}")
    assert not v and unknown == {"https://biomedit.ch/rdf/sphn-schema/sphn#NoSuchThing"}


def test_checker_failure_is_not_compliant(monkeypatch):
    import nl2q.compliance.verdict as verdict
    monkeypatch.setattr(verdict, "analyse", lambda q: 1 / 0)
    assert verdict.judge("SELECT * WHERE { ?s ?p ?o }").startswith("Not judged compliant")
    assert verdict.validate("SELECT * WHERE { ?s ?p ?o }") is None
