"""rowcheck: what may run, the match classes and the terms the data lacks. No database: results are built by hand."""
import pytest
from nl2q.tools import rowcheck as R


def lit(v, dt=None):
    return {"type": "literal", "value": v, **({"datatype": dt} if dt else {})}


def res(cols, *rows):
    return {"status": "ok", "cols": cols, "rows": [tuple(lit(v) if v is not None else None for v in r) for r in rows]}


@pytest.mark.parametrize("q, ok", [
    ('DEFINE sql:select-option "order"\nSELECT ?x WHERE { ?x ?p ?o }', True),
    ('SELECT (bif:datediff("day", ?a, ?b) AS ?d) WHERE { ?s ?p ?o }', True),
    ("DELETE WHERE { ?s ?p ?o }", False),
    ("SELECT * WHERE { SERVICE <http://x> { ?s ?p ?o } }", False),
    ("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }", False),
    ('DEFINE input:grab-all "yes" SELECT * WHERE { ?s ?p ?o }', False),
    ('SELECT (bif:exec("x") AS ?e) WHERE { }', False),
    ('SELECT ?x WHERE { ?x <http://a/DELETE> "INSERT" }', True),  # keywords inside IRIs and strings do not count
])
def test_runnable(q, ok):
    assert (R.runnable(q) is None) == ok


@pytest.mark.parametrize("ours, ref, cls", [
    (res(["a", "n"], ("x", "3"), ("y", "4")), res(["n", "b"], ("4", "y"), ("3", "x")), "identical"),
    (res(["n"], ("0.3333333333",)), res(["m"], ("0.333333",)), "same up to format"),
    (res(["n"], ("3",)), res(["m"], ("3.0",)), "same up to format"),
    (res(["k", "n", "extra"], ("x", "3", "1"), ("y", "4", "1")), res(["k", "n"], ("x", "3"), ("y", "4")), "same numbers other shape"),
    (res(["k", "n"], ("x", "3")), res(["k", "n"], ("x", "3"), ("y", "4")), "subset/superset"),
    (res(["n"], ("0",)), res(["n"], ("5",)), "empty on one side"),
    (res(["n"]), res(["n"], ("0",)), "both empty"),
    (res(["n"], ("7",)), res(["n"], ("5",)), "different"),
])
def test_classes(ours, ref, cls):
    assert R.compare(ours, ref)["class"] == cls


def test_single_numbers_and_ratio():
    out = R.compare(res(["n"], ("10",)), res(["m"], ("5",)))
    assert out["numbers"] == [[10.0], [5.0]] and out["ratio"] == 2.0


def test_unseen_terms_and_codes():
    u = R.unseen("SELECT * WHERE { ?e sphn:hasDrugPrescription ?p . ?c a icd-10-gm:A00 OPTION (inference \"x\") . "
                 "?d a snomed:1 . ?b sphn:hasAdministrationRouteCode ?r }")
    assert u["terms"] == ["sphn:hasDrugPrescription"] and u["inference"]
    assert u["codes"]["icd-10-gm:A00"] in ("present",) or u["codes"]["icd-10-gm:A00"].startswith("sub-codes")
    assert u["codes"]["snomed:1"] == "absent (exact)"  # under inference a SNOMED parent may still match
