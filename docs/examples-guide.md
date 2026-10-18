# Worked examples

The translator learns from worked examples: a question, its SPHN 2026.1 query and the verdict it should get. They are
made **only from official material, by plain code**: no example is written by a model.

```
docs/official/  ──►  python -m nl2q.tools.make_examples  ──►  nl2q/config/examples.md  ──►  the prompt
                                                                      │
                                         python -m nl2q.tools.check_examples  (every check below)
```

## Sources
| Ids | Source | Question |
|---|---|---|
| `org-00` … `org-15` | the organizers: the brief's worked example and `queries-example.md` | the official one (two corrected to match their query, the original kept as `Original:`) |
| `sphn-*` | the SPHN team's feasibility queries (`docs/official/sphn-sparql-optimization/`, see its `SOURCE.md`) | a fixed template with the codes the query uses |
| `kit-*` | the organizers' Virtuoso kit (`docs/official/kit-queries/`) | a fixed template |
| `*-r1`, `*-r4`, `*-r5` | unsafe variants of each templated safe query, made by rewriting it: one patient's graph, `DESCRIBE`, identifiers added | a fixed template |
| `ex-00` | the team's first fixed example (literal translation of a request for identifiers) | as written |

Every change to an official query is mechanical and listed in its `Changes:` line: SPHN 2024.2 names → 2026.1 (the mock
data is 2024.2, so the original is kept as `sparql mock` to run on it), the SPHN team's internal shortcut properties
(`einstein:`) → the 2026.1 paths, and one bug fix (`EXCLUDING.rq` kept the patients *with* the diagnosis). Queries not
used, and why, are listed at the end of `TEMPLATED` in `make_examples.py`.

## How they are used
- Examples marked `always` are shown with every request; of the others, the `NL2Q_SHOTS` (default 4) most similar to the
  request are added (shared words, BM25; at most two per family).
- `python -m nl2q.tools.evaluate` turns on `NL2Q_HOLDOUT`: a test question never sees its own example or its variants.

## Checks (`check_examples`)
| Check | What fails |
|---|---|
| fields | a missing field, a label that contradicts its rule, a duplicate id |
| valid | the query does not parse (as the API checks it) |
| terms | a `sphn:` term that SPHN 2026.1 does not define |
| links | a property used on a record type that does not have it in 2026.1 (e.g. `hasAdministrativeCase` on `DrugPrescription`) |
| inference | an ICD-10-GM or CHOP class matched without `OPTION (inference ...)`: the data codes them per year, so it never matches |
| style | `PREFIX`/`DEFINE` lines in the stored query; a compliant variant without `GRAPH ?g` |
| verdict | the compliance check disagrees with the label, or finds another rule than the one given |
| virtuoso, codes | (`--virtuoso`) Virtuoso 7.2.16 does not compile it; a code is not in the loaded terminologies |
| run | (`--run`) the query fails on the data |

`--explain <id>` prints what a query does in plain words; `--pack` writes one file to review all examples with another
person or model. pytest checks that `config/examples.md` is exactly what the generator writes.

## Restricted terminologies
Questions name codes (ICD-10-GM E11, LOINC 718-7, ...), never labels copied from SNOMED CT, ICD-10-GM, LOINC, CHOP or UCUM.
