"""The syntax guard (translation/grammar.py, resources/sparql.ebnf) without a model: the grammar accepts every query of
the examples and of the bench as the generator writes it (without PREFIX and DEFINE lines, alone or in a ```sparql
block) and the forms an unsafe request needs, and rejects what would not parse or is more than the query.
xgrammar is in the vLLM venv (serving/serve_llm.sh), not in .venv: the check runs there in a subprocess, skipped where
that venv is missing (NL2Q_VLLM_PYTHON points to another interpreter)."""
import json, os, re, subprocess
from pathlib import Path
import pytest
from nl2q import schema as SC
from nl2q import settings as S
from nl2q.compliance.rules import VIRTUOSO_PREFIXES, parse
from nl2q.translation.examples import load
from nl2q.translation.grammar import GRAMMAR
from nl2q.translation.prompt import finish

_SCRATCH = Path(os.getenv("SCRATCH", f"/data/scratch/{os.getenv('USER', '')}"))  # as serving/serve_llm.sh
_SCRATCH = _SCRATCH if _SCRATCH.is_dir() else Path.home() / ".cache" / "nl2q"
VLLM_PYTHON = Path(os.getenv("NL2Q_VLLM_PYTHON", _SCRATCH / "venvs" / "vllm" / "bin" / "python"))
CHECK = """
import json, sys
import xgrammar as xgr
grammar, texts = json.load(sys.stdin)
compiled = xgr.GrammarCompiler(xgr.TokenizerInfo([]), cache_enabled=False).compile_grammar(grammar)
def stop(text):  # None if the grammar accepts the whole text, else the offset where it stops accepting
    matcher = lambda: xgr.GrammarMatcher(compiled, terminate_without_stop_token=True)
    m = matcher()
    if m.accept_string(text) and m.is_terminated():
        return None
    m = matcher()
    return next((i for i, ch in enumerate(text) if not m.accept_string(ch)), len(text))
print(json.dumps([stop(t) for t in texts]))
"""


def body(q):
    """The query as the generator writes it: finish() adds the PREFIX and DEFINE lines."""
    return re.sub(r"(?im)^\s*(DEFINE|PREFIX)\b[^\n]*\n?", "", q).strip()


BENCH = sorted((Path(__file__).parents[1] / "bench").glob("*.jsonl"))
_ALL = [(e["id"], body(e["sparql"])) for e in load()] + [
    (r["id"], body(r["sparql"])) for f in BENCH for r in map(json.loads, filter(str.strip, f.read_text().splitlines()))]
_OLD = lambda q: any(SC.NS + t not in SC.TERMS for t in re.findall(r"sphn:(\w+)", q))  # SPHN 2024.2 names
QUERIES = [(i, q) for i, q in _ALL if not _OLD(q)]
OLD_NAMES = [(i, q) for i, q in _ALL if _OLD(q)]  # some bench references keep 2024.2 names (hasRank, ...): 2026.1 has not
UNSAFE = [  # translated literally, refused by the checker, so the grammar must let the model write them
    "DESCRIBE ?p WHERE { GRAPH ?g { ?p a sphn:SubjectPseudoIdentifier } }",
    "DESCRIBE <http://example.org/graph/mock_data-1_0042>",
    "CONSTRUCT { ?s ?p ?o } WHERE { GRAPH ?g { ?s ?p ?o } }",
    "ASK { GRAPH ?g { ?d a sphn:Death } }",
    'INSERT DATA { GRAPH <http://example.org/graph/x> { <http://example.org/d> sphn:hasCode "x" } }',
    'DELETE DATA { GRAPH <http://example.org/graph/x> { <http://example.org/d> sphn:hasCode "x" } }',
    "DELETE WHERE { GRAPH ?g { ?s ?p ?o } }",
    "DELETE { GRAPH ?g { ?d ?p ?o } } INSERT { GRAPH ?g { ?d ?p 0 } } WHERE { GRAPH ?g { ?d a sphn:Death ; ?p ?o } }",
    "INSERT { GRAPH ?g { ?d sphn:hasCode 1 } } WHERE { GRAPH ?g { ?d a sphn:Death } }",
    "LOAD <http://example.org/data.ttl> INTO GRAPH <http://example.org/graph/x>",
    "CLEAR GRAPH <http://example.org/graph/x>",
    "DROP ALL",
    "CREATE GRAPH <http://example.org/graph/x>",
    "SELECT ?s WHERE { SERVICE <http://example.org/sparql> { ?s ?p ?o } }",
    "SELECT (COUNT(*) AS ?n) WHERE { GRAPH <http://example.org/graph/mock_data-1_0042> { ?s ?p ?o } }",
]
BLOCKS = ["```sparql\nSELECT * WHERE { ?s ?p ?o }\n```", "```\nASK { ?s ?p ?o }```"]  # as the prompt asks
REJECTED = {
    "prose": "The query: SELECT * WHERE { ?s ?p ?o }",
    "prose-before-block": "Here it is:\n```sparql\nSELECT * WHERE { ?s ?p ?o }\n```",
    "prose-after-block": "```sparql\nSELECT * WHERE { ?s ?p ?o }\n```\nIt lists every triple.",
    "unclosed-block": "```sparql\nSELECT * WHERE { ?s ?p ?o }\n",
    "other-language": "```sql\nSELECT * WHERE { ?s ?p ?o }\n```",
    "prefix-line": "PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>\nSELECT * WHERE { ?s ?p ?o }",
    "define-line": 'DEFINE sql:select-option "order"\nSELECT * WHERE { ?s ?p ?o }',
    "comment": "SELECT * WHERE { ?s ?p ?o } # every triple",
    "leading-blank": "\nSELECT * WHERE { ?s ?p ?o }",
    "unclosed-brace": "SELECT * WHERE { ?s ?p ?o",
    "sql-case": "SELECT ?b WHERE { ?s ?p ?a BIND (CASE WHEN ?a THEN 1 ELSE 0 END AS ?b) }",
    "glued-keywords": "SELECT ?s WHERE { ?s ?p ?o } ORDERBY ?s",
    "glued-variable": "SELECT (?sAS ?t) WHERE { ?s ?p ?o }",
    "missing-dot": "SELECT ?s WHERE { ?s a sphn:Death ?t a sphn:Death }",
    "bare-aggregate": "SELECT COUNT(*) WHERE { ?s ?p ?o }",
    "other-rule-set": 'SELECT ?c WHERE { ?c a icd-10-gm:E11 OPTION (inference "sphn") }',
    "empty-prefix": "SELECT ?s WHERE { ?s a :Death }",
    "unknown-prefix": "SELECT ?s WHERE { ?s a sphn-schema:Death }",
    "invented-sphn-term": "SELECT ?s WHERE { ?s sphn:hasDiagnosisCode ?c }",
    "sql-null": "SELECT ?x WHERE { ?s sphn:hasValue ?a BIND (IF(?a > 1, ?a, NULL) AS ?x) }",
    "describe-all-without-where": "DESCRIBE *",
    "long-blank-run": "SELECT * WHERE {" + " " * 101 + "?s ?p ?o }",
    "empty": "",
}


@pytest.fixture(scope="module")
def stops():
    """{text: None if the grammar accepts it, else the offset where it stops accepting}, in one xgrammar run."""
    if not VLLM_PYTHON.exists():
        pytest.skip(f"no vLLM venv (xgrammar) at {VLLM_PYTHON}")
    texts = [q for _, q in QUERIES + OLD_NAMES] + BLOCKS + UNSAFE + list(REJECTED.values())
    out = subprocess.run([str(VLLM_PYTHON), "-c", CHECK], input=json.dumps([GRAMMAR, texts]), capture_output=True,
                         text=True, timeout=600)
    assert out.returncode == 0, out.stderr[-2000:]
    return dict(zip(texts, json.loads(out.stdout.strip().splitlines()[-1])))


@pytest.mark.parametrize("query", [q for _, q in QUERIES], ids=[i for i, _ in QUERIES])
def test_examples_and_bench_queries_are_accepted(stops, query):
    at = stops[query]
    assert at is None, f"rejected at char {at}: ...{query[max(0, at - 80):at]} >>> {query[at:at + 20]}"


@pytest.mark.parametrize("text", BLOCKS)
def test_accepts_the_query_in_a_sparql_block(stops, text):
    assert stops[text] is None


@pytest.mark.parametrize("query", UNSAFE)
def test_unsafe_forms_stay_writable(stops, query):
    assert stops[query] is None


@pytest.mark.parametrize("query", UNSAFE)
def test_unsafe_forms_are_valid_sparql(query):
    parse(finish(query))  # raises compliance.Invalid if not: the checker, not the grammar, refuses them


@pytest.mark.parametrize("text", REJECTED.values(), ids=REJECTED.keys())
def test_rejects_what_would_not_parse_or_is_more_than_the_query(stops, text):
    assert stops[text] is not None


def test_grammar_has_a_root_and_the_rule_set():
    assert "\nroot ::= " in GRAMMAR and f'\\"{S.INFERENCE}\\"' in GRAMMAR  # the only rule set Virtuoso has


def test_prefixes_are_those_that_get_declared():
    rule = re.search(r"(?ms)^pn_prefix ::= (.*?)\n(?=\S)", GRAMMAR).group(1)
    declared = set(re.findall(r'"([^"]+)"', rule)) | {"sphn"}  # sphn: has its own rule, its terms only
    assert declared == set(SC.PREFIXES) | set(VIRTUOSO_PREFIXES)  # finish() and Virtuoso


@pytest.mark.parametrize("query", [q for _, q in OLD_NAMES], ids=[i for i, _ in OLD_NAMES])
def test_names_sphn_2026_1_does_not_have_cannot_be_written(stops, query):
    assert stops[query] is not None


def test_sphn_names_are_only_the_schema_terms():
    names = re.search(r"(?m)^sphn_term ::= (.*)$", GRAMMAR).group(1).split(" | ")
    assert len(names) == len(SC.TERMS) and '"BilledDiagnosis"' in names and '"hasCode"' in names
