"""The generic-type training generator (nl2q.training.shapes.generic, --no-llm --no-db): every plan passes the checks,
names its type and compiles to a valid query."""
import pytest
from nl2q.training import make as M
from nl2q.training.shapes import generic as G
from nl2q.translation import plan as P
from nl2q.translation.prompt import finish
from nl2q.tools.query_check import valid

pytestmark = pytest.mark.skipif(not M.INVENTORY.exists(), reason="no 5k-patient inventory")


@pytest.fixture(scope="module")
def items():
    return G.sample(120, seed=3, db=False, log=lambda m: None)


def test_every_plan_passes_the_checks_and_compiles(items):
    assert len(items) == 120
    for it in items:
        plan, text = it["plan"], it["template"]
        assert P.check(plan) == [], (text, plan)
        assert P.type_problems(text, plan) == [], (text, plan)
        assert P.cue_mismatch(text, plan) is None and P.beyond(text) is None, text
        ok, why = valid(finish(P.compile(plan)))
        assert ok, (why, plan)
        assert not M.problems(text, it), text


def test_generic_types_and_forms(items):
    types = {r["type"] for it in items for r in it["plan"]["records"].values()}
    assert {"DrugPrescription", "BloodPressure"} <= types
    assert sum(t not in P.CURATED for t in types) >= 15
    assert {it["form"] for it in items} >= {"has", "threshold", "stat", "date", "detail", "group", "two"}


def test_units_are_stated(items):
    for it in items:
        for r in it["plan"]["records"].values():
            for w in r.get("where") or []:
                if w["attr"].endswith("unit"):
                    assert M.ucum_text(w["value"]) in it["template"], it["template"]
