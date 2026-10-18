"""Grouped plans on the 100k-patient data (no database, no model): GROUP BY (REPLACE(...) AS ?k) straight over the rows
put every row in a group of its own there (organizers' example 6: 70,130 rows, not 4; drug routes: 277,127, not 101),
and the code key next to two DISTINCT counts failed ("Max row length is exceeded"). The rows are grouped by plain
variables first (plan._keyed), the key over those groups; a class that is one code is the key itself when records
are counted."""
import json
import re
from nl2q.tools.query_check import to_2024, valid
from nl2q.translation import plan as P
from nl2q.translation.prompt import finish

PLANS = {json.loads(line)["id"]: json.loads(line)["plan"] for line in (P.HERE / "resources" / "plans.jsonl").read_text().splitlines()}
ROUTES = {"route": "plan", "records": {"drug": {"type": "DrugAdministrationEvent"}}, "group": [{"code_class": "drug.route", "ns": "snomed"}],
          "output": [{"name": "patients", "agg": "patients"}, {"name": "avg_days", "agg": "avg", "of": "drug.days"},
                     {"name": "patients_in_stay", "agg": "patients_if", "if": {"attr": "drug.in_stay", "op": "=", "value": True}}],
          "order": [{"by": "patients", "desc": True}]}


def inner(q):
    """(variables, GROUP BY variables) of the sub-query that groups the rows."""
    m = re.search(r"\n        SELECT (\?[^\n]*)\n        WHERE \{\n.*?\n        \}\n        GROUP BY (\?[^\n]*)\n    \}\n(?:    VALUES [^\n]*\n)?\}\n",
                  q, re.S)
    assert m, q
    return m.group(1).split(), m.group(2).split()


def test_versioned_code_key_is_grouped_over_the_grouped_rows():
    q = finish(P.compile(PLANS["org-06"]))
    cols, by = inner(q)
    assert cols == by and {"?g", "?proc", "?proc_code", "?proc_class", "?proc_start", "?proc_end", "?proc_rank"} <= set(cols)
    assert "_none" not in " ".join(cols)
    # the year versions of a CHOP code merge in the outer key, after the rows' plain grouping
    assert q.index("GROUP BY ?g ") < q.index('GROUP BY (REPLACE(STR(?proc_class), "^.*[/#]", "") AS ?proc_code_id)')
    assert q.endswith('GROUP BY (REPLACE(STR(?proc_class), "^.*[/#]", "") AS ?proc_code_id)\nORDER BY DESC(?patients)')
    assert "SELECT ?proc_code_id (COUNT(DISTINCT ?g) AS ?patients)" in q
    assert q.index("OPTION (inference") < q.index("?proc sphn:hasCode")  # the code triple still first
    assert valid(q) == (True, None)


def test_route_key_over_the_grouped_rows_keeps_every_output():
    q = finish(P.compile(ROUTES))
    cols, by = inner(q)
    used = set(re.findall(r"\?\w+", q[q.index("SELECT"):q.index("\nWHERE")])) - {"?drug_route_id", "?patients", "?avg_days",
                                                                                  "?patients_in_stay", "?patients_in_stay_none"}
    assert used <= set(cols) == set(by)  # every variable the outputs use comes out of the grouped rows
    assert 'GROUP BY (REPLACE(STR(?drug_route), "^.*[/#]", "") AS ?drug_route_id)' in q and "BIND" not in q
    assert valid(q) == (True, None)
    q24, mapped = to_2024(q)
    assert mapped == ["stay dates"] and inner(q24)[0] == cols  # the 2024.2 mapping leaves the grouping alone


def test_one_code_per_class_with_records_counted_is_grouped_by_the_class():
    q = finish(P.compile(PLANS["org-07"]))
    assert q.endswith("GROUP BY ?drug_route\nORDER BY DESC(?patients)")
    assert 'SELECT (REPLACE(STR(?drug_route), "^.*[/#]", "") AS ?drug_route_id) (COUNT(DISTINCT ?g) AS ?patients) ' \
           "(COUNT(DISTINCT ?drug) AS ?administrations)" in q
    assert inner(q)[1][0] == "?g" and valid(q) == (True, None)


def test_versioned_code_with_records_counted_keeps_the_merged_key():
    plan = {"route": "plan", "records": {"dx": {"type": "BilledDiagnosis", "code": ["icd-10-gm:E11"]}}, "group": [{"code_class": "dx"}],
            "output": [{"name": "patients", "agg": "patients"}, {"name": "n", "agg": "records", "of": "dx"}]}
    assert P.check(plan) == []
    q = P.compile(plan)
    assert q.endswith('GROUP BY (REPLACE(STR(?dx_class), "^.*[/#]", "") AS ?dx_code_id)')  # .../2024/E11.9 = .../2023/E11.9
    assert "?dx_class" in inner(q)[1]


def test_year_and_value_keys_are_grouped_over_the_grouped_rows():
    year = {"route": "plan", "records": {"drug": {"type": "DrugAdministrationEvent"}}, "group": [{"year": "drug.start"}],
            "output": [{"name": "patients", "agg": "patients"}]}
    q = P.compile(year)
    assert q.endswith("GROUP BY (YEAR(?drug_start) AS ?drug_year)") and "?drug_start" in inner(q)[1]
    stay = {"route": "plan", "records": {"stay": {"type": "AdministrativeCase"}}, "group": [{"code_class": "stay.insurance"}],
            "output": [{"name": "patients", "agg": "patients"}, {"name": "n", "agg": "records", "of": "stay"}]}
    q = P.compile(stay)
    assert q.endswith("GROUP BY ?stay_insurance")  # a value-set individual is one value


def test_sex_alone_and_no_key_stay_one_level():
    sex = {"route": "plan", "records": {"lab": {"type": "LabTestEvent", "code": ["loinc:718-7"]}}, "group": [{"sex": True}],
           "output": [{"name": "patients", "agg": "patients"}]}
    q = P.compile(sex)
    assert q.endswith("GROUP BY ?sex") and "        GROUP BY" not in q
    plain = {"route": "plan", "records": {"lab": {"type": "LabTestEvent", "code": ["loinc:718-7"]}},
             "output": [{"name": "v", "agg": "avg", "of": "lab.value"}]}
    assert "GROUP BY" not in P.compile(plain)
