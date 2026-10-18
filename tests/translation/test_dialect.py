import pytest
from nl2q import settings as S
from nl2q.translation.dialect import fix


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setattr(S, "DIALECT", True)


def test_round_with_digits():
    q = "SELECT (ROUND(AVG(?d), 2) AS ?a) WHERE { ?x <urn:p> ?d }"
    assert fix(q) == "SELECT ((ROUND((AVG(?d)) * 100.0) / 100.0) AS ?a) WHERE { ?x <urn:p> ?d }"
    assert fix(q.replace(", 2)", ", 0)")) == "SELECT (ROUND(AVG(?d)) AS ?a) WHERE { ?x <urn:p> ?d }"


def test_round_one_argument_unchanged():
    q = "SELECT (ROUND(1000.0 * ?a / ?b) / 10 AS ?p) WHERE { ?x <urn:p> ?a ; <urn:q> ?b }"
    assert fix(q) == q


def test_exists_and_exists_in_if():
    q = 'SELECT ?x WHERE { BIND (IF(EXISTS { ?s a <urn:a> } && EXISTS { ?t a <urn:b> }, "y", "n") AS ?x) }'
    assert fix(q) == 'SELECT ?x WHERE { BIND (IF(IF(EXISTS { ?s a <urn:a> }, EXISTS { ?t a <urn:b> }, false), "y", "n") AS ?x) }'


def test_exists_or():
    q = 'SELECT ?x WHERE { BIND (IF(EXISTS { ?s a <urn:a> } || ?y > 1, "y", "n") AS ?x) ?s <urn:p> ?y }'
    assert "IF(EXISTS { ?s a <urn:a> }, true, ?y > 1)" in fix(q)


def test_accepted_forms_unchanged():
    for q in ['SELECT ?x WHERE { ?x a <urn:a> FILTER (EXISTS { ?s a <urn:a> } && EXISTS { ?t a <urn:b> }) }',
              'SELECT ?x WHERE { BIND (IF(!EXISTS { ?s a <urn:a> } && ?y > 1, "a", "b") AS ?x) ?x <urn:p> ?y }',
              'SELECT ?x WHERE { BIND (IF(?y > 1 && EXISTS { ?s a <urn:a> }, "a", "b") AS ?x) ?x <urn:p> ?y }']:
        assert fix(q) == q


def test_off(monkeypatch):
    monkeypatch.setattr(S, "DIALECT", False)
    q = "SELECT (ROUND(?d, 2) AS ?a) WHERE { ?x <urn:p> ?d }"
    assert fix(q) == q
