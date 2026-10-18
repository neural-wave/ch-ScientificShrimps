"""A "!=" compare also requires the other record: patients without a nationality are not "born outside every country
of citizenship"."""
from nl2q.translation import plan as P

O4_LIKE = {"route": "plan", "records": {"birth": {"type": "Birth"}, "nat": {"type": "Nationality"}},
           "cohort": {"compare": {"left": "birth.country", "op": "!=", "right": "nat.country"}},
           "output": [{"name": "patients", "agg": "patients"}]}


def test_not_equal_compare_needs_the_other_record():
    assert "BOUND(?nat_alias)" in P.compile(O4_LIKE)  # the patient must have a Nationality record


def test_equal_compare_is_unchanged():
    eq = {**O4_LIKE, "cohort": {"compare": {**O4_LIKE["cohort"]["compare"], "op": "="}}}
    assert P._with_other(eq["cohort"]) == eq["cohort"]
