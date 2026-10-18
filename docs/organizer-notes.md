# What the organizers told us

Answers that shape the system and the report.

## 17 Oct 17:30 clarification

- **Schema:** the evaluation uses SPHN 2026.1, so focus on the 2026.1 schema.
- **Queries and data:** a different set of queries from the examples, run on our same dataset, the 2024.2 mock data.
- **The evaluation queries also work on the 2024.2 schema.** The 2024.2/2026.1 differences are not part of the
  evaluation queries.
- **"We also check the results, yes."** Besides compliance and execution time, they check that a query returns the
  right results. This reverses the 08:45 answer below ("only the verdict and the times count").
- *For us:*
  - Correct results are now scored. The translation audit (`docs/report/translation-audit.md` on
    `lorenzo/translation-audit`) found 0 of 9 official compliant questions return the reference's answer, so
    translation accuracy is the top risk.
  - Keep writing 2026.1. Terms renamed between the versions won't be tested, so a 2026.1-only term that empties a
    query on this data (`hasDrugPrescription`, `hasAdmission`) is our own risk.

## 17 Oct clarification

**1. The system must be static: it must not depend on the database it runs against.**
- Metadata about the dataset may only be an optional component that improves optimization. It must not be part of the
  final evaluation. Today that metadata is:
  - the counts in `nl2q/optimize/cardinality.json`;
  - the metadata scan of the patient files;
  - the cost model and the slow path;
  - the `NL2Q_CARD_STATS` cues in the prompt.
- *For us:*
  - Every data-dependent feature stays behind its flag, off by default for the evaluation.
  - Only static rewrites (pure syntax, such as `paths`) can be on by default.
  - The stats-ranked `reorder` is data-dependent, so it needs a schema-only fallback or must stay off.

**2. k-anonymity and differential privacy are dynamic checks.**
- They belong in an evaluation stage that runs the query, for example after a defined timeout so that latency is
  preserved.
- The current system is a static evaluation, so it should not include them.
- *For us:* `NL2Q_KANON` stays off. The report describes k-anonymity as the dynamic stage it would be.

**3. The model.**
- A Chinese model (Qwen) is hard to justify to a legal team.
- Running **Apertus 8B** on a single GPU, testing how well it generates the queries and reporting the results as a
  table would be a plus. It is not mandatory.

**4. The evaluation set has 15-20 queries, none of them among the examples.**
- We need our own out-of-sample set to evaluate on.
- *For us:* the 14 official bench cases come from `queries-example.md` and sit in the example bank. Holdout hides them
  at evaluation time, but they are not truly unseen. We should write new questions that are not derived from the bank.

**5. No exotic schemas in the final evaluation.**
- Support them, but don't over-engineer.

**6. The report must document the differences between the 2024.2 data and the 2026.1 schema.**
- They explain the zero counts and broken lookups.
- *For us:* at 100k, 15 traced generated queries fail with a Virtuoso division by zero:
  - 5 count `sphn:hasDrugPrescription`, a 2026.1 property the data lacks;
  - 5 count codes the data does not contain (exact SNOMED and UCUM codes);
  - 5 have a group with nothing to count.
- *For us:* a 2026.1 property absent from the data returns nothing at once (O02#4: 0.00 s).
- *For us:* the metadata v2 work flags every (class, property) pair where the data and the SHACL 2026.1 cardinalities
  disagree.

## 16-17 Oct clarification

- **Non-compliant requests:**
  - `sparql_query` may be empty; `is_compliant: false` and the reason are what count.
  - The quality of the reason matters.
- **Guardrail:** a lightweight check on the request before generation is a good idea.
- **Schema:**
  - The data was generated with 2024.2 by mistake.
  - The evaluation queries are 100% 2026.1, checked one by one.
  - The example queries may contain 2024.2 slips (O06, O07, O08, O12).
- **Evaluation data:** the same dataset the teams have (the 100,000-patient mock data).
  - Hospitals give a query 10 minutes.
- **Scoring:**
  - Only the verdict and the times count: the time to produce the query and the time to run it.
  - The result rows are not scored. *(Reversed on 17 Oct at 17:30: they do check the results.)*
  - The weights are only "a balance".
- **Optimization tip:** the SHACL files give the properties and cardinalities of the schema.
- **k-anonymity:**
  - It is an innovative extra check that can earn bonus points.
  - A distribution query with a group under 10 is non-compliant.
  - Refined on 17 Oct (above): it belongs to a dynamic stage, not to the static system.
