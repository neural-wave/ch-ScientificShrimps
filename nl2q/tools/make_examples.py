"""Write the worked examples (nl2q/config/examples.md) from official material only, with plain code: no example is
written by a model.
  python -m nl2q.tools.make_examples      # then: python -m nl2q.tools.check_examples
Sources (docs/official/):
- the organizers' examples: the brief's worked example (API_specification.md) and queries-example.md (org-*);
- the SPHN team's feasibility and distribution queries, sphn-sparql-optimization/ (sphn-*, see its SOURCE.md);
- the organizers' Virtuoso kit queries, kit-queries/ (kit-*).
Every change is mechanical and listed in its example ("Changes:"): SPHN 2024.2 names -> 2026.1, the SPHN team's internal
shortcut properties -> 2026.1 paths, one bug fix. A query that comes without a question gets a fixed template with the
codes it uses (never labels from restricted terminologies). Each safe template query also gets three unsafe variants,
made by rewriting the query: one patient (R1), not a SELECT (R4), identifiers exposed (R5)."""
import re, textwrap
from .. import settings as S

DOCS = S.HERE.parent / "docs" / "official"
OUT = S.HERE / "config" / "examples.md"
ONE_PATIENT = "http://example.org/graph/gen_mock_data-1_1000001"

# --- the organizers' examples -------------------------------------------------------------------------------------
# SPHN 2024.2 -> 2026.1, applied in this order. snomed:63161005 is the principal rank: the one rank code 2026.1 allows on
# both BilledDiagnosis and BilledProcedure, as Principal was the one rank 2024.2 gave both.
RENAMES = [("sphn:hasRank sphn-individual:Principal", "sphn:hasRankCode/rdf:type snomed:63161005"),
           ("sphn:hasRank ", "sphn:hasRankCode "),
           ("sphn:hasAdmissionDateTime", "sphn:hasAdmission/sphn:hasDateTime"),
           ("sphn:hasDischargeDateTime", "sphn:hasDischarge/sphn:hasDateTime"),
           ("sphn:hasGeneticVariation", "sphn:hasGenomicVariation")]
# Structural change: in 2026.1 a prescription reaches its stay through the event that issued it.
EDITS = {5: [(r"\?prescription a sphn:DrugPrescription ;\n(\s+)sphn:hasAdministrativeCase \?case ;\n",
              "?prescription_event sphn:hasDrugPrescription ?prescription ;\n                            sphn:hasAdministrativeCase ?case .\n"
              "        ?prescription a sphn:DrugPrescription ;\n",
              "DrugPrescription hasAdministrativeCase -> DrugPrescriptionEvent hasDrugPrescription, hasAdministrativeCase")]}
# Questions that did not say what their reference query does: both queries keep the principal diagnosis only.
QUESTIONS = {3: ("(any diagnosis rank)", "as the main diagnosis"), 12: ("each cholera case", "each case of cholera as main diagnosis")}
NOTES = {9: "to review: the query splits events by seriousness category instead of keeping the serious ones only"}
# n: (topic, kind of question, rule broken (None = compliant), reason)
ORG = {0: ("billed diagnosis", "count", None, None),
       1: ("oncology diagnosis, surgery, radiotherapy", "pathway, duration", None, None),
       2: ("drug administration, billed diagnosis, stay", "by group, duration", None, None),
       3: ("billed diagnosis, administrative sex", "by group", None, None),
       4: ("birth, nationality, country", "count", None, None),
       5: ("drug prescription, drug administration", "by group", None, None),
       6: ("billed procedure", "by group, duration", None, None),
       7: ("drug administration, stay", "by group, duration", None, None),
       8: ("genetic variant, oncology diagnosis", "by group", None, None),
       9: ("adverse event", "by group", None, None),
       10: ("birth, gestational age", "by group", None, None),
       11: ("lab test", "count", "R1", "a single identifiable patient: the query reads one patient's graph"),
       12: ("billed diagnosis, age, insurance", "rows", "R2", "not aggregated: one row per diagnosis, not totals across patients"),
       13: ("everything", "count", "R3", "matches the whole graph without restriction (?s ?p ?o)"),
       14: ("oncology diagnosis", "graph export", "R4", "not a SELECT query (CONSTRUCT)"),
       15: ("billed diagnosis", "count", "R5", "exposes identifiers: returns the patients' pseudo identifiers")}
# The team's literal-translation example, shown with every request since the first version (not from docs/official).
TEAM = {"id": "ex-00", "topic": "billed diagnosis", "kind": "identifiers", "rule": "R5", "always": True,
        "reason": "exposes identifiers: returns the patients' pseudo identifiers", "source": "team: first fixed few-shot example",
        "question": "List the pseudo identifiers of the patients with a billed diagnosis of multiple sclerosis.",
        "sparql": """SELECT DISTINCT ?pseudo_id
WHERE {
  GRAPH ?g {
    ?diagnosis0_code rdf:type icd-10-gm:G35 OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global") .
    ?diagnosis sphn:hasCode ?diagnosis0_code .
    ?diagnosis rdf:type sphn:BilledDiagnosis .
    ?subject rdf:type sphn:SubjectPseudoIdentifier ;
             sphn:hasIdentifier ?pseudo_id .
  }
}"""}

# --- the SPHN team's queries and the kit's: no question, so a template with the codes they use --------------------
E11, HB, D = "a billed diagnosis with ICD-10-GM code E11", "a lab test with LOINC code 718-7", "(\\s*)"
# The SPHN team's queries use internal shortcut properties (einstein:) that SPHN 2026.1 does not define: the paths.
LAB_VALUE = [(rf"{D}\?lab_res einstein:LabResult_codereplacement1 \?labUnitCode\s*\.",
              r"\1?lab_res sphn:hasQuantity ?lab_quantity .\1?lab_quantity sphn:hasUnit/sphn:hasCode ?labUnitCode .",
              "einstein:LabResult_codereplacement1 -> sphn:hasQuantity, sphn:hasUnit/sphn:hasCode"),
             (rf"{D}\?lab_res einstein:LabResult_valuereplacement1 \?lab_test_i1_filter\s*\.",
              r"\1?lab_quantity sphn:hasValue ?lab_test_i1_filter .",
              "einstein:LabResult_valuereplacement1 -> sphn:hasQuantity/sphn:hasValue")]
DRUG = [(rf"{D}\?drug_administration_event einstein:DrugAdministrationEvent_codereplacement1 \?drug_administration_event0_code\s*\.",
         r"\1?drug_administration_event sphn:hasDrug/sphn:hasActiveIngredient/sphn:hasCode ?drug_administration_event0_code ."
         r"\1?drug_administration_event rdf:type sphn:DrugAdministrationEvent .",
         "einstein:DrugAdministrationEvent_codereplacement1 -> sphn:hasDrug/sphn:hasActiveIngredient/sphn:hasCode, "
         "typed sphn:DrugAdministrationEvent as the shortcut's name says")]
# EXCLUDING.rq keeps the patients who HAVE the diagnosis (IF(alias, true, false)): the same count as AND.rq.
EXCLUDING_FIX = [(r'BIND\(IF\(\?diagnosis_alias, "true"\^\^xsd:boolean, "false"\^\^xsd:boolean\) AS \?diagnosis_filter_absence\)',
                  'BIND(IF(BOUND(?diagnosis_alias) && ?diagnosis_alias, "false"^^xsd:boolean, "true"^^xsd:boolean) AS ?diagnosis_filter_absence)',
                  "bug fix: the official file keeps the patients WITH the diagnosis (the same count as AND.rq); "
                  "now without it, as the file's name says")]
# file -> (id, topic, kind, condition (template "How many patients <condition>?") or question, rule, reason, edits)
TEMPLATED = {
    "sphn-sparql-optimization/Feasibility_Queries/All.rq": ("sphn-all", "all patients", "count", None,
                                                            "How many patients are there in total?", None, None, []),
    "sphn-sparql-optimization/Feasibility_Queries/AND.rq": ("sphn-and", "billed diagnosis, lab test", "count, both conditions",
                                                            f"have both {E11} and {HB}", None, None, None, []),
    "sphn-sparql-optimization/Feasibility_Queries/OR.rq": ("sphn-or", "billed diagnosis, lab test", "count, either condition",
                                                           f"have {E11} or {HB}", None, None, None, []),
    "sphn-sparql-optimization/Feasibility_Queries/WITHOUT.rq": ("sphn-without", "billed diagnosis", "count, absence",
                                                                f"do not have {E11}", None, None, None, []),
    "sphn-sparql-optimization/Feasibility_Queries/EXCLUDING.rq": ("sphn-excluding", "lab test, billed diagnosis", "count, exclusion",
                                                                  f"have {HB} but not {E11}", None, None, None, EXCLUDING_FIX),
    "sphn-sparql-optimization/Feasibility_Queries/BEFORE.rq": ("sphn-before", "billed diagnosis, lab test", "count, time order",
                                                               f"have {E11} recorded more than 7 days before {HB}", None, None, None, []),
    "sphn-sparql-optimization/Feasibility_Queries/Lab Value Restriction.rq": (
        "sphn-lab-value", "lab result", "count, value threshold", "have a lab result above 15 g/L for LOINC code 718-7",
        None, None, None, LAB_VALUE),
    "sphn-sparql-optimization/Feasibility_Queries/Medication (SNOMED CT).rq": (
        "sphn-drug-snomed", "drug administration", "count", "were given a drug with SNOMED CT code 771351007", None, None, None, DRUG),
    "sphn-sparql-optimization/Example_queries/StepD.rq": ("sphn-female-diagnosis", "billed diagnosis, administrative sex",
                                                          "count, both conditions", f"are female and have {E11}", None, None, None, []),
    "sphn-sparql-optimization/Distribution_Queries/BMI.rq": (
        "sphn-bmi-per-patient", "body mass index, administrative sex", "rows per patient", None,
        "For every female patient, list her pseudo identifier with the value and unit of her latest body mass index.",
        "R5", "exposes identifiers: one row per patient, with the patient's pseudo identifier", []),
    "kit-queries/with-inference.rq": ("kit-lab-tests", "lab test", "count", "have at least one lab test", None, None, None, []),
    "kit-queries/count-triples.rq": ("kit-count-triples", "everything", "count", None,
                                     "How many triples are stored across all the patient graphs?",
                                     "R3", "matches the whole graph without restriction (?s ?p ?o)", []),
    "kit-queries/count-per-graph.rq": ("kit-triples-per-patient", "everything", "rows per patient", None,
                                       "Which ten patient graphs hold the most triples, and how many does each hold?",
                                       "R5", "exposes identifiers: one row per patient graph", []),
}
# Not used: Diagnosis.rq is the brief's worked example (org-00); Medication (ATC).rq needs the ATC terminology, which
# the kit does not load (and the data never uses); StepA-C and Starting.rq are StepD before its
# optimisation; the ++ files and Lab Result.rq compute age with einstein:hasCalculatedBirthDate, which 2026.1 has no
# path for; kit-queries/classes.rq counts every typed node, and whether that is a whole-graph match is not clear-cut.


def body(q):
    """The query without DEFINE, PREFIX and comment lines: pipeline's finish adds DEFINE and PREFIX back."""
    return re.sub(r"(?m)^\s*(?:DEFINE|PREFIX|#)[^\n]*\n?", "", q).strip()


def edit(sparql, edits):
    changes = []
    for old, new, why in edits:
        sparql, n = re.subn(old, new, sparql)
        assert n >= 1, ("edit did not apply", old)
        changes.append(why)
    return sparql, changes


def organizers():
    spec = (DOCS / "API_specification.md").read_text()
    found = [(0, "docs/official/API_specification.md (worked example of the brief)", re.search(r'example: "(.*?)"', spec).group(1),
              textwrap.dedent(re.search(r"sparql_query: \|\n(.*?)\n\s*tokens_consumed", spec, re.S).group(1)))]
    parts = re.split(r"(?m)^(\d+)\.\s+(.+)$", (DOCS / "queries-example.md").read_text())
    for n, title, text in zip(parts[1::3], parts[2::3], parts[3::3]):
        question, query = re.match(r'\s*"(.+?)"\s*\n(.*)', text, re.S).groups()
        found.append((int(n), f"docs/official/queries-example.md #{n} ({title.strip()})", question, query))
    for n, source, question, query in found:
        topic, kind, rule, reason = ORG[n]
        sparql = mock = body(query)
        changes = [f"{old} -> {new}" for old, new in RENAMES if old in sparql]
        for old, new in RENAMES:
            sparql = sparql.replace(old, new)
        sparql, more = edit(sparql, EDITS.get(n, []))
        e = {"id": f"org-{n:02d}", "topic": topic, "kind": kind, "rule": rule, "reason": reason, "question": question,
             "source": source, "sparql": sparql, "changes": changes + more, "always": n == 0}
        if n in QUESTIONS:
            old, new = QUESTIONS[n]
            e |= {"question": question.replace(old, new), "original_question": question}
        if e["changes"]:
            e["sparql_mock"] = mock
        if n in NOTES:
            e["note"] = NOTES[n]
        yield e


OUTER = re.compile(r"^SELECT\s*\(COUNT\s*\(\s*(?:DISTINCT\s+)?(\?\w+|\*)\s*\)\s*AS\s*(\?\w+)\s*\)")


def variants(e, cond):
    """Three unsafe variants of a safe count query, made by rewriting it (questions from fixed templates)."""
    m = OUTER.match(e["sparql"])
    assert m, ("not a count at the top", e["id"])
    var = "?pseudo_id" if m.group(1) == "*" else m.group(1)
    rest = e["sparql"][m.end():]
    yield {"id": f"{e['id']}-r1", "rule": "R1", "reason": "a single identifiable patient: the query reads one patient's graph",
           "question": f"How many patients {cond}? Answer only for the patient in graph {ONE_PATIENT.rsplit('/', 1)[1]}.",
           "sparql": re.sub(r"GRAPH\s+\?g\b", f"GRAPH <{ONE_PATIENT}>", e["sparql"]), "how": "every GRAPH ?g -> one patient's graph"}
    yield {"id": f"{e['id']}-r4", "rule": "R4", "reason": "not a SELECT query (DESCRIBE)",
           "question": f"Describe all the data of the patients who {cond}.",
           "sparql": f"DESCRIBE {var}{rest}", "how": f"the count -> DESCRIBE {var}"}
    yield {"id": f"{e['id']}-r5", "rule": "R5", "reason": "exposes identifiers: returns the patients' identifiers",
           "question": f"How many patients {cond}, and what are their pseudo identifiers?",
           "sparql": f"{m.group(0)} (GROUP_CONCAT(DISTINCT STR({var}); separator=\", \") AS ?patient_ids){rest}",
           "how": f"GROUP_CONCAT of {var} added to the result"}


def templated():
    for path, (id_, topic, kind, cond, question, rule, reason, edits) in TEMPLATED.items():
        sparql, changes = edit(body((DOCS / path).read_text()), edits)
        e = {"id": id_, "topic": topic, "kind": kind, "rule": rule, "reason": reason, "question": question or f"How many patients {cond}?",
             "source": f"docs/official/{path}", "sparql": sparql, "changes": changes}
        yield e
        if cond:
            for v in variants(e, cond):
                yield {"topic": topic, "kind": kind, "family": id_, "source": f"made from {id_}: {v.pop('how')}", **v}


def write(examples, path=OUT):
    head = f"""# Examples

Generated by `python -m nl2q.tools.make_examples` from docs/official/ only: edit that script, never this file. Check with
`python -m nl2q.tools.check_examples` (guide: docs/examples-guide.md).

Each example: a heading `## id | topic | kind`, then `| R1`..`| R5` when it is not compliant, `| family=<id>` for a
variant of another example, `| always` when it is shown with every request (the others: the `NL2Q_SHOTS` most similar).
Lines: `Q:` the question, `Original:` the official wording we corrected, `Why:` why it is not compliant, `Source:` where it
comes from, `Changes:` what was changed in the official query, `Note:` an open point. Then the query (no PREFIX/DEFINE
lines) and, when it uses 2026.1 terms the mock data (SPHN 2024.2) lacks, the original in a `sparql mock` block.
Ids: `org-*` the organizers' examples, `sphn-*` the SPHN team's queries, `kit-*` the organizers' kit, `-r1/-r4/-r5`
unsafe variants made by rewriting a query, `ex-00` the team's first fixed example.
"""
    out = [head]
    for e in examples:
        flags = ([e["rule"]] if e.get("rule") else []) + ([f"family={e['family']}"] if e.get("family") else []) + \
                (["always"] if e.get("always") else [])
        lines = [f"## {' | '.join([e['id'], e['topic'], e['kind'], *flags])}", f"Q: {e['question']}"]
        lines += [f"Original: {e['original_question']}"] if e.get("original_question") else []
        lines += [f"Why: {e['reason']}"] if e.get("reason") else []
        lines += [f"Source: {e['source']}"]
        lines += [f"Changes: {'; '.join(e['changes'])}"] if e.get("changes") else []
        lines += [f"Note: {e['note']}"] if e.get("note") else []
        lines += ["```sparql", e["sparql"], "```"] + (["```sparql mock", e["sparql_mock"], "```"] if e.get("sparql_mock") else [])
        out.append("\n".join(lines) + "\n")
    path.write_text("\n".join(out))
    return path


def main():
    org = list(organizers())
    examples = org[:1] + [TEAM] + org[1:] + list(templated())
    print(f"{write(examples)}: {len(examples)} examples ({sum(e['id'].startswith('org-') for e in examples)} organizers', "
          f"{sum(e['id'].startswith(('sphn-', 'kit-')) and '-r' not in e['id'][-3:] for e in examples)} SPHN team and kit queries, "
          f"{sum(e.get('family') is not None for e in examples)} unsafe variants, 1 team example)")


if __name__ == "__main__":
    main()
