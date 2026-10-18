# Easy test set (E01-E30)

30 easy, safe questions about the SPHN mock data, each with a reference SPARQL query that has been checked on the
database. The set tests how a question-to-SPARQL translator does on easy questions. The file is `easy.jsonl`, one
object per line: `id`, `kind`, `question`, `sparql`, `answer` (the shape of the answer).

Run the translator on it and compare the rows with the references (5k store by default; `SPARQL_ENDPOINT` for
another store):

```
python -m nl2q.tools.query_check --set easy --runs 1 --timeout 30 --out <folder>
```

Results of the shipped system on the 5k and the 100k store: `docs/report/translation.md`, sections 11 and 12.

**29 items are scored.** E29 keeps its question but not its reference (field `reference_invalid`, note in
`unscored`): the reference reads `sphn:hasRecordDateTime`, which SPHN 2026.1 does not allow on a `DrugPrescription`,
and the data's prescriptions carry no other date, so no valid reference exists. Found after the first run, by the
validity check every reference must pass.

## How it was made

- **Blind.** The set was written without opening any part of the translator: not its code, prompts, example bank,
  training data, runs, benches or reports. The questions were not shaped to suit it.
- **Sources.** `docs/official/queries-example.md` (only the SPARQL patterns were reused, not the questions),
  `docs/data.md`, `docs/organizer-notes.md`, the 2026.1 term list loaded by `nl2q/schema.py`,
  `nl2q_data/query_check/terms.json` (terms missing from the data) and `/data/scratch/nw-data/stats/` (code IRIs and
  counts). Units and codes were then picked with aggregate queries on the 5,000-patient store, which returned only
  IRIs and counts. No terminology label or hierarchy was read.
- **Patterns.** One named graph per patient (`GRAPH ?g`), patients counted as `COUNT(DISTINCT ?g)`, codes matched
  with their sub-codes through `OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global")`, quantities read
  through `sphn:hasQuantity`/`sphn:hasValue` with the unit's UCUM code, and breakdowns by code returning the code's
  class IRI (with a `STRSTARTS` filter on the namespace). No `rdfs:label`, no `skos:`, no explicit terminology
  hierarchy.
- **Terms.** Only 2026.1 names that also occur in the 2024.2 data are used. `to_2024` mapped nothing in any of the 30
  queries. Avoided on purpose: `hasAdmissionDateTime`, `hasDischargeDateTime`, `hasRank` and
  `hasFirstAdministrationDateTime`, which are not 2026.1 names.
- **Validation.** Each query ran once, one at a time, with `execute(to_2024(q)[0], 60)` on http://localhost:8890/sparql
  (5,000 patients). All 30 run without error, return at least one row, and none is blank: no all-zero or empty answer.
- **Kinds.** A: 7 (patients with one thing), B: 3 (records), C: 5 (thresholds), D: 5 (average, minimum, maximum),
  E: 4 (two conditions), F: 4 (breakdowns), G: 2 (dates). 10 questions name a code (ICD-10-GM, CHOP or SNOMED CT);
  the other 20 use plain words.

## Data facts behind the choices

- **LOINC and ATC never occur in the data**, so the named codes are ICD-10-GM, CHOP and SNOMED CT only.
- **Units.** Each measurement used here has a single unit in the data, so the unit filter drops nothing:
  blood pressure mm[Hg] (100%), oxygen saturation %, BMI kg/m2, gestational age d, body surface area m2, tumor purity
  %. Body weight, height, temperature and heart rate carry random units (no kg, cm or /min), so they are left out.
- **Values are synthetic** (0 to 9999), so averages such as 4978 mmHg are expected. Don't judge them by plausibility.
- **ICD-10-GM.** Unversioned codes reach all year versions through inference: `icd-10-gm:A00` matches all 3,518
  cholera records across the 13 versions. I10 and E11 exist only as the unversioned sub-codes I10.90 and E11.90, so
  E01 and E06 need inference. A direct match on I10 or E11 returns 0.
- **CHOP.** Only `chop:00.0` reaches the year-versioned codes through inference (3,405 records). An unversioned leaf
  such as `chop:00.03` matches only its 97 unversioned records, against about 1,474 across the versions, and 00.01
  and 00.02 match nothing. CHOP questions therefore use 00.0 (ultrasound treatment, all sub-codes).
- **SNOMED CT.** The named codes (63856005, 419320008) give the same count with and without inference, so including
  sub-codes or not makes no difference.
- **Lab tests.** No lab-test-code question. The microbiology tests are subclasses of `sphn:LabTest`, and every
  SNOMED CT code used on `sphn:LabTest` also appears on them. "Any lab test" gives 2,689 patients through
  `LabTestEvent` alone and 3,577 with the microbiology events, so such a question has two answers. E04 uses the
  antimicrobial susceptibility test, a class of its own.

## Per item (5,000-patient store)

| id | kind | rows | seconds | 5k value | note |
|---|---|---|---|---|---|
| E01 | A | 1 | 0.00 | 15 | only I10.90 occurs; inference needed |
| E02 | A | 1 | 0.00 | 437 | `RadiotherapyProcedure` |
| E03 | A | 1 | 0.00 | 64 | prescription -> drug -> active ingredient code |
| E04 | A | 1 | 0.00 | 710 | `AntimicrobialSusceptibilityLabTestEvent` (see Lab tests) |
| E05 | A | 1 | 0.00 | 1915 | `BodyMassIndex` |
| E06 | A | 1 | 0.00 | 5 | only E11.90 occurs; inference needed |
| E07 | A | 1 | 0.01 | 1417 | replaced: CHOP 00.02 gave 0 (see CHOP) |
| E08 | B | 1 | 0.01 | 36790 | prescription records |
| E09 | B | 1 | 0.01 | 3518 | cholera records, A00.0/A00.1/A00.9 across 13 versions |
| E10 | B | 1 | 0.00 | 597 | replaced: CHOP 00.03 records were only the unversioned 97 |
| E11 | C | 1 | 0.16 | 2464 | systolic > 140 mm[Hg] |
| E12 | C | 1 | 0.03 | 27 | oxygen saturation records < 90 % |
| E13 | C | 1 | 0.02 | 1911 | BMI >= 30 kg/m2 |
| E14 | C | 1 | 0.01 | 115 | gestational age < 259 d |
| E15 | C | 1 | 0.03 | 4620 | tumor specimens, purity > 50 % |
| E16 | D | 1 | 0.03 | 4978.24 | average over all diastolic readings |
| E17 | D | 1 | 0.03 | 9993.0 | maximum |
| E18 | D | 1 | 0.02 | 3.0 | minimum, not zero |
| E19 | D | 1 | 0.01 | 5059.05 | one gestational age per patient |
| E20 | D | 1 | 0.01 | 5253.35 | average over all body surface area records |
| E21 | E | 1 | 0.08 | 1521 | second condition as `FILTER EXISTS` |
| E22 | E | 1 | 0.01 | 341 | 437 with radiotherapy, 96 of them with cancer surgery |
| E23 | E | 1 | 0.03 | 295 | fixed twice: CHOP 00.01 gave 0; then "A00.0 but not A00.1" excluded nobody |
| E24 | E | 1 | 0.12 | 8 | replaced the CHOP 00.03 condition |
| E25 | F | 3 | 0.01 | - | three SNOMED CT sex classes |
| E26 | F | 16 | 0.00 | - | years 2010 to 2025 |
| E27 | F | 5 | 0.10 | - | no tie at the cut: 5th has 71 patients, 6th has 70 |
| E28 | F | 5 | 0.07 | - | no tie at the cut: 5th has 991 prescriptions, 6th has 925 |
| E29 | G | 1 | 0.01 | - | two date-times (1955 to 2026); unscored: the property is not 2026.1 on prescriptions |
| E30 | G | 1 | 0.00 | 70 | `YEAR(hasRecordDateTime) = 2021` |

Values come from the 5,000-patient store; on the 100,000-patient store the values differ.
