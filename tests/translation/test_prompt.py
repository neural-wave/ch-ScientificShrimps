"""The prompt and the finished query, without a model: the few-shot examples parse as queries for Virtuoso, and
finish() declares exactly the prefixes a query uses."""
import pytest
from nl2q.compliance import analyse
from nl2q.translation.prompt import EXAMPLES, finish


@pytest.mark.parametrize("e", EXAMPLES, ids=range(len(EXAMPLES)))
def test_few_shot_examples_parse(e):
    analyse(finish(e["sparql"]))  # raises compliance.Invalid if it does not parse


def test_finish_declares_used_prefixes_only():
    q = finish("PREFIX x: <http://x#>\nSELECT (COUNT(?c) AS ?n) WHERE { ?c rdf:type icd-10-gm:E11 }")
    assert "PREFIX icd-10-gm:" in q and "PREFIX rdf:" in q and "PREFIX x:" not in q and "PREFIX ucum:" not in q
