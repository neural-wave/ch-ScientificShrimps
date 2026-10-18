"""query_check without the database or the model: the SPHN 2026.1 -> 2024.2 mapping, the result normalisation and
comparison, the read-only filter, check() with a stub runner, the clean set's secrecy, generate()'s info about the
path that wrote a query; the terms the mock data has and the untestable categories, the reference cache, our query's
time limit, and translating in threads while the queries run one at a time."""
import hashlib, importlib, json, re, threading, time
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
import pytest
from nl2q import schema as SC
from nl2q import settings as S
from nl2q.compliance import validate
from nl2q.compliance.rules import parse
from nl2q.tools import query_check as Q
from nl2q.tools.make_examples import RENAMES
from nl2q.translation import generate
from nl2q.translation import plan as P
from nl2q.translation.examples import load
from nl2q.translation.prompt import finish

MOCKS = [e for e in load() if e.get("sparql_mock")]  # official queries that the bank rewrote for SPHN 2026.1
LEFT = r"sphn:hasRankCode|sphn:hasAdmission\b|sphn:hasDischarge\b|sphn:hasGenomicVariation"  # 2026.1 terms 2024.2 lacks


# ---- SPHN 2026.1 -> 2024.2 -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("e", MOCKS, ids=[e["id"] for e in MOCKS])
def test_renames_are_undone_on_the_official_queries(e):
    renamed = e["sparql_mock"]
    for old, new in RENAMES:
        renamed = renamed.replace(old, new)
    back, done = Q.to_2024(renamed)
    assert back == e["sparql_mock"].replace("sphn-individual:Principal", Q.PRINCIPAL)
    assert bool(done) == (renamed != e["sparql_mock"])


@pytest.mark.parametrize("q", [e["sparql_mock"] for e in MOCKS] + [t["reference"] for t in Q.bench()])
def test_a_2024_query_passes_unchanged(q):
    assert Q.to_2024(q) == (q, [])


FORMS = [  # how our queries write the renamed terms (plan compiler, conventions.md, the generator) -> 2024.2
    ("?dx sphn:hasRankCode/rdf:type snomed:63161005 .", f"?dx sphn:hasRank {Q.PRINCIPAL} .", "rank"),
    ("FILTER EXISTS { ?dx sphn:hasRankCode/rdf:type <http://snomed.info/id/63161005> }",
     f"FILTER EXISTS {{ ?dx sphn:hasRank {Q.PRINCIPAL} }}", "rank"),
    ("?dx sphn:hasRankCode ?dx_rank_code . ?dx_rank_code rdf:type ?dx_rank .\nFILTER (?dx_rank = <http://snomed.info/id/63161005>)",
     f"?dx sphn:hasRank ?dx_rank_code . VALUES (?dx_rank_code ?dx_rank) {{ {Q.RANKS} }} .\n"
     "FILTER (?dx_rank = <http://snomed.info/id/63161005>)", "rank"),
    ("OPTIONAL { ?dx sphn:hasRankCode ?r . ?r rdf:type ?class }",
     f"OPTIONAL {{ ?dx sphn:hasRank ?r . VALUES (?r ?class) {{ {Q.RANKS} }} }}", "rank"),
    ("?dx sphn:hasRankCode ?r . ?r a snomed:63161005 .", f"?dx sphn:hasRank ?r . FILTER (?r = {Q.PRINCIPAL}) .", "rank"),
    ("?case sphn:hasAdmission/sphn:hasDateTime ?in ; sphn:hasDischarge/sphn:hasDateTime ?out .",
     "?case sphn:hasAdmissionDateTime ?in ; sphn:hasDischargeDateTime ?out .", "stay dates"),
    ("?case sphn:hasAdmission ?a . ?a sphn:hasDateTime ?in .", "?case sphn:hasAdmissionDateTime ?in .", "stay dates"),
    ("?v sphn:hasGenomicVariation ?gv .", "?v sphn:hasGeneticVariation ?gv .", "genetic variation"),
]


@pytest.mark.parametrize("q,expected,name", FORMS, ids=[f"{f[2]}-{i}" for i, f in enumerate(FORMS)])
def test_the_forms_our_queries_write_are_mapped(q, expected, name):
    back, done = Q.to_2024(q)
    assert (back, done) == (expected, [name]) and Q.to_2024(back) == (back, [])  # once is enough
    parse("PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>\nPREFIX snomed: <http://snomed.info/id/>\n"
          f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH ?g {{ ?dx a sphn:BilledDiagnosis . ?case a sphn:AdministrativeCase . "
          f"?v a sphn:VariantDescriptor . {back} }} }}")  # still SPARQL (raises Invalid otherwise)


PLANS = {"rank": {"route": "plan", "records": {"dx": {"type": "BilledDiagnosis", "code": ["icd-10-gm:E11"], "where": [
            {"attr": "dx.rank", "op": "=", "value": "main"}]}}, "output": [{"name": "n", "agg": "patients"}]},
         "in_stay": {"route": "plan", "records": {"drug": {"type": "DrugAdministrationEvent", "where": [
            {"attr": "drug.in_stay", "op": "=", "value": True}]}}, "output": [{"name": "n", "agg": "patients"}]}}


@pytest.mark.parametrize("name", PLANS)
def test_compiled_plans_map_to_valid_2024_queries(name):
    assert P.check(PLANS[name]) == []
    back, done = Q.to_2024(finish(P.compile(PLANS[name])))
    parse(back)
    assert done and not re.search(LEFT, back)


def test_val_references_map_to_valid_2024_queries():
    vals = [S.DATA / "training" / f for f in ("val.jsonl", "val_v2.jsonl", "val_v21.jsonl")]
    if not any(f.exists() for f in vals):
        pytest.skip("no nl2q_data/training/val*.jsonl")
    for f in (f for f in vals if f.exists()):
        for line in filter(str.strip, f.read_text().splitlines()):
            gold = json.loads(json.loads(line)["messages"][-1]["content"])
            if gold.get("route") != "plan":
                continue
            try:
                back, _ = Q.to_2024(finish(P.compile(gold)))
            except P.Unsupported:
                continue
            parse(back)
            assert not re.search(LEFT, back), gold


# ---- comparing results ---------------------------------------------------------------------------------------------
INT, DEC, DBL, BOOL = (Q.XSD + t for t in ("integer", "decimal", "double", "boolean"))


def b(v, dt=None, kind="literal"):
    """A JSON binding as Virtuoso returns it."""
    return {"type": kind, "value": v, **({"datatype": dt} if dt else {})}


def test_column_order_and_names_do_not_matter():
    assert Q.compare([(b("http://x/A", kind="uri"), b("3", INT))], [(b("3", INT), b("http://x/A", kind="uri"))]) == ("match", 1.0)


def test_number_formats_compare_by_value():
    assert Q.compare([(b("12", INT),)], [(b("12.0", DEC),)])[0] == "match"
    assert Q.compare([(b("12", INT),)], [(b("1.2E1", DBL),)])[0] == "match"
    assert Q.compare([(b("0.1234567", DBL),)], [(b("0.12345671", DEC),)])[0] == "match"  # 6 significant digits
    assert Q.compare([(b("33.3", DEC),)], [(b("33.4", DEC),)]) == ("mismatch", 0.0)
    assert Q.value(b("-0.0", DBL)) == Q.value(b("0", INT)) == Q.value("0") == "0"
    assert Q.value(b("2024-01-02T00:00:00", Q.XSD + "dateTime")) == "2024-01-02T00:00:00"


def test_iris_and_strings_compare_exactly():
    assert Q.compare([(b("http://x/a", kind="uri"),)], [(b("http://x/A", kind="uri"),)]) == ("mismatch", 0.0)
    assert Q.compare([(b("abc"),)], [(b("abc "),)])[0] == "mismatch"


def test_empty_results_are_inconclusive():
    assert Q.compare([], []) == ("both_empty", None)
    assert Q.compare([(b("0", INT),)], []) == ("both_empty", None)  # a count of nothing
    assert Q.compare([(None, b("0", INT))], [(b("0", DEC),)]) == ("both_empty", None)
    assert Q.compare([], [(b("5", INT),)]) == ("mismatch", 0.0)
    assert Q.compare([(b("0", INT),)], [(b("5", INT),)]) == ("mismatch", 0.0)


def test_rows_are_a_multiset_and_f1_gives_partial_credit():
    a = [(b("A"), b("3", INT)), (b("B"), b("4", INT))]
    assert Q.compare(list(reversed(a)), a) == ("match", 1.0)  # row order does not matter
    assert Q.compare(a, [(b("A"), b("3", INT)), (b("B"), b("5", INT))]) == ("mismatch", 0.75)
    assert Q.compare(a + a[:1], a) == ("mismatch", 1.0)  # a duplicate row: the same values, not the same answer


def test_ask_answers():
    yes, no = [(b("true", BOOL),)], [(b("false", BOOL),)]
    assert Q.compare(yes, yes)[0] == "match" and Q.compare(no, no)[0] == "both_empty" and Q.compare(yes, no)[0] == "mismatch"


# ---- what may run --------------------------------------------------------------------------------------------------
COUNT = "SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?dx a sphn:BilledDiagnosis } }"


@pytest.mark.parametrize("q", [
    COUNT,
    "ASK { GRAPH ?g { ?dx a sphn:BilledDiagnosis } }",
    'DEFINE sql:select-option "order"\nPREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>\n' + COUNT,
    'SELECT ?delete WHERE { ?s <http://x/insert> "drop it; clear" . } # LOAD <http://x/data.ttl>',
    "SELECT ?x WHERE { ?x a sphn:Load ; sphn:hasCopy ?y ; sphn:hasDrug ?with }",
    'PREFIX bif: <bif:>\nSELECT (AVG(bif:datediff("day", ?a, ?b)) AS ?d) WHERE { ?x sphn:hasStartDateTime ?a ; sphn:hasEndDateTime ?b }',
    "SELECT (xsd:integer(?v) AS ?n) WHERE { ?q sphn:hasValue ?v }"])
def test_read_only_queries_run(q):
    assert Q.runnable(q) is None


@pytest.mark.parametrize("q,why", [
    ("CONSTRUCT { ?s ?p ?o } WHERE { GRAPH ?g { ?s ?p ?o } }", "CONSTRUCT, not compared"),
    ("DESCRIBE ?dx WHERE { GRAPH ?g { ?dx a sphn:BilledDiagnosis } }", "DESCRIBE, not compared"),
    ("DELETE WHERE { GRAPH ?g { ?dx rdf:type sphn:BilledDiagnosis . } }", "DELETE, never run"),
    ("INSERT DATA { GRAPH <http://x/g> { <http://x/s> <http://x/p> 1 } }", "INSERT, never run"),
    ("LOAD <http://x/data.ttl> INTO GRAPH <http://x/g>", "LOAD, never run"),
    ("CLEAR GRAPH <http://x/g>", "CLEAR, never run"),
    ("DROP SILENT GRAPH <http://x/g>", "DROP, never run"),
    ("CREATE GRAPH <http://x/g>", "CREATE, never run"),
    ("WITH <http://x/g> DELETE { ?s ?p ?o } WHERE { ?s ?p ?o }", "WITH, never run"),
    ("PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>\n# SELECT\ninsert { ?s ?p ?o } where { ?s ?p ?o }", "INSERT, never run"),
    (COUNT + " ;\nDELETE DATA { <http://x/s> <http://x/p> 1 }", "DELETE, never run"),
    ("SELECT (COUNT(?r) AS ?n) WHERE { SERVICE <https://example.org/sparql> { ?r a sphn:LabResult } }", "SERVICE"),
    ('DEFINE get:soft "replace"\nSELECT * FROM <http://x/remote> WHERE { ?s ?p ?o }', "DEFINE get:soft, never run"),
    ('SELECT (bif:exec("checkpoint") AS ?x) WHERE { }', "bif:exec"),
    ('SELECT (<bif:exec>("checkpoint") AS ?x) WHERE { }', "bif:exec"),
    ('PREFIX b: <bif:>\nSELECT (b:exec("checkpoint") AS ?x) WHERE { }', "bif:exec"),
    ("SELECT (sql:my_procedure() AS ?x) WHERE { }", "sql:my_procedure"),
    ("", "no query form, never run")])
def test_other_queries_never_run(q, why):
    assert (Q.runnable(q) or "").startswith(why)


def test_bench_references_that_do_not_run_are_not_select_or_ask_or_call_another_endpoint_or_read_labels():
    refused = {t["id"]: Q.runnable(Q.to_2024(t["reference"])[0]) for t in Q.bench()}
    assert all(re.match(r"(CONSTRUCT|DESCRIBE|INSERT|DELETE|SERVICE|rdfs:label)\b", w) for w in refused.values() if w)
    assert sum(w is None for w in refused.values()) >= 20


@pytest.mark.parametrize("q", [
    "SELECT ?l WHERE { ?c rdfs:label ?l }",
    "PREFIX r: <http://www.w3.org/2000/01/rdf-schema#>\nSELECT ?l WHERE { ?c r:label ?l }",
    "SELECT ?l WHERE { GRAPH ?g { ?c <http://www.w3.org/2000/01/rdf-schema#label> ?l } }",
    "SELECT ?l WHERE { ?c skos:prefLabel ?l }",
    "PREFIX s: <http://www.w3.org/2004/02/skos/core#>\nSELECT ?l WHERE { ?c s:altLabel ?l }",
    "ASK { ?c rdfs:subClassOf ?d ; rdfs:label|skos:notation ?l }"])
def test_queries_that_read_terminology_labels_never_run(q):
    assert (Q.runnable(q) or "").endswith("(terminology labels), never run")


def test_a_label_in_a_string_or_a_comment_is_no_label_query():
    assert Q.runnable('SELECT ?x WHERE { ?x rdfs:subClassOf ?y . FILTER (STR(?y) != "rdfs:label") } # skos:prefLabel') is None


# ---- check() with a stub runner ------------------------------------------------------------------------------------
OURS, REF = COUNT, "SELECT (COUNT(DISTINCT ?g) AS ?patients) WHERE { GRAPH ?g { ?x a sphn:BilledDiagnosis } }"
FIVE = [(b("5", INT),)]


def runner(answers):
    """run(query, timeout) answering from {query: [(seconds, rows) or an exception, one per run]}; it records the calls."""
    calls = []

    def run(q, timeout):
        calls.append(q)
        x = answers[q].pop(0) if len(answers[q]) > 1 else answers[q][0]
        if isinstance(x, Exception):
            raise x
        return x
    run.calls = calls
    return run


def test_match_with_median_times_and_the_slow_flag():
    run = runner({OURS: [(0.5, FIVE), (0.3, FIVE), (0.4, FIVE)], REF: [(0.1, FIVE), (0.2, FIVE), (0.1, FIVE)]})
    r = Q.check(OURS, REF, runs=3, run=run)
    assert (r["category"], r["f1"], r["seconds_ours"], r["seconds_ref"], r["rows_ours"]) == ("match", 1.0, 0.4, 0.1, 1)
    assert r["ratio"] == pytest.approx(4) and r["slow"] and run.calls == [REF, OURS] * 3


def test_a_fast_enough_query_is_not_flagged():
    r = Q.check(OURS, REF, runs=1, run=runner({OURS: [(0.2, FIVE)], REF: [(0.1, FIVE)]}))
    assert r["ratio"] == pytest.approx(2) and not r["slow"]
    r = Q.check(OURS, REF, runs=1, run=runner({OURS: [(11.0, FIVE)], REF: [(10.0, FIVE)]}))
    assert r["slow"]  # over 10 s


def test_failures_and_timeouts_are_categories_and_not_run_again():
    run = runner({OURS: [Q.TooSlow()], REF: [(0.1, FIVE)]})
    r = Q.check(OURS, REF, runs=3, run=run)
    assert r["category"] == "ours_timeout" and run.calls.count(OURS) == 1 and run.calls.count(REF) == 3
    assert r["seconds_ours"] is None and r["ratio"] is None and not r["slow"]
    r = Q.check(OURS, REF, runs=2, run=runner({OURS: [RuntimeError("HTTP 400: syntax error")], REF: [(0.1, FIVE)]}))
    assert r["category"] == "ours_error" and r["error_ours"] == "HTTP 400: syntax error"
    r = Q.check(OURS, REF, runs=2, run=runner({OURS: [RuntimeError("HTTP 500")], REF: [Q.TooSlow()]}))
    assert r["category"] == "ref_timeout" and r["status_ours"] == "error"  # nothing to compare with: the reference's fault
    assert Q.check(OURS, REF, runs=1, run=runner({OURS: [(0.1, FIVE)], REF: [RuntimeError("x")]}))["category"] == "ref_error"


def test_what_does_not_run_is_not_compared():
    run = runner({})
    for ours, ref in [(OURS, "DELETE WHERE { GRAPH ?g { ?s ?p ?o } }"), ("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }", REF),
                      ("DESCRIBE ?dx WHERE { ?dx a sphn:BilledDiagnosis }", REF)]:
        assert Q.check(ours, ref, run=run)["category"] == "not_compared"
    r = Q.check("", REF, run=run)
    assert (r["category"], r["why"]) == ("ours_error", "no query written") and run.calls == []


def test_both_sides_run_mapped_to_2024():
    ours = "SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?v sphn:hasGenomicVariation ?x } }"
    mapped = ours.replace("hasGenomicVariation", "hasGeneticVariation")
    run = runner({mapped: [(0.1, FIVE)], REF: [(0.1, FIVE)]})
    r = Q.check(ours, REF, runs=1, run=run)
    assert run.calls == [REF, mapped] and r["mapped_ours"] == ["genetic variation"] and r["mapped_ref"] == []


# ---- the clean set: ids and numbers only ---------------------------------------------------------------------------
@pytest.mark.parametrize("ours", [finish(COUNT), "SELECT (COUNT(?x) AS ?n) WHERE { ?x a sphn:SecretType }"])
def test_a_clean_item_keeps_no_text(monkeypatch, ours):
    translation = importlib.import_module("nl2q.translation")
    monkeypatch.setattr(translation, "generate", lambda text, check, usage=None, info=None: (ours, []))
    monkeypatch.setattr(Q, "check", lambda ours, ref, runs, *a, **k: {"category": "mismatch", "f1": 0.5, "why": "x",
                                                                      "error_ours": "y", "absent_ours": ["sphn:Secret"]})
    t = {"id": "C01", "question": "a secret request", "reference": REF}
    r = Q.one(t, 1, secret=True)
    assert set(r) <= set(Q.PUBLIC) and r["id"] == "C01" and r["category"] == "mismatch"
    assert "Secret" not in Q.line(r) and "secret" not in Q.line(r)
    assert {"question", "sparql_ours", "invalid"} <= set(Q.one(t, 1, secret=False))


# ---- valid: parses, and names only SPHN 2026.1 terms ---------------------------------------------------------------
def test_a_query_with_2026_terms_is_valid():
    assert Q.valid(finish(COUNT)) == (True, None)
    assert Q.valid(finish(P.compile(PLANS["rank"]))) == (True, None)


@pytest.mark.parametrize("q,why", [
    ("", "no query"),
    ("SELECT (COUNT(* AS ?n) WHERE { ?s a ?t }", "SPARQL syntax error"),
    (finish("SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?dx a sphn:BilledDiagnosis ; sphn:hasRank ?r } }"),
     "not in the SPHN 2026.1 schema: sphn:hasRank"),  # a 2024.2 name
    (finish("SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?dx a ?t } FILTER (?t = sphn:NoSuchType) }"),
     "not in the SPHN 2026.1 schema: sphn:NoSuchType"),  # outside a triple
    (finish("ASK { GRAPH ?g { ?dx a sphn:NoSuchType } }"), "not in the SPHN 2026.1 schema: sphn:NoSuchType"),
    ("ASK { ?dx a <https://biomedit.ch/rdf/sphn-schema/sphn#NoSuchType> }", "not in the SPHN 2026.1 schema: sphn:NoSuchType")])
def test_invalid_queries_say_why(q, why):
    ok, reason = Q.valid(q)
    assert not ok and reason.startswith(why)


# ---- generate() says which path wrote the query --------------------------------------------------------------------
def test_generate_tells_which_path_wrote_the_query(monkeypatch):
    G = importlib.import_module("nl2q.translation.generate")
    monkeypatch.setattr(S, "PLAN", True)
    monkeypatch.setattr(P, "query", lambda text, usage, llm_fn=None: (finish(COUNT), None))
    info = {}
    assert generate("q", validate, info=info) == (finish(COUNT), []) and info == {"path": "plan"}
    monkeypatch.setattr(P, "query", lambda text, usage, llm_fn=None: (None, "plan: free"))
    monkeypatch.setattr(G, "llm", lambda messages, usage, **kw: f"```sparql\n{COUNT}\n```")
    info = {}
    assert generate("q", validate, info=info)[0] == finish(COUNT) and info == {"path": "free", "fallback": "plan: free"}
    info = {}
    generate("q", validate, lambda messages, usage: f"```sparql\n{COUNT}\n```", None, info)  # a stub generator: free
    assert info == {"path": "free"}
    assert generate("q", validate, lambda messages, usage: f"```sparql\n{COUNT}\n```")[0] == finish(COUNT)  # no info: as before


# ---- the sphn: terms the mock data has, and the untestable categories ----------------------------------------------
NS = SC.NS


def asks(present):
    """run(query, timeout) for data_terms: true for the terms in `present` (local names); it records (query, timeout)."""
    def run(q, timeout):
        run.calls.append((q, timeout))
        return 0.01, [(b("true" if any(f"#{t}>" in q for t in present) else "false", BOOL),)]
    run.calls = []
    return run


def test_the_terms_asked_are_the_schemas_and_the_2024_names_to_2024_writes():
    asked = Q.asked_terms()
    assert set(SC.TERMS) < set(asked) and len(asked) == len(set(asked))
    assert {n for old, _ in RENAMES for n in re.findall(r"sphn:(\w+)", old)} == set(Q.NAMES_2024)
    assert {NS + n for _, expected, _ in FORMS for n in re.findall(r"sphn:(\w+)", expected)} <= set(asked)


def test_one_tiny_ask_per_term_a_class_by_an_instance_a_property_by_a_triple(tmp_path):
    run = asks({"BilledDiagnosis", "hasCode"})
    terms = [NS + t for t in ("hasRankCode", "BilledDiagnosis", "hasCode", "DrugPrescriptionEvent")]
    t = Q.data_terms(run=run, path=tmp_path / "terms.json", terms=terms)
    assert run.calls == [(f"ASK {{ ?s a <{NS}BilledDiagnosis> }}", Q.ASK_TIMEOUT),
                         (f"ASK {{ ?s a <{NS}DrugPrescriptionEvent> }}", Q.ASK_TIMEOUT),
                         (f"ASK {{ ?s <{NS}hasCode> ?o }}", Q.ASK_TIMEOUT),
                         (f"ASK {{ ?s <{NS}hasRankCode> ?o }}", Q.ASK_TIMEOUT)]
    assert (t["present"], t["absent"], t["failed"]) == (["BilledDiagnosis", "hasCode"], ["DrugPrescriptionEvent", "hasRankCode"], [])
    assert (t["endpoint"], t["ns"]) == (S.SPARQL_ENDPOINT, NS) and t["date"]
    assert json.loads((tmp_path / "terms.json").read_text()) == t


def test_the_terms_are_asked_once_and_kept(tmp_path, monkeypatch):
    path, terms = tmp_path / "terms.json", [NS + "BilledDiagnosis", NS + "hasRankCode"]
    first = Q.data_terms(run=asks({"BilledDiagnosis"}), path=path, terms=terms)
    never = lambda q, timeout: pytest.fail("asked again")
    assert Q.data_terms(run=never, path=path, terms=terms) == first == Q.data_terms(run=never, path=path, terms=terms[:1])
    again = asks({"BilledDiagnosis", "hasRankCode"})
    assert Q.data_terms(refresh=True, run=again, path=path, terms=terms)["absent"] == [] and len(again.calls) == 2
    more = asks(set())
    Q.data_terms(run=more, path=path, terms=terms + [NS + "hasCode"])
    assert len(more.calls) == 3  # a term the file lacks: all asked again
    monkeypatch.setattr(S, "SPARQL_ENDPOINT", "http://elsewhere:8890/sparql")
    other = asks(set())
    assert Q.data_terms(run=other, path=path, terms=terms)["endpoint"] == "http://elsewhere:8890/sparql" and len(other.calls) == 2


def test_a_term_whose_ask_failed_is_not_absent_and_is_asked_again(tmp_path):
    path, terms = tmp_path / "terms.json", [NS + "BilledDiagnosis", NS + "hasRankCode"]

    def flaky(q, timeout):
        if "hasRankCode" in q:
            raise Q.TooSlow()
        return 0.01, [(b("false", BOOL),)]
    t = Q.data_terms(run=flaky, path=path, terms=terms)
    assert (t["absent"], t["failed"]) == (["BilledDiagnosis"], ["hasRankCode"])
    run = asks(set())
    Q.data_terms(run=run, path=path, terms=terms)
    assert len(run.calls) == 2


GONE = "SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?e a sphn:DrugPrescriptionEvent } }"
ABSENT = frozenset({NS + "DrugPrescriptionEvent", NS + "hasRankCode"})
ZERO = [(b("0", INT),)]
DIV0 = RuntimeError("HTTP 500: Virtuoso 22012 Error SR083: Division by zero")
SYNTAX = RuntimeError("HTTP 400: Virtuoso 37000 Error SP030: SPARQL compiler, line 3: syntax error")


@pytest.mark.parametrize("answer,category", [
    ([(0.1, ZERO)], "untestable"),   # nothing found: the data lacks the type
    ([DIV0], "untestable"),          # a share of nothing
    ([(0.1, FIVE)], "match"),        # it matches anyway
    ([SYNTAX], "ours_error"),        # Virtuoso cannot compile it, whatever the data
    ([Q.TooSlow()], "ours_timeout")])
def test_our_query_naming_a_term_the_data_lacks_is_untestable_when_it_does_not_match(answer, category):
    r = Q.check(GONE, REF, runs=1, run=runner({GONE: list(answer), REF: [(0.1, FIVE)]}), absent=ABSENT)
    assert r["category"] == category
    if category == "untestable":
        assert r["absent_ours"] == ["sphn:DrugPrescriptionEvent"] and "sphn:DrugPrescriptionEvent" in r["why"]


def test_untestable_needs_the_terms_and_covers_both_empty():
    assert Q.check(GONE, REF, runs=1, run=runner({GONE: [(0.1, ZERO)], REF: [(0.1, FIVE)]}))["category"] == "mismatch"
    assert Q.check(GONE, REF, runs=1, run=runner({GONE: [(0.1, [])], REF: [(0.1, ZERO)]}), absent=ABSENT)["category"] == "untestable"


def test_a_reference_naming_a_term_the_data_lacks_is_ref_untestable():
    ref = GONE + " "  # another text than ours
    for ours, answer in ((OURS, [(0.1, FIVE)]), (GONE, [(0.1, ZERO)]), (OURS, [DIV0])):
        r = Q.check(ours, ref, runs=1, run=runner({ours: answer, ref: [(0.1, ZERO)]}), absent=ABSENT)
        assert r["category"] == "ref_untestable" and r["absent_ref"] == ["sphn:DrugPrescriptionEvent"], ours
    assert Q.check(OURS, ref, runs=1, run=runner({OURS: [(0.1, FIVE)], ref: [DIV0]}), absent=ABSENT)["category"] == "ref_untestable"
    r = Q.check(GONE, REF, runs=1, run=runner({GONE: [(0.1, FIVE)], REF: [DIV0]}), absent=ABSENT)
    assert r["category"] == "ref_error"  # the reference failed on its own: nothing to compare with
    assert Q.check(GONE, ref, runs=1, run=runner({GONE: [(0.1, FIVE)], ref: [(0.1, FIVE)]}), absent=ABSENT)["category"] == "match"


def test_a_term_mapped_to_2024_is_not_absent():
    ours = "SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?dx sphn:hasRankCode/rdf:type snomed:63161005 } }"
    r = Q.check(ours, REF, runs=1, run=runner({Q.to_2024(ours)[0]: [(0.1, ZERO)], REF: [(0.1, FIVE)]}), absent=ABSENT)
    assert r["category"] == "mismatch" and "absent_ours" not in r


def test_the_summary_counts_the_untestable_items():
    rows = [{"id": "A", "path": "free", "category": "untestable", "absent_ours": ["sphn:X"], "valid": True,
             "status_ours": "ok", "seconds_ours": 0.5, "ref_cached": True},
            {"id": "B", "path": "plan", "category": "ref_untestable", "absent_ref": ["sphn:Y"], "valid": True, "ref_cached": False},
            {"id": "C", "path": "plan", "category": "match", "f1": 1.0, "valid": True, "status_ours": "ok", "seconds_ours": 0.2,
             "seconds_ref": 0.1, "ratio": 2.0, "ref_cached": True},
            {"id": "D", "path": "plan", "category": "ours_timeout", "valid": True, "status_ours": "timeout", "timeout_ours": 30}]
    text = Q.summary(rows, Q.options(["--set", "bench", "--runs", "1", "--timeout", "30"]),
                     {"absent": ["X", "Y"], "present": ["Z"], "date": "2026-10-17"})
    assert re.search(r"(?<!ref_)untestable 1\b", text) and "ref_untestable 1" in text
    assert "A (sphn:X)" in text and "B (sphn:Y)" in text and "D >30s" in text and "terms not in the mock data 2/3" in text
    assert "median seconds ours 0.350 over the 2" in text and "references kept from an earlier run 2/3" in text
    assert "timeout=30s reference_timeout=120s" in text
    secret = [{k: r[k] for k in Q.PUBLIC if k in r} for r in rows]
    assert "sphn:X" not in Q.summary(secret, Q.options(["--set", "clean"]))


# ---- the reference cache -------------------------------------------------------------------------------------------
def test_a_reference_runs_once_and_is_kept_for_later_runs(tmp_path):
    path, other = tmp_path / "references.json", OURS.replace("?n", "?m")
    run = runner({OURS: [(0.2, FIVE)], other: [(0.3, FIVE)], REF: [(0.1, FIVE)]})
    refs = Q.References(path)
    r1, r2 = Q.check(OURS, REF, runs=1, run=run, refs=refs), Q.check(other, REF, runs=1, run=run, refs=refs)
    assert run.calls == [REF, OURS, other] and (r1["ref_cached"], r2["ref_cached"]) == (False, True)
    assert (r2["category"], r2["seconds_ref"], r2["rows_ref"]) == ("match", 0.1, 1)
    later = runner({OURS: [(0.2, FIVE)]})  # the next run of the tool, another version: from the file
    r3 = Q.check(OURS, REF, runs=1, run=later, refs=Q.References(path))
    assert later.calls == [OURS] and r3["category"] == "match" and r3["ref_cached"]
    (key, kept), = json.loads(path.read_text()).items()
    assert key == hashlib.sha256(f"{S.SPARQL_ENDPOINT}\n{REF}".encode()).hexdigest()
    assert (kept["status"], kept["seconds"], kept["endpoint"]) == ("ok", 0.1, S.SPARQL_ENDPOINT) and kept["date"]
    assert "COUNT" not in path.read_text()  # the reference's text is never kept


def test_the_mapped_reference_and_the_endpoint_make_the_key(tmp_path, monkeypatch):
    ref26 = "SELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?v sphn:hasGenomicVariation ?x } }"
    ref24 = Q.to_2024(ref26)[0]
    run = runner({OURS: [(0.2, FIVE)], ref24: [(0.1, FIVE)]})
    refs = Q.References(tmp_path / "references.json")
    Q.check(OURS, ref26, runs=1, run=run, refs=refs)
    assert run.calls == [ref24, OURS] and Q.References.key(ref24) in json.loads((tmp_path / "references.json").read_text())
    monkeypatch.setattr(S, "SPARQL_ENDPOINT", "http://elsewhere:8890/sparql")
    Q.check(OURS, ref26, runs=1, run=run, refs=refs)
    assert run.calls.count(ref24) == 2  # another endpoint: run there


def test_refresh_runs_each_reference_again_once(tmp_path):
    path = tmp_path / "references.json"
    Q.check(OURS, REF, runs=1, run=runner({OURS: [(0.2, FIVE)], REF: [(0.1, FIVE)]}), refs=Q.References(path))
    run, refs = runner({OURS: [(0.2, FIVE)], REF: [(0.4, FIVE)]}), Q.References(path, refresh=True)
    Q.check(OURS, REF, runs=1, run=run, refs=refs)
    r = Q.check(OURS, REF, runs=1, run=run, refs=refs)
    assert run.calls.count(REF) == 1 and r["seconds_ref"] == 0.4 and r["ref_cached"]
    assert json.loads(path.read_text())[Q.References.key(REF)]["seconds"] == 0.4


def test_kept_failures_say_no_more_than_the_http_status_for_the_clean_set(tmp_path):
    path = tmp_path / "references.json"
    err = RuntimeError("HTTP 500: Virtuoso 22012 Error SR083: Division by zero SPARQL query: SELECT ?secret")
    r = Q.check(OURS, REF, runs=1, run=runner({OURS: [(0.1, FIVE)], REF: [err]}), refs=Q.References(path, secret=True))
    assert r["category"] == "ref_error" and "secret" not in path.read_text()
    assert json.loads(path.read_text())[Q.References.key(REF)] | {"date": "-"} == {
        "status": "error", "seconds": None, "rows": None, "endpoint": S.SPARQL_ENDPOINT, "date": "-", "error": "HTTP 500"}
    r = Q.check(OURS, REF, runs=1, run=runner({OURS: [(0.1, FIVE)]}), refs=Q.References(path))
    assert r["category"] == "ref_error" and r["ref_cached"]  # kept: Virtuoso's own answer


def test_a_timeout_is_kept_but_a_server_out_of_reach_is_not(tmp_path):
    path = tmp_path / "references.json"
    r = Q.check(OURS, REF, runs=1, run=runner({OURS: [(0.1, FIVE)], REF: [ConnectionRefusedError("refused")]}),
                refs=Q.References(path))
    assert r["category"] == "ref_error" and not path.exists()
    r = Q.check(OURS, REF, runs=1, run=runner({OURS: [(0.1, FIVE)], REF: [Q.TooSlow()]}), refs=Q.References(path))
    assert r["category"] == "ref_timeout" and json.loads(path.read_text())[Q.References.key(REF)]["status"] == "timeout"


# ---- our query's time limit ----------------------------------------------------------------------------------------
def test_our_query_gets_the_timeout_and_the_reference_120_s(tmp_path):
    seen = []

    def run(q, timeout):
        seen.append((q, timeout))
        return 0.1, FIVE
    Q.check(OURS, REF, runs=2, timeout=30, run=run, refs=Q.References(tmp_path / "references.json"))
    assert seen == [(REF, 120), (OURS, 30), (OURS, 30)]
    seen.clear()
    Q.check(OURS, REF, runs=1, timeout=30, run=run)  # no cache: the reference next to ours, still 120 s
    assert seen == [(REF, 120), (OURS, 30)]
    r = Q.check(OURS, REF, runs=1, timeout=30, run=runner({OURS: [Q.TooSlow()], REF: [(0.1, FIVE)]}))
    assert r["category"] == "ours_timeout" and "time >30s/0.100s" in Q.line({**r, "id": "X", "path": "plan"})


def test_the_options_default_to_the_full_run():
    a = Q.options(["--set", "bench"])
    assert (a.runs, a.timeout, a.workers, a.refresh_terms, a.refresh_refs) == (3, 120, 4, False, False)
    a = Q.options(["--set", "clean", "--runs", "1", "--timeout", "30", "--workers", "2", "--refresh-terms", "--refresh-refs"])
    assert (a.runs, a.timeout, a.workers, a.refresh_terms, a.refresh_refs) == (1, 30, 2, True, True)


# ---- translated in threads, checked one at a time ------------------------------------------------------------------
def test_requests_are_translated_in_parallel_before_any_query_runs(monkeypatch):
    translation = importlib.import_module("nl2q.translation")
    together, events, lock = threading.Barrier(4, timeout=10), [], threading.Lock()

    def generate(text, check, usage=None, info=None):
        together.wait()  # four requests at once, or BrokenBarrierError (the translation fails)
        with lock:
            events.append("translated")
        info["path"] = "plan"
        return COUNT, []

    def check(ours, ref, runs, timeout, refs=None, absent=frozenset()):
        assert threading.current_thread() is threading.main_thread()  # one at a time, in the item order
        events.append(ref)
        return {"category": "match", "f1": 1.0}
    monkeypatch.setattr(translation, "generate", generate)
    monkeypatch.setattr(Q, "check", check)
    items = [{"id": f"T{i}", "question": f"q{i}", "reference": f"ref {i}"} for i in range(8)]
    rows = Q.benchmark(items, Q.options(["--set", "bench", "--workers", "4"]))
    assert events == ["translated"] * 8 + [f"ref {i}" for i in range(8)]
    assert [(r["id"], r["path"], r["category"]) for r in rows] == [(f"T{i}", "plan", "match") for i in range(8)]


def test_a_run_whose_translations_all_failed_stops_before_the_database(monkeypatch):
    translation = importlib.import_module("nl2q.translation")

    def down(text, check, usage=None, info=None):
        raise ConnectionError("model server down")
    monkeypatch.setattr(translation, "generate", down)
    monkeypatch.setattr(Q, "check", lambda *a, **k: pytest.fail("a query ran"))
    with pytest.raises(SystemExit):
        Q.benchmark([{"id": "T1", "question": "q", "reference": REF}], Q.options(["--set", "bench"]))


def test_the_translators_database_lookups_wait_for_each_other_and_carry_the_server_timeout(monkeypatch):
    store = importlib.import_module("nl2q.store")
    original, lock, now, peak, sent = store.run, threading.Lock(), [0], [0], []

    class Reply:
        status_code, headers = 200, {}

        def raise_for_status(self):
            pass

        def json(self):
            return {"head": {"vars": ["c"]}, "results": {"bindings": [{"c": {"type": "uri", "value": "http://x/c"}}]}}

    def post(url, data, headers, timeout):
        with lock:
            now[0] += 1
            peak[0] = max(peak[0], now[0])
            sent.append((url, data["timeout"], timeout))
        time.sleep(0.02)
        with lock:
            now[0] -= 1
        return Reply()
    monkeypatch.setattr(Q._http, "post", post)
    with Q.one_query_at_a_time(), ThreadPoolExecutor(4) as pool:
        out = list(pool.map(lambda i: store.run(f"SELECT ?c WHERE {{ ?c ?p {i} }}", timeout=10), range(8)))
        missing = P.unknown_codes({"records": {"x": {"type": "LabTestEvent", "code": ["loinc:1234-5"]}}})  # the plan path's
    assert peak[0] == 1 and out == [(["c"], [("http://x/c",)])] * 8 and missing == ["https://loinc.org/rdf/1234-5"]
    assert sent == [(S.SPARQL_ENDPOINT, 10000, 10)] * 9 and store.run is original


def test_easy_set_holds_29_valid_references():
    items = Q.easy()
    assert [t["id"] for t in items] == [f"E{i:02d}" for i in range(1, 31) if i != 29]  # E29: no valid reference
    assert all(t["question"] and Q.valid(t["reference"]) == (True, None) for t in items)  # SPHN 2026.1 names only


def test_fresh_set_holds_30_valid_references():
    items = Q.bench(Q.FRESH)
    assert [t["id"] for t in items] == [f"F{i:02d}" for i in range(1, 31)]
    assert all(t["question"] and Q.valid(t["reference"]) == (True, None) for t in items)  # SPHN 2026.1 names only
