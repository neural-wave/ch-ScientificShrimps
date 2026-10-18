# Fresh test set (F01-F30)

30 clear, safe questions about the SPHN mock data, each with a reference SPARQL query checked on the
5,000-patient store. It is the second set of this kind, after `tests/easy/` (E01-E30), and repeats none of its
questions. The file is `fresh.jsonl`, one object per line, in the same format as `tests/easy/easy.jsonl`: `id`,
`kind`, `question`, `sparql`, `answer` (the shape of the answer).

Run the translator on it and compare the rows with the references (5k store by default; `SPARQL_ENDPOINT` for
another store):

```
python -m nl2q.tools.query_check --set fresh --runs 1 --timeout 30 --out <folder>
```

Its writer was asked to cover many SPHN classes, including drug prescriptions, blood-pressure values and specimens,
the kinds the hybrid plan format added; the questions themselves were never read while building or training it.
Results: `docs/report/translation.md`, section 12.

## How it was made

- **Blind.** The set was written without opening any part of the translator: its code (`nl2q/translation/`,
  `nl2q/training/`, the training and coded tools), its example bank (`nl2q/config/examples.md`,
  `nl2q_data/examples/`), training data, runs, benches, tests, reports and handoffs. The questions were not shaped
  to suit it. `nl2q/config/schema.json`, which `nl2q/schema.py` loads, was used only for term names: one grep for
  five value-set names. One disclosure: when its keys were listed, the first ~1,500 characters of its header, its
  index and one block (AccessDevicePresence) were printed. Nothing in this set comes from them.
- **Sources.** `tests/easy/` for the format, the data facts and the questions to avoid.
  `docs/official/queries-example.md` for SPARQL patterns only. `docs/data.md`, `docs/organizer-notes.md`,
  `nl2q/compliance/domains.json` (the properties each 2026.1 class allows, and its subclasses),
  `nl2q_data/query_check/terms.json` (terms missing from the data) and `/data/scratch/nw-data/stats/` (classes,
  edges, individuals, code IRIs and counts). Units, codes and thresholds were then picked with aggregate queries on
  the 5k store. Those queries returned only counts, numbers and IRIs. No terminology label or hierarchy was read.
- **Patterns.** The same as the first set:
  - one named graph per patient (`GRAPH ?g`), with patients counted as `COUNT(DISTINCT ?g)`;
  - codes matched with their sub-codes through `?code a <code> OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global")`;
  - quantities read through the class's quantity property, then `sphn:hasValue` and the unit's UCUM code class;
  - two conditions written as `FILTER EXISTS` / `FILTER NOT EXISTS` on the same `?g`;
  - breakdowns by code returning the code's class IRI, with a `STRSTARTS` filter on the namespace.

  No `rdfs:label`, no `skos:` and no explicit terminology hierarchy.
- **Validity.** `valid(q)` returns `(True, None)` for all 30. That check also rejects a property that the class
  does not allow in 2026.1: it rejects the first set's E29 reference. Each (class, property) pair was also checked
  by hand against `nl2q/compliance/domains.json`, with no violation. `to_2024` mapped nothing in any of the 30
  queries. These names were avoided on purpose:
  - `hasRank` / `hasRankCode`;
  - `hasAdmissionDateTime` / `hasDischargeDateTime`;
  - `hasFirstAdministrationDateTime`;
  - `hasRecordDateTime` on prescriptions;
  - every 2026.1 term the data lacks.

  The value-set individuals used (`LifeThreatening`, `NeutralBufferedFormalin`, the five outcomes) are named in the
  2026.1 schema config.
- **Run.** Each query ran with `execute(to_2024(q)[0], 60)` on http://localhost:8890/sparql, one at a time. All 30
  run without error and return at least one row. None is blank: there is no all-zero or empty answer. All 30 ran
  twice: once as a first draft, then after three wording edits and an indentation fix. Only counts, numbers and IRIs
  were printed.
- **Kinds.**
  - A: 8 (patients with one thing)
  - B: 4 (records of a type)
  - C: 6 (threshold, with the unit stated)
  - D: 5 (average, minimum or maximum, with the unit stated)
  - E: 4 (both, or A but not B)
  - F: 3 (breakdowns)

  Seven questions name a code: six SNOMED CT (F02, F03, F07, F08, F10, F12) and one ICD-10-GM (F04). F29 and F30
  break down by SNOMED CT code. The other 21 use plain words.

## Record classes covered (26, all with data)

| Area | Classes |
|---|---|
| Adverse events | AdverseEvent (consequences, outcome) |
| Allergies | Allergy (reaction type, allergen), AllergyEpisode |
| Diagnoses | OncologyDiagnosis (ICD-10-GM), NursingDiagnosis, BilledDiagnosis |
| Procedures | RadiotherapyProcedure (radiation dose), ImagingProcedure (code), ElectrocardiographicProcedure, OrganSupport |
| Drugs | DrugPrescription, DrugAdministrationEvent (reason to stop) |
| Specimens and labs | TumorSpecimen (fixation type, tumor purity), MicroorganismIdentificationLabTestEvent |
| Measurements | BloodPressure (systolic, diastolic, mean), BodyMassIndex, BodySurfaceArea, CardiacIndex, OxygenSaturation |
| Life events and other | Birth (delivery mode), GestationalAgeAtBirth, Death (condition leading to death), ResuscitationDirective, TransplantPresence, TobaccoExposure (duration) |
| Administration | AdministrativeCase (care handling type) |

Nested nodes used: Allergen, CareHandling, Quantity, Unit.

## Data facts behind the choices

- **Subclasses.** Several SPHN classes have subclasses:
  - Diagnosis, MedicalProcedure, Sample, Exposure and Measurement;
  - LabTestEvent, LabTest and LabResult (the microbiology tests).

  A question on the parent class would have two answers, with and without the subclasses, so every question names
  a leaf class. Two other ideas were dropped because two classes give different patient counts:
  - "genetic variant": VariantDescriptor or GeneticVariation;
  - "implant": ImplantPresence or the Implant device.
- **Equivalent readings give the same answer.** On the 5k store:
  - F05: `Electrocardiogram` = `ElectrocardiographicProcedure` = 1270 patients;
  - F06: the test node `MicroorganismIdentificationLabTest` = the event = 826;
  - F25: `Transplant` = `TransplantPresence` = 233;
  - F14: `BloodPressureMeasurement` → `hasResult` = `BloodPressure` = 159.
- **Filler codes.** Diagnosis, NursingDiagnosis, MedicalProcedure, OncologySurgery, ProblemCondition,
  ExcludedDisorder and the AdverseEvent code and severity point to a generic `sphn:Code`, which is filler. No code
  question uses them.
- **Units.** These quantities have a single unit on the 5k store, so the unit filter drops nothing:
  - blood pressure, all three: mm[Hg];
  - oxygen saturation and tumor purity: %;
  - BMI: kg/m2;
  - gestational age: d;
  - body surface area: m2;
  - cardiac index: L/min/m2;
  - tobacco exposure duration: a.

  These carry random units and are left out:
  - heart rate, temperature, weight, height and respiratory rate;
  - cardiac output, circumference and age;
  - fluid balance, drug quantity and administration duration;
  - allergy episode duration, time to positivity and ECG leads.

  The radiotherapy radiation quantity has four units: cGy 1709, Gy 909, mCi 791 and MBq 549 values. F23 asks for
  the dose in Gy. See its note.
- **Values are synthetic** (0 to 9999). The thresholds use clinical values, but the counts come from noise.
  - "Below" thresholds hit the low tail and stay discriminating: F13-F16.
  - "Above" thresholds keep almost everything: F17 keeps 4866 of 5000 patients, F18 keeps 1489 of 1497. The filter
    still changes the count.
  - Natural low thresholds for cardiac index, body surface area and tobacco duration would return 0 (minimums 33,
    22 and 11), so those classes are asked as D questions or with "above".
- **Inference.** Every named SNOMED CT code gives the same count with and without inference, except F03: 343
  patients with sub-codes against 130 for the exact code. Its question says "or any of its sub-codes". ICD-10-GM C18
  occurs only as sub-codes, so F04 needs inference. On the 5k store C18 is on the oncology diagnoses of 4 patients
  and on the billed diagnosis of 1 more patient, who is not counted: the question asks for an oncology diagnosis.
- **Codes are named by number only.** Their meaning would come from terminology labels, which were not read. The
  one exception is F04: "colon cancer" is a common description of C18, not a copied label.

## Per item (5,000-patient store)

| id | kind | rows | seconds | 5k value | note |
|---|---|---|---|---|---|
| F01 | A | 1 | 0.00 | 148 | `hasConsequences sphn-individual:LifeThreatening` |
| F02 | A | 1 | 0.00 | 48 | `Death hasConditionCode`; same count with and without inference |
| F03 | A | 1 | 0.00 | 343 | inference matters: 130 for the exact code; question says sub-codes included |
| F04 | A | 1 | 0.00 | 4 | C18 only as sub-codes; oncology diagnoses only (1 more patient has C18 billed) |
| F05 | A | 1 | 0.00 | 1270 | same count via `sphn:Electrocardiogram` |
| F06 | A | 1 | 0.00 | 826 | own class, not `LabTestEvent`; same count via the test node |
| F07 | A | 1 | 0.00 | 127 | `Birth hasDeliveryModeCode`; same count with and without inference |
| F08 | A | 1 | 0.00 | 712 | `Allergy hasReactionTypeCode`; same count with and without inference |
| F09 | B | 1 | 0.00 | 7936 | allergy episode records (1353 patients) |
| F10 | B | 1 | 0.00 | 282 | records, not patients (41); same count with and without inference |
| F11 | B | 1 | 0.00 | 354 | `TumorSpecimen hasFixationType sphn-individual:NeutralBufferedFormalin` |
| F12 | B | 1 | 0.00 | 696 | events, not patients (152); same count with and without inference |
| F13 | C | 1 | 0.00 | 23 | `hasMeanPressure` < 65 mm[Hg] |
| F14 | C | 1 | 0.00 | 159 | readings, not patients; same via `BloodPressureMeasurement` |
| F15 | C | 1 | 0.00 | 4 | BMI < 18.5 kg/m2; low tail, small on 5k |
| F16 | C | 1 | 0.00 | 14 | patients, not specimens |
| F17 | C | 1 | 0.02 | 4866 | > 294 d; nearly all patients (see Values) |
| F18 | C | 1 | 0.02 | 1489 | `TobaccoExposure hasDuration` > 40 a; 1497 tobacco patients in all |
| F19 | D | 1 | 0.01 | 9987.0 | maximum, L/min/m2 |
| F20 | D | 1 | 0.01 | 9927.0 | maximum, m2 |
| F21 | D | 1 | 0.03 | 9.0 | minimum, not zero |
| F22 | D | 1 | 0.03 | 5215.43 | average over all readings, not per patient |
| F23 | D | 1 | 0.01 | 9919.0 | Gy only; converting cGy to Gy gives the same maximum (cGy max 9961 = 99.61 Gy); no unit filter gives 9961 |
| F24 | E | 1 | 0.00 | 78 | second condition as `FILTER EXISTS` |
| F25 | E | 1 | 0.00 | 233 | `FILTER NOT EXISTS`; same count via `sphn:Transplant` |
| F26 | E | 1 | 0.02 | 775 | nursing diagnosis, no billed diagnosis |
| F27 | E | 1 | 0.23 | 2311 | the slowest reference |
| F28 | F | 5 | 0.00 | - | five outcome individuals: 199, 191, 186, 176, 135 events |
| F29 | F | 5 | 0.01 | - | no tie at the cut: 5th has 38 patients, 6th has 37 (the 39/39 tie inside the top 5 does not matter) |
| F30 | F | 3 | 0.01 | - | three SNOMED CT care handling types; one case per patient, so cases = patients |

Values come from the 5,000-patient store. On the 100,000-patient store the values differ: it was not queried.

## Choices changed before the final run

No reference failed validation, so none was replaced after it. These ideas were dropped during exploration:

- **A top 5 of delivery modes:** tie at the cut, with the 5th and 6th both at 84 births.
- **Low thresholds on cardiac index, body surface area and tobacco duration:** 0 rows.
- **Parent-class questions:** the subclass ambiguity above.
- **Genetic variant and implant questions:** two classes, two answers.

Three questions were reworded for clarity (F07, F10, F28), and the indentation of the quantity queries was fixed.
All 30 references were then run again.
