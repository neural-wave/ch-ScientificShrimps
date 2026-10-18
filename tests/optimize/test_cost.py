"""The cost model (nl2q/optimize/cost.py) ranks queries by the rows Virtuoso visits at 100k; optbench calibrate checks it
against the timings. No database."""
from nl2q.optimize.cost import cost

HEAD = ('DEFINE sql:select-option "order"\nPREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n'
        'PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>\nPREFIX icd-10-gm: <https://biomedit.ch/rdf/sphn-resource/icd-10-gm/>\n')
INF = 'OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global")'
CODE = f"?c a icd-10-gm:E11 {INF} . ?d sphn:hasCode ?c . ?d a sphn:BilledDiagnosis ."
CLASS = f"?d a sphn:BilledDiagnosis . ?d sphn:hasCode ?c . ?c a icd-10-gm:E11 {INF} ."


def q(body, head=HEAD):
    return head + f"SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE {{ GRAPH ?g {{ {body} }} }}"


def test_code_first_is_cheaper():  # measured: 0.01 s against 1.39 s
    assert cost(q(CODE)) * 10 < cost(q(CLASS))


def test_a_term_the_data_lacks_empties_the_group():  # a 2026.1 property on the 2024.2 data (O02#4: 0.00 s)
    assert cost(q("?case a sphn:AdministrativeCase . ?case sphn:hasAdmission ?a .")) == 0


def test_optional_under_the_pragma_runs_once_per_row_unbound():  # O08: 60 s cap with the pragma, 3 s without
    body = f"{CODE} OPTIONAL {{ ?x a sphn:OncologyDiagnosis . }}"
    assert cost(q(body)) > 10 * cost(q(body, HEAD.replace('DEFINE sql:select-option "order"\n', "")))


def test_unreadable():
    assert cost("not sparql {") is None
