"""k-anonymity (compliance/kanon.py, NL2Q_KANON off by default): the patient variable at the level that aggregates,
the HAVING rewrite on the official compliant queries and on quasi-identifier attacks, unsupported shapes, and flag mode
with a stub in place of Virtuoso. No model, no database."""
import json
from pathlib import Path
import pytest
from nl2q import settings as S
from nl2q.compliance import judge, kanon
from nl2q.compliance.kanon import RULE, flag, measure_query, patient, rewrite
from nl2q.compliance.rules import parse

HERE = Path(__file__).parent
OFFICIAL = [t for t in (json.loads(l) for l in open(HERE.parent / "bench" / "official.jsonl") if l.strip()) if t["compliant"]]
P = ("PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>\nPREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
     "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\nPREFIX icd-10-gm: <https://biomedit.ch/rdf/sphn-resource/icd-10-gm/>\n")
DX = '?c rdf:type icd-10-gm:E11 OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global") . ?dx sphn:hasCode ?c . ?dx rdf:type sphn:BilledDiagnosis .'
H = "HAVING (COUNT(DISTINCT ?g) >= 10)"
# where the clause lands in each official compliant query: before ORDER BY, else at the end; an ungrouped count is one
# group, and Virtuoso accepts HAVING only after a GROUP BY (SP031), hence GROUP BY ("all")
WHERE = {
    "O01": f"GROUP BY ?pathway\n{H}\nORDER BY DESC(?patients)",
    "O02": f"GROUP BY ?cohort\n{H}",
    "O04": f'}}\nGROUP BY ("all") {H}',
    "O05": f"GROUP BY ?route_agreement\n{H}\nORDER BY ?route_agreement",
    "O06": f"GROUP BY ?chop_code\n{H}\nORDER BY DESC(?patients)",
    "O07": f"GROUP BY ?route_concept\n{H}\nORDER BY DESC(?patients)",
    "O08": f"GROUP BY ?variant_type\n{H}\nORDER BY DESC(?patients)",
    "O09": f"GROUP BY ?seriousness\n{H}\nORDER BY DESC(?fatal_pct)",
    "O10": f"GROUP BY ?delivery_mode_concept\n{H}\nORDER BY DESC(?births)",
}
BIRTH = "?b rdf:type sphn:Birth ; sphn:hasDate ?bd . ?bd sphn:hasYear ?y ; sphn:hasMonth ?m ; sphn:hasDay ?d ."
ATTACKS = {  # quasi-identifiers: the rules let these through (they return counts), k-anonymity must not
    "count_on_one_birth_date": f'{P}SELECT (COUNT(DISTINCT ?g) AS ?patients) WHERE {{ GRAPH ?g {{ {BIRTH} }} '
                               f'FILTER (STR(?y) = "1950" && STR(?m) = "--03" && STR(?d) = "---14") }}',
    "count_per_birth_date": f"{P}SELECT ?y ?m ?d (COUNT(DISTINCT ?g) AS ?patients) WHERE {{ GRAPH ?g {{ {BIRTH} }} }} GROUP BY ?y ?m ?d",
    "avg_age_per_birth_date": f"{P}SELECT ?y ?m ?d (AVG(?age) AS ?mean_age) WHERE {{ GRAPH ?g {{ {BIRTH} ?dx rdf:type sphn:BilledDiagnosis ; sphn:hasSubjectAge ?a . "
                              f"?a sphn:hasQuantity/sphn:hasValue ?age . }} }} GROUP BY ?y ?m ?d",
}


def run_with(*sizes, seen=None):
    """A stub for store.run: the measuring query returns one row per group with its patient count."""
    def run(query, timeout):
        if seen is not None:
            seen.append((query, timeout))
        return ["x", "kanon_patients"], [("v", str(n)) for n in sizes]
    return run


@pytest.mark.parametrize("t", OFFICIAL, ids=[t["id"] for t in OFFICIAL])
def test_rewrite_official(t):
    new, why = rewrite(t["sparql"], 10)
    assert why is None and WHERE[t["id"]] in new
    assert new.count("COUNT(DISTINCT ?g) >= 10") == 1 and parse(new)[0] is not None and judge(new) is None
    assert t["sparql"] in {new.replace(x, "", 1) for x in (f"{H}\n", f"\n{H}", f'\nGROUP BY ("all") {H}')}  # nothing else changed


def test_official_cover_every_compliant_example():
    assert sorted(WHERE) == sorted(t["id"] for t in OFFICIAL)


@pytest.mark.parametrize("q", ATTACKS.values(), ids=ATTACKS.keys())
def test_quasi_identifier_attacks_get_the_having_clause(q):
    assert judge(q) is None  # compliant for the rules: an exact birth date as a group key may deserve a rule of its own
    new, why = rewrite(q, 10)
    assert why is None and new.rstrip().endswith(H) and judge(new) is None
    assert ("GROUP BY ?y ?m ?d" in new) != ('GROUP BY ("all")' in new)


def test_flag_birth_date_count():
    q = ATTACKS["count_on_one_birth_date"]
    seen = []
    r = flag(q, run_with(1, seen=seen), 10)
    assert r == ("Groups smaller than 10 patients may identify a patient: 1 of the 1 groups the query returns has fewer "
                 "than 10 patients (the smallest has 1).")
    assert "(COUNT(DISTINCT ?g) + 0 AS ?kanon_patients)" in seen[0][0] and seen[0][1] == S.KANON_TIMEOUT
    r = flag(ATTACKS["count_per_birth_date"], run_with(3, 25, 1), 10)
    assert r.lower().startswith(RULE.format(k=10)) and "2 of the 3 groups" in r and "smallest has 1" in r


@pytest.mark.parametrize("sizes, k, hit", [((10, 25), 10, False), ((9, 25), 10, True), ((9,), 5, False), ((4,), 5, True),
                                            ((0,), 10, False), ((), 10, False)])
def test_flag_threshold(sizes, k, hit):
    assert (flag(ATTACKS["count_per_birth_date"], run_with(*sizes), k) is not None) == hit


def test_flag_keeps_the_rules_verdict_when_the_run_fails():
    def boom(query, timeout):
        raise TimeoutError("read timed out")
    assert flag(ATTACKS["count_per_birth_date"], boom, 10) is None
    q = f"{P}SELECT (AVG(?n) AS ?mean) WHERE {{ {{ SELECT ?c (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ {DX} }} }} GROUP BY ?c }} }}"
    assert flag(q, run_with(1), 10) is None  # unsupported: never measured, never guessed


PATIENT = {  # the variable counted, at the level that aggregates
    "graph": (f"{P}SELECT (COUNT(?dx) AS ?n) WHERE {{ GRAPH ?g {{ {DX} }} }}", "g"),
    "official_subquery_alias": (f"{P}SELECT (COUNT(?pseudo_id) AS ?n) WHERE {{ {{ SELECT DISTINCT (?g AS ?pseudo_id) "
                                f"WHERE {{ GRAPH ?g {{ {DX} }} }} }} }}", "pseudo_id"),
    "group_key_of_subquery": (f"{P}SELECT ?k (COUNT(*) AS ?n) WHERE {{ {{ SELECT ?g (COUNT(?dx) AS ?k) WHERE {{ GRAPH ?g {{ {DX} }} }} "
                              f"GROUP BY ?g }} }} GROUP BY ?k", "g"),
    "pseudo_identifier_link": (f"{P}SELECT (COUNT(?dx) AS ?n) WHERE {{ {DX} ?dx sphn:hasSubjectPseudoIdentifier ?p . }}", "p"),
    "bind_alias": (f"{P}SELECT (COUNT(DISTINCT ?who) AS ?n) WHERE {{ {DX} ?dx sphn:hasSubjectPseudoIdentifier ?p . BIND(?p AS ?who) }}", "p"),
    "not_through_if": (f"{P}SELECT (COUNT(DISTINCT ?x) AS ?n) WHERE {{ {DX} ?dx sphn:hasSubjectPseudoIdentifier ?p . "
                       f"BIND(IF(BOUND(?c), ?p, ?c) AS ?x) FILTER(BOUND(?x)) }}", "p"),  # ?x may be ?c: the link ?p is the patient
    "union_both_sides": (f"{P}SELECT (COUNT(*) AS ?n) WHERE {{ {{ GRAPH ?g {{ {DX} }} }} UNION {{ GRAPH ?g {{ ?x rdf:type sphn:Birth }} }} }}", "g"),
}
UNSUPPORTED = {
    "no_patient_at_outer_level": (f"{P}SELECT (AVG(?n) AS ?mean) WHERE {{ {{ SELECT ?c (COUNT(DISTINCT ?g) AS ?n) "
                                  f"WHERE {{ GRAPH ?g {{ {DX} }} }} GROUP BY ?c }} }}", "no patient variable"),
    "patient_only_in_optional": (f"{P}SELECT ?c (COUNT(DISTINCT ?h) AS ?n) WHERE {{ {DX} OPTIONAL {{ GRAPH ?h {{ ?dx rdf:type sphn:BilledDiagnosis }} }} }} GROUP BY ?c",
                                 "no patient variable"),
    "two_patients": (f"{P}SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH ?g {{ {DX} }} GRAPH ?h {{ ?x rdf:type sphn:Birth }} }}", "several patient variables"),
    "nested_graph": (f"{P}SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH ?g {{ {DX} GRAPH ?h {{ ?x rdf:type sphn:Birth }} }} }}", "several patient variables"),
    "union_one_side": (f"{P}SELECT (COUNT(*) AS ?n) WHERE {{ {{ GRAPH ?g {{ {DX} }} }} UNION {{ ?x rdf:type sphn:Birth }} }}", "no patient variable"),
    "not_aggregated": (f"{P}SELECT ?c WHERE {{ GRAPH ?g {{ {DX} }} }}", "does not aggregate"),
}


@pytest.mark.parametrize("q, var", PATIENT.values(), ids=PATIENT.keys())
def test_patient_variable(q, var):
    assert str(patient(q)[0]) == var
    new, why = rewrite(q, 10)
    assert why is None and f"(COUNT(DISTINCT ?{var}) >= 10)" in new and parse(new)[0] is not None


@pytest.mark.parametrize("q, why", UNSUPPORTED.values(), ids=UNSUPPORTED.keys())
def test_unsupported_left_unchanged(q, why):
    pv, reason = patient(q)
    assert pv is None and why in reason
    new, note = rewrite(q, 10)
    assert new == q and note.startswith("unsupported") and measure_query(q)[0] is None


def test_text_scanning():
    q = ('DEFINE sql:select-option "order"\n' + P +
         '# a comment with { braces } and ORDER BY\n'
         'SELECT DISTINCT ?t (COUNT(DISTINCT ?g) AS ?n) (SUM(IF(?t = icd-10-gm:E11, 1, 0)) AS ?e)\n'
         'WHERE { GRAPH ?g { ' + DX + ' ?c rdf:type ?t . } FILTER (?t != "x } HAVING {" && !STRSTARTS(STR(?t), "https://a#b")) }\n'
         'GROUP BY ?t HAVING (COUNT(?dx) > 1) ORDER BY DESC(?n) LIMIT 5 # trailing comment')
    assert judge(q) is None
    new, why = rewrite(q, 7)
    assert why is None and "GROUP BY ?t HAVING (COUNT(DISTINCT ?g) >= 7) (COUNT(?dx) > 1) ORDER BY" in new
    mq, col = measure_query(q)
    assert col == "kanon_patients" and "SELECT DISTINCT (COUNT(DISTINCT ?g) + 0 AS ?kanon_patients) ?t" in mq
    q2 = q.replace("LIMIT 5 # trailing comment", "").replace("HAVING (COUNT(?dx) > 1) ORDER BY DESC(?n)", "")
    assert rewrite(q2, 7)[0].rstrip().endswith("GROUP BY ?t\nHAVING (COUNT(DISTINCT ?g) >= 7)")
    q3 = q.replace("?e)", "?kanon_patients)")
    assert measure_query(q3)[1] == "kanon_patients_"


def test_rewrite_limit_and_offset():
    q = f"{P}SELECT ?t (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ {DX} ?c rdf:type ?t . }} }} GROUP BY ?t LIMIT 10 OFFSET 5"
    assert f"GROUP BY ?t HAVING (COUNT(DISTINCT ?g) >= 10)\nLIMIT 10 OFFSET 5" in rewrite(q, 10)[0]


def test_switch(monkeypatch):
    q = ATTACKS["count_per_birth_date"]
    assert S.KANON == "off" and kanon(q, run_with(1)) == (q, None)  # the default: nothing changes
    monkeypatch.setattr(S, "KANON", "rewrite")
    assert kanon(q, run_with(1)) == (rewrite(q)[0], None) and H in kanon(q)[0]
    monkeypatch.setattr(S, "KANON", "flag")
    new, reason = kanon(q, run_with(2, 40))
    assert new == q and reason.startswith("Groups smaller than 10 patients may identify a patient")
    monkeypatch.setattr(S, "K", 2)
    assert kanon(q, run_with(2, 40)) == (q, None)
    monkeypatch.setattr(S, "KANON", "typo")
    assert kanon(q, run_with(1)) == (q, None)
