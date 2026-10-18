# nl2q: compliant SPARQL from natural language over SPHN clinical graphs

State of `main` on Sun 18 Oct 2026 (ba5a2d6). How the design got here, idea by idea: [ideas-tracker.md](ideas-tracker.md).

## Task (official brief and organizer answers)
Translate a researcher's request into SPARQL over SPHN 2026.1 data in Virtuoso 7.2.16 (one named graph per patient),
and say whether the query complies with the security constraints. `POST /nl-to-sparql {"text"}` returns
`{sparql_query, tokens_consumed, is_compliant, compliance_reason}`. Judged on:
- time to a compliant query, retries included;
- verdict accuracy: false compliant and false refusal both count, and so does the quality of the reason;
- the results: the organizers check that a compliant query returns the right rows (Discord, 17 Oct 17:30);
- the query's execution time in Virtuoso (mean of 10 runs), on the 100,000-patient mock data, with a 10-minute limit;
- then tokens, model size and RAM.

The evaluation has 15-20 questions that are not among the examples. The mock data was generated with SPHN 2024.2 by
mistake; the evaluation questions are 2026.1 and avoid the terms that differ. The judged system must be **static**: it
must not depend on the database it runs against. Answers and dates: [organizer-notes.md](organizer-notes.md).

## Design
```
text ─► d3 pre-screen on the request (NL2Q_PRESCREEN, off): blocked ─► empty query + reason, done
     ─► plan path (NL2Q_PLAN, off): JSON plan ─► compiler ─► validate ─► judge
          the plan cannot express the request, or validate objects ─► free generation below
     ─► prompt: system rules + conventions, linked schema card, code lookup, 2 fixed + 4 similar worked examples
     ─► generator (Qwen3.8-27B FP8, vLLM) ─► ```sparql``` body
     ─► finish (DEFINE sql:select-option "order" + PREFIX lines) ─► dialect ─► code class
     ─► compliance.validate
          syntax error, unknown sphn: term, schema feedback ─► back to the generator (at most MAX_RETRIES = 2)
     ─► optimizer (NL2Q_OPTIMIZE, off): same rows, faster in Virtuoso
     ─► compliance.judge: the rules ─► response
```
- **The generator is literal.** It never refuses and never repairs a request, so "list the patients with X" comes out
  as the listing query and the checker marks it non-compliant. Rewriting a non-compliant request into a compliant
  query would be a false "compliant", the costly error.
- **The verdict is code.** No tokens and milliseconds, so time and token cost are the generator's alone (plus d3's
  with the optional pre-screen).
- **A retry can only fix form.** `validate()` speaks of syntax and schema only, never of compliance. Its schema
  feedback runs only on queries the rules accept: "?g is never bound" on a single-patient query would get `GRAPH ?g`
  back on the retry, a compliant query for a non-compliant request.
- **Every rewrite is judged as returned.** Dialect, code class and the optimizer change the query before `judge()`.
  A rewrite that no longer parses, or that the reader does not fully understand, leaves the query unchanged.
- **The judged path is static.** With the defaults nothing runs a query or reads the data's counts while answering
  (a test fails on any SPARQL request or any read of the counts). The parts that do are behind switches, off.
- **Query style follows the organizers' examples:** `GRAPH ?g` per patient, `COUNT(DISTINCT ?g)` or the
  `SELECT DISTINCT(?g AS ?pseudo_id)` subquery, codes as `?code rdf:type icd-10-gm:E11 OPTION (inference ...)`,
  most selective triple first under `DEFINE sql:select-option "order"`.

## Stages and defaults
The defaults are what the jury gets ([nl2q/settings.py](../nl2q/settings.py) has every switch; the README lists the
main ones).

| Stage | Where | Switch, default | Static | Why it is on or off |
|---|---|---|---|---|
| Schema linking: card by BM25 over class definitions, code lookup, schema feedback | `translation/prompt.py`, `compliance/feedback.py` | `NL2Q_LINK`, on | yes | the card shows 92.4% of the classes the references need (73.9% before) |
| Code node to its class | `translation/codeclass.py` | `NL2Q_CODE_CLASS`, on | yes | grouping by a code node was the main false refusal (R5) |
| Virtuoso dialect | `translation/dialect.py` | `NL2Q_DIALECT`, on | yes | Virtuoso refuses `ROUND(x, n)` and `EXISTS {..} && ..` |
| Shape rule in the prompt | `translation/prompt.py` | `NL2Q_SHAPE_RULE`, on | yes | OPTIONAL only for unmatched rows; a flag from another record is `EXISTS` |
| Order pragma | `prompt.finish` | `NL2Q_SELECT_ORDER`, on | yes | the written order is the execution order |
| Reasoning before the query | `translation/llm.py` | `NL2Q_THINK`, off | yes | +3 verdicts on 26 at about 4x the latency (overnight exp 4) |
| Plan path and compiler | `translation/plan.py` | `NL2Q_PLAN`, off | yes | needs the fine-tuned plan writer (`NL2Q_PLAN_URL`) |
| Syntax grammar | `translation/grammar.py` | `NL2Q_GRAMMAR`, off | yes | vLLM structured output: the model can only write SPARQL that parses |
| Prefix cache | `prompt.card`, `generate.warm_up` | `NL2Q_FIXED_CORE`, `NL2Q_WARM_PREFIX`, off | yes | cached prompt 20.7% to 45.8%, -0.2 s prefill per request |
| d3 pre-screen | `compliance/d3.py` | `NL2Q_PRESCREEN`, off | yes (request text) | kept for the demo |
| Optimizer on SHACL 2026.1 | `optimize/` | `NL2Q_OPTIMIZE`, off (`NL2Q_OPT_STATS=schema`) | yes | x1.83 on the changed queries at 100k, timeouts 28 to 18 |
| Optimizer on the data's counts, card cues, slow path | `optimize/stats.py`, `cost.py`, `slowpath.py` | `NL2Q_OPT_STATS=data`, `NL2Q_CARD_STATS`, `NL2Q_SLOWPATH`, off | no | organizers: no database-dependent part in the judged system |
| k-anonymity | `compliance/kanon.py` | `NL2Q_KANON`, off | no (runs the query) | organizers: a dynamic stage, not part of the static system |

## Translation layer (`nl2q/translation/`)
- **Prompt** (`prompt.messages`): the system rules and `resources/conventions.md` (how the dataset words things), the
  worked examples as chat turns (the `always` ones plus the `NL2Q_SHOTS=4` most similar by BM25, at most two per
  family), then one user turn with the schema card, the codes, the request and, on a retry, the error to fix.
  In evaluation, holdout hides a request's own example and its family.
- **Schema card** (`NL2Q_LINK`): the core blocks (AdministrativeSex, Age, Birth, Death) plus the 6 blocks voted for by
  the classes whose label, definition and notes best match the request; a nested class lifts its vote to the blocks
  that reach it, and an ICD-10-GM or CHOP code named in the request votes for the blocks that record it. The card is
  built from the 2026.1 schema alone (`tools.adapt --linked`, `config/card.json`).
- **Code lookup:** `nl2q_data/codes.json`, built from the kit's terminology files (`tools.codes`, by
  `serving/serve_api.sh` on first start). It holds licensed labels, so it is never committed. Codes are searched only
  among those the card's code properties allow. Without the file the lookup is off.
- **Worked examples** (`config/examples.md`) come only from `docs/official/` (`tools.make_examples`,
  [examples-guide.md](examples-guide.md)).
- **Static rewrites of the reply**, in this order:
  - `finish`: the order pragma and a PREFIX line for every prefix used;
  - `dialect.fix`: `ROUND(x, 2)` becomes `ROUND(x * 100.0) / 100.0`, and a positive `EXISTS` left of `&&` / `||`
    becomes `IF(EXISTS {..}, .., false)`;
  - `codeclass.to_class`: a code node that is grouped, returned or compared becomes its class
    (`?c a ?c_class`), only when the outer query aggregates and the request does not ask for nodes or identifiers.
- **Plan path** (`NL2Q_PLAN`, Michele's track, [report/translation.md](report/translation.md)): a Qwen3.5-9B LoRA
  trained on synthetic (question, plan) pairs writes a JSON plan under structured output. Code checks it and compiles
  it into the SPHN team's fast pattern: one sub-query per record inside `GRAPH ?g`, a yes/no flag per record,
  `GROUP BY` the patient, a boolean `FILTER`, then the count, with the code triple first and the inference option on
  it only. The unsafe parts of a request stay literal in the plan (`unsafe`), so the rules judge what was asked.

## Compliance rules (`nl2q/compliance/rules.py`)
Virtuoso's `DEFINE` and `OPTION (...)` are set aside, rdflib parses the rest. Then the check follows what the query
*returns*: a variable carrying a patient (`GRAPH ?g`, `BIND(?g AS ?p)`, the object of `hasSubjectPseudoIdentifier`), a
record node (any node of the patient data, codes included) or an identifier value may be counted, never returned,
grouped by, sampled or compared with a fixed value. Values flow through `BIND`, `IF` branches, `COALESCE`, string
functions and aggregates other than `COUNT`; conditions (`BOUND`, comparisons, `EXISTS`) carry no identity. Subqueries
may do anything as long as the outer query aggregates.

| Rule (Query_Compliance.md) | Check |
|---|---|
| R1 single identifiable patient | fixed `GRAPH <iri>` (other than the ontology graph), `FROM`, a patient-data IRI, an identifier literal (also at the end of a property path, read from both ends of an inverse path `^p`), any constant shaped like a record ID, a UUID or a patient graph (`IDENTIFIER_SHAPES`), a patient or identifier compared with a constant or fixed by `VALUES` / `BIND`, a record's IRI compared with a string, a subquery that keeps particular patients (`LIMIT` / `OFFSET`, or `MIN` / `MAX` / `SAMPLE` of a patient that is then queried) |
| R2 aggregated across all patients | the outer query aggregates; every returned column is an aggregate or a group key; no hidden `GROUP BY` on patients or records |
| R3 no unrestricted whole-graph match | no variable predicate or negated path; every group of joined patterns has a class, code or value (`?x a sphn:Age . ?s a ?t` scans every typed node) |
| R4 only SELECT (read-only) | no update, ASK, CONSTRUCT, DESCRIBE, `SERVICE`, `DEFINE input:grab-*`, server-side built-ins (`bif:exec`, file and HTTP functions, `sql:` procedures) |
| R5 no identifiers exposed | no returned or grouped value carries a patient, record or identifier (MIN/MAX/SAMPLE/GROUP_CONCAT of one included); the subject of any `sphn:` or `dct:` property is a record (a patient's DataRelease node too); a code node's identifier only when a terminology class or `hasCodingSystemAndVersion` pins the code node (a bare `sphn:Code` identifier is a record ID in the data); a file hash's algorithm is a value |
| API: valid for Virtuoso | rdflib parse (Virtuoso's predefined prefixes allowed); inference rule set must be the loaded one; a variable named like rdflib's internal aggregates (`?__agg_N__`) is invalid, so the generator renames it |

A pin (a terminology class, a coding system, a hash type) counts only where it holds on every row: inside `OPTIONAL`
or one `UNION` branch only for variables bound nowhere else, never from `MINUS` or `FILTER (NOT) EXISTS`, and each
query level takes pins from its own patterns (a subquery passes on only what it projects). The fuzzing run and the
review of 17 Oct added these fixes; every one is stricter only.

How the rules are tested:
- `python -m nl2q.tools.evaluate --oracle` replays the reference queries of [`tests/bench/`](../tests/bench): 14 of the
  15 organizer examples (`official.jsonl`, O03 left out) plus 12 own cases (`compliance.jsonl`): 26/26. On the
  synthetic set (`tests/bench_synth/kg100.jsonl`) the rules agree with all 100 labels.
- Regression files `tests/compliance/{fuzz,leaks,mutations}.jsonl`; `python -m nl2q.tools.fuzz_rules` searches for
  false compliants with meaning-preserving rewrites and runs before every change to `nl2q/compliance/`.

## Optimizer (`nl2q/optimize/`, optional)
`NL2Q_OPTIMIZE=1` rewrites the generated query between `generate` and `judge`, so the verdict is computed on the
query that is returned. Passes (`NL2Q_OPT_PASSES`, default `paths,reorder,pragma`):
- `paths`: a sequence path becomes a chain of triples, so each step can be placed (Virtuoso may evaluate a path back
  to front);
- `reorder`: inside each group, a contiguous run of triple patterns in greedy order; nothing crosses `OPTIONAL`,
  `MINUS`, `BIND`, `VALUES`, `UNION` or a sub-group, and `OPTION (inference ...)` stays on its triple. The order key
  comes from SHACL 2026.1 (`NL2Q_OPT_STATS=schema`, static) or from the counts of the 100k mock patients (`data`,
  dynamic, off for the evaluation);
- `pragma`: a query with `OPTIONAL` loses the order pragma (at 100k the written order made such queries slower: four
  60 s timeouts ran in 1.7-3 s without it), unless it has a pattern the data never matches (data mode);
- `flag` (optional): an `OPTIONAL` used only through `BOUND` becomes `EXISTS`. Rows change by design: the OPTIONAL
  form counts a row once per match (6.0M rows against 153k for O02 at 100k), the reference counts with `EXISTS`.

Measured at 100k on 132 queries (cap 120 s): schema mode x1.83 geomean on the 80 queries it changes, timeouts 28 to
18, rows identical apart from flag's fix, 0.45 ms per query; data mode x2.74. Details and rejected passes:
[report/sparql-optimization.md](report/sparql-optimization.md).

## d3 pre-screen (optional)
`NL2Q_PRESCREEN=1` asks vllm-sr/d3 (Apache-2.0, `serving/serve_decision.sh`, :8002), before generation, which
criterion of `compliance/policy.json` (proceed + one per rule, 5 labelled requests each) the request text alone falls
under; its tokens count in `tokens_consumed`. It can only block: when P(not proceed) reaches `PRESCREEN_MIN_CONF`
(0.9) the request gets an empty query, `is_compliant: false` and the rule's wording as the reason (the organizers
allow an empty query for a non-compliant request); `not_aggregated` is only logged (`PRESCREEN_LOG_ONLY`). Everything
else, d3 unreachable or slower than `PRESCREEN_TIMEOUT` (1 s; awake it answers in ~0.3 s) included, is generated and
judged by the rules, so a miss costs nothing while a false block cannot be undone. On the bench: 0 false blocks, 9 of
14 non-compliant requests caught, -14% tokens; the policy examples were written after seeing the bench, so these
numbers are optimistic.

## Dynamic stages (off for the evaluation)
These read the database, so the organizers place them outside the static system; each is kept as a switch and
described in the report.
- `NL2Q_KANON=flag|rewrite`: refuse a result with a group of 1 to K-1 distinct patients (`NL2Q_K=10`), or add
  `HAVING (COUNT(DISTINCT ?patient) >= K)`. A rewritten query is judged again.
- `NL2Q_OPT_STATS=data` and `NL2Q_CARD_STATS`: the 100k counts (`optimize/cardinality.json`) for the optimizer's order
  and as cues in the card.
- `NL2Q_SLOWPATH`: one more generator call for a compliant query that the cost model predicts slow, kept only if it
  validates, stays compliant and costs less. It found 0 useful rewrites in 16 triggers.

## Evaluation
- `python -m nl2q.tools.evaluate [--url ...] [--virtuoso]`: verdicts, seconds and tokens per request; with
  `--virtuoso` also execution time (mean of 10) and row match. Holdout is on by default.
- `python -m nl2q.tools.rowcheck TRACE`: why generated and reference rows differ, by hash, never printing rows.
- `python -m nl2q.tools.kgsynth score TRACE`: the synthetic set by stratum and hop count
  ([report/synthetic-eval.md](report/synthetic-eval.md)).
  - The set has 100 items: 40 aggregate, 30 that expose identifiers, 30 that use identifiers safely.
  - Each gold query is compiled by code from a schema path; Apertus v1.5 8B (`serving/serve_apertus.sh`) writes each
    question, and never translates.
  - `kgsynth build --extend` adds items on unused record classes without touching a reviewed set.
- Measure execution time on the 100k store, never only on the 5k one, and compare cold with cold: 1-2 verdicts on 26
  requests are within the noise (overnight exp 4).

Today's numbers: `tests/bench/` 25/26 verdicts (N10 false compliant), 2 of 12 compliant requests with the reference's
rows (0 of the 9 official), 3.4 s and 8.1k tokens per request; synthetic set (100 items, 3 runs) 300/300 verdicts,
0 false compliant, 0 false refusal, 65% with the gold's rows, 1.9 s and 7.9k tokens per request. The plan path and the
grammar guard are off by default, so they do not change these numbers.

## Known limits
- **Correct results are the weak point.** The queries parse, use 2026.1 terms and run, but 0 of the 9 official
  compliant questions return the reference's rows; the remaining errors are about meaning (which path through the
  data, which value, which grouping), not syntax or schema recall.
  - On the synthetic set: numbers 83%, dates 70%, value-set individuals 35%.
  - The model ignores the card's `sphn-individual:` values: only 5 of 12 items naming one get it right. A static check
    on value-set properties would catch the rest.
- **N10 stays a false compliant.** For a count "stored at" a remote endpoint the generator drops the `SERVICE` clause,
  so the rules judge a local count. The rules are right on the query they get.
- **2026.1 terms the 2024.2 data lacks** (`hasAdmission`, `hasDrugPrescription`, ...) match nothing; a percentage
  over such a count makes Virtuoso abort the whole query (SR083 division by zero) where SPARQL 1.1 would leave the
  value unbound.
- **OPTIONAL fan-out** (the O02 family) can run past the 10-minute limit at 100k unless the model follows the shape
  rule or the flag pass rewrites it into `EXISTS`.
- Small cohorts are not detected: a count over a filter that happens to match one patient is aggregated and passes
  (the official rules are about query shape; k-anonymity is the dynamic answer).
- Variables are tracked by name across subqueries (a name reused for something else inherits its role).
- `literal_props` / `value_props` come from the schema's ranges; a property with unknown range counts as a record link.
  The 2024.2 terms of the data that 2026.1 dropped (admission and discharge dates, `hasRank`, ...) are classified in
  `rules.py` for the verdict only; `validate()` still reports them, so the generator keeps writing 2026.1.
- `IDENTIFIER_SHAPES` describe the mock data's generator (`ID-` + 14 digits, UUIDs); other data needs its own shapes.
- Open fuzz findings are false refusals (inverse paths, a terminology pin through a `BIND`): fixing them would loosen
  the rules, so they stay.
- A checker error is judged non-compliant (fail closed), never compliant and never an API error.
