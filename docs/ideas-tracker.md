# Ideas tracker: what we tried, in order (16-18 Oct 2026)

Every idea we built or measured during the hackathon, from the official brief (Fri 16 Oct) to the freeze (Sun 18 Oct,
02:30) and the synthetic set's extension to 100 items (03:20), with what happened to it and why. Times are local (CEST). Sources: the git history, `docs/report/`, the
experiment results in the team's scratch area and the session notes. Numbers and question IDs only, no result rows and
no terminology labels. The current design is in [design.md](design.md).

## How an idea was tested
- Each idea got its own branch (often its own git worktree) and its own switch, **off by default**, so `main` stayed
  unchanged until it was measured.
- The gate before a merge: `pytest -q` green, `evaluate --oracle` 26/26 (the rules alone on the reference queries),
  the compliance regression files unchanged, then an end-to-end run against the tag `baseline-v0`.
- From 17 Oct 17:30, when the organizers said they check the results, the first criterion became "does the query
  return the reference's rows", measured by `rowcheck` (by hash, rows never printed).
- 1-2 verdicts on 26 requests are within the noise (overnight exp 4), so a gain had to show in more than one run.

## Outcomes at a glance
**On**: in `main`, on by default. **Off**: in `main` behind a switch, off by default. **Branch**: built and measured,
not merged. **Dropped**: removed or never merged, for a measured reason. **Parked**: not built, or never run end to end.

| # | When | Idea | Outcome | Where |
|---|---|---|---|---|
| 0.1 | before 16 Oct | Privacy on the answers: group threshold, session audit, differential privacy | Dropped | old repo, deleted |
| 1.1 | Fri 22:35 | Two layers that meet in one file | On | `pipeline.py` |
| 1.2 | Fri 22:35 | The generator translates literally, code decides | On | `compliance/rules.py` |
| 1.3 | Fri 23:14 | Example bank from official sources only, holdout in evaluation | On | `config/examples.md` |
| 1.4 | Fri 23:42 | d3 pre-screen on the request text | Off (demo) | `NL2Q_PRESCREEN` |
| 1.5 | Sat 00:54 | Mutation cases for the rules | On | `tests/compliance/mutations.jsonl` |
| 1.6 | Sat 02:07 | Freeze a baseline tag | Practice | tag `baseline-v0` |
| 2.1 | Sat 02:56 | k-anonymity on compliant results | Off (dynamic) | `NL2Q_KANON` |
| 2.2 | Sat 03:10 | Fuzz the rules with meaning-preserving rewrites | On | `tools/fuzz_rules.py` |
| 2.3 | overnight | A 2024.2 + 2026.1 union schema | Dropped | `night/exp3-schema` |
| 2.4 | Sat 06:07 | Settings grid: reasoning, shots, pre-screen, pragma | THINK off, SHOTS=4, pragma on | exp 4 |
| 2.5 | Sat 07:27 | A second Virtuoso with all 100k patients | On (tooling) | `nl2q-jury-virtuoso` :8990 |
| 3.1 | Sat 11:26 | Close three leaks found in a review | On | `rules.py` |
| 4.1 | Sat 11:35 | Deterministic SPARQL optimizer from the mentor's paper | Off | `NL2Q_OPTIMIZE` |
| 4.2 | Sat 15:58 | Optimizer knowledge: SHACL only vs the data's counts | schema Off, data Off (rule) | `NL2Q_OPT_STATS` |
| 4.3 | Sat afternoon | Qwen as the optimizer | Dropped | - |
| 4.4 | Sat 15:50 | `flag` pass: OPTIONAL-as-BOUND becomes EXISTS | Off | `NL2Q_OPT_PASSES=flag,...` |
| 4.5 | Sat afternoon | Decorrelate EXISTS; drop or tighten OPTIONALs | Dropped | - |
| 4.6 | Sat 14:25 | Cost model and a slow path | Off | `NL2Q_SLOWPATH` |
| 4.7 | Sat 14:17 | Prefix cache: fixed head in SYSTEM, warm-up | Off | `NL2Q_FIXED_CORE`, `NL2Q_WARM_PREFIX` |
| 4.8 | Sat 14:31 | Prompt hints: shape rule, data cues per schema block | shape rule On (from 20:28), cues Off | `NL2Q_SHAPE_RULE`, `NL2Q_CARD_STATS` |
| 4.9 | Sat 16:32 | Scan the 100k data against SHACL 2026.1 | On (report) | `tools/metadata_scan.py` |
| 5.1 | Sat 18:34 | Translation audit: do our queries return the reference's rows? | On (tooling) | `tools/rowcheck.py`, `report/translation-audit.md` |
| 6.1 | Sat 18:49 | Better card retrieval, synthetic patients, A/B tool | Branch | `priya/synthetic-data` |
| 6.2 | Sat 19:52 | Schema linking and code lookup | On | `NL2Q_LINK` |
| 6.3 | Sat 20:28 | Code nodes become their classes | On | `NL2Q_CODE_CLASS` |
| 6.4 | Sat 20:42 | Medical synonyms for the card | Parked | - |
| 6.5 | Sat 21:43 | Rewrite the forms Virtuoso refuses | On | `NL2Q_DIALECT` |
| 6.6 | Sat ~21:45 | Three extra conventions in the prompt | Off | `NL2Q_RULES2` |
| 6.7 | Sat 21:06 | JSON spec route: the model fills a spec, code writes SPARQL | Branch | `priya/synthetic-data`, `NL2Q_IR` |
| 7.1 | Sat 21:32 | Static semantic verifier with repair hints | Branch | `lorenzo/verify`, `NL2Q_VERIFY` |
| 7.2 | Sat 22:15 | Five ideas in parallel: verify hints, path check, drafts vote, adapt an example, empty probe | Branch (all failed the gate) | `lorenzo/{try-verify,path-check,multi-draft,adapt-example,empty-probe}` |
| 8.1 | Sun 01:42 (100 items 03:20) | Synthetic evaluation set grounded in the knowledge graph | On (evaluation) | `tools/kgsynth.py`, `tests/bench_synth/` |
| 8.2 | Sun ~00:40 | Apertus 8B as the translator | Parked (question writer only) | `serving/serve_apertus.sh` |
| 8.3 | Sat 15:07 to Sun 02:32 | Plan path: a fine-tuned 9B writes a JSON plan, code compiles it | Off | `NL2Q_PLAN`, `NL2Q_GRAMMAR` |

## Before the brief: privacy on the answers (14-16 Oct)

**0.1 Group threshold, session audit, differential privacy.** Before the data and the official brief arrived, nl2q
was a generic NL-to-SPARQL tool with a guard on the *answers*: a minimum group size, an audit of the queries in a
session (to catch small regions alone or in pairs), and on 16 Oct differential privacy on patient counts (off by
default).
- The brief (16 Oct) changed the task: deliver the query and a compliance verdict, judged on the verdict, the time to
  produce the query and the query's execution time. Nothing checks protected answers.
- **Dropped.** The repo was rebuilt from scratch on 16 Oct and the old one deleted. Privacy moved from the results to
  the query's shape, checked in code.

## Fri 16 Oct, evening: rebuild around the brief

**1.1 Two layers, one meeting point.** Translation (Michele) and compliance (Lorenzo) became two packages that meet
only in `pipeline.py`, through `generate`, `validate` and `judge` (PRs #1 and #2, 22:49-22:50). **On**, unchanged
since: it let three people and several agent sessions work in parallel with few conflicts.

**1.2 The generator translates literally; code decides.** The language model never refuses and never repairs a
request. The verdict comes from rules R1-R5 in `rules.py` (no model, no tokens, milliseconds). Retries may only fix
syntax and schema terms, never compliance, so a retry cannot turn a non-compliant request into a compliant query.
- First dry run (00:29): 24/26 verdicts, **0 false compliant** on 14 non-compliant requests, 2 false refusals (O01,
  O06), both translation errors.
- **On.** It held all the way: every wrong verdict since has been a translation error, not a rule error.

**1.3 Worked examples from official sources only** (Michele, 23:14 and 00:15). The bank holds the organizers'
examples, so evaluation runs with holdout, which hides a request's own example and its family. **On.** The catch,
found later: the 14 official bench cases are in the bank, so they are never truly unseen.

**1.4 d3 pre-screen on the request** (23:42). A small decision model (vllm-sr/d3) reads the request text before
generation and may only block, at 0.9 confidence. The organizers had just said a lightweight guardrail is a good idea
(Discord, 22:51).
- Bench: 0 false blocks, 9 of 14 non-compliant requests caught, about 0.28 s and 970 tokens per call. In the overnight
  grid it saved 14% of the tokens and changed no verdict.
- The policy examples were written after seeing the bench, so the numbers are optimistic.
- **Off by default, kept for the demo** (Lorenzo's decision, 17 Oct). A false block cannot be undone, while a miss
  costs nothing, because the rules still check the query. `not_aggregated` stays log-only.

**1.5 Mutation cases and stricter identifiers** (Priyanshi 00:54, Lorenzo 01:52). Hand-made variants of the official
queries, plus identifier shapes read from the data (`ID-` + 14 digits, UUIDs). **On.**

**1.6 Freeze a baseline.** Tag `baseline-v0` (03b4b7c, 02:07): 22/26 verdicts, false compliant N10, false refusals
O01 O06 O07, 3.27 s and 6,363 tokens per request, rows match 2/6. Every later change was reported against it.

## Sat 17 Oct, 02:44-07:30: the overnight run

Five experiments ran unattended in their own worktrees, under one orchestrating session.

**2.1 k-anonymity on compliant results** (exp 1). `NL2Q_KANON=flag` refuses a result with a group of 1-9 distinct
patients; `rewrite` adds a `HAVING` that drops those groups.
- At 5k, 18 of 48 labelled compliant queries release a group under 10. At 100k, 0 of the 10 measured do.
- The organizers called it innovative (Discord, 01:24 and 08:27), then placed it outside the judged system: it runs
  the query, so it is a dynamic check (voice, 17 Oct afternoon).
- **Off by default**, described in the report as the dynamic stage it would be.

**2.2 Fuzz the rules** (exp 2). 13 rewrites that keep a query's meaning, alone or chained: 154,831 variants.
- `main` had **162 false compliants** from two gaps: inverse paths (`?id ^sphn:hasIdentifier ?s`, 113) and variables
  named like rdflib's internal aggregates (`?__agg_N__`, 49).
- Both fixed, stricter only: 0 false compliants. 39 regression cases.
- **On**, merged at 10:33. 97 open findings are false refusals; fixing them would loosen the rules, so they stay.

**2.3 A 2024.2 + 2026.1 union schema** (exp 3). The data is SPHN 2024.2, the evaluation 2026.1, so a union config
would stop `validate()` from rejecting the data's own terms.
- End to end it changed nothing: 22/26 in 4 runs, byte-identical queries. The generator never writes 2024.2 terms,
  because the card and the examples are 2026.1.
- **Dropped** once the organizers confirmed 2026.1 (08:45).

**2.4 Settings grid and noise** (exp 4, 20 runs).
- The default scored 22/26 in 5 of 5 runs. Cold and warm runs follow two fixed paths that differ by 0-2 verdicts.
- `NL2Q_THINK=1`: 25/26 and N10 right in all 6 runs, but 14.8 s per request (max 93 s) against about 3 s.
  **Off**: latency is scored.
- `SHOTS=0`: 19/26, so **keep 4**. `SELECT_ORDER=0` made O04 take 34.6 s: **keep the pragma**.
- It pointed at the next target: grouping by a code node caused 29 of the 58 false refusals (see 6.3).

**2.5 Virtuoso at jury scale** (exp 5). All 100,000 patients, 611,719,746 triples, loaded into a second container on
:8990.
- Under concurrent clients, O01, O04 and O05 ran past 600 s. Idle, N03 took 2.3-2.7 s instead of ~300 s, so timings
  are only compared under the same conditions.
- **On** as the measurement base for every execution time since. A near miss: every kit script defaults to the
  shared 5k container, so the scripts must be run after `source env.sh`.

## Sat 17 Oct, morning: the organizers' answers and a review

At 08:45-09:02 the organizers answered the overnight questions: the evaluation runs on our 100k data, with a
10-minute timeout, on 2026.1 queries; "only the verdict and the times count". Execution time became a main target.

**3.1 Close three leaks found in a review** (11:26, merged 11:43). Three false compliants that the fuzzer could not
reach from its parent queries:
- grouping by a patient's DataRelease node through `dct:conformsTo`: the subject of any `sphn:` or `dct:` property is
  now a record;
- a terminology pin inside OPTIONAL, MINUS, NOT EXISTS or one UNION branch: it now counts only where it holds on
  every row;
- a pin inside a subquery that does not project the node: each query level now takes pins from its own patterns.

0 reasons changed on 259 labelled queries, 16 regression cases. **On.** The same morning: a file hash's algorithm is
a value, not a record (11:02, **On**).

## Sat 17 Oct, 11:35-18:30: making the queries fast

**4.1 A deterministic optimizer, from the mentor's paper.** The paper the organizers shared (Brites Marto et al.,
2025) gives Virtuoso rules: code triple first under the order pragma, inference only on the code triple, avoid
OPTIONAL and property paths. We measured them on :8990 first: code first 1.39 s against 0.01 s; per-triple
inference 0.14 s against 2.64 s for a global one.
- Passes `paths`, `reorder` and `pragma`, plus the `optbench` harness: 132 queries (generated, references, examples,
  the paper's), read-only, under a lock, after an idle check, rows compared by hash.
- **Off by default** (merged 17:20). It is the candidate to switch on for the evaluation, in schema mode.

**4.2 Static or dynamic knowledge for the optimizer.** The first `reorder` ranked patterns by the 100k data's counts.
Then the organizers asked, by voice, for a system that does not depend on the database it runs against.
- Split at 15:58: `schema` mode (SHACL 2026.1 cardinalities only) and `data` mode (the 100k counts).
- At 100k, cap 120 s: schema x1.83 on the 80 queries it changes, timeouts 28 to 18; data x2.74, timeouts 34 to 24.
  The data adds a third of the gain; the schema keeps the rest with nothing but the schema.
- **Schema mode Off** (with the optimizer), **data mode Off** by the organizers' rule.

**4.3 Qwen as the optimizer.** Lorenzo asked whether the model should do the rewriting. **Dropped:** a pass takes
0.3 ms against about 2 s for a model call, and only code can prove the rows stay the same.

**4.4 The `flag` pass** (15:50). An OPTIONAL used only through `BOUND` becomes `EXISTS`. It is not exact on purpose:
it fixes a double count. At 100k the generated O02 built 6.0M rows for one group against 153k with EXISTS, the
form of the organizers' reference, and went from the cap to 42-107 s. **Off** (not in the default passes);
approved as "fixes a double count", which became a gain once results were checked.

**4.5 Decorrelate EXISTS; drop or tighten OPTIONALs.** Every decorrelated form hit the 120 s cap in Virtuoso, slower
than the correlated one. No slow query matched the OPTIONAL cases. **Dropped.**

**4.6 Cost model and slow path** (14:25-14:30). A model of the rows Virtuoso visits, fitted on the optbench timings
(recall 0.82 at precision 0.66 for "slow"), and one more generator call for a compliant query predicted slow.
- The cost model passed its gate. The slow path triggered on 16 of 87 generated queries and kept 1 rewrite, which
  then failed at 100k: **0 useful rewrites in 16**.
- **Off**, and dynamic anyway (it reads the data's counts).

**4.7 Prefix cache** (14:17). vLLM reuses only whole 800-token blocks of an identical prompt head. Moving the core
schema blocks into SYSTEM and warming the head at startup took the cached share from 20.7% to 45.8% and prefill from
0.69 to 0.49 s. **Off**, offered to the translation layer as patches; worth turning on.

**4.8 Prompt hints for faster shapes** (14:31, 15:54). A static rule (`NL2Q_SHAPE_RULE`: OPTIONAL only for unmatched
rows, a flag is EXISTS) and data cues under each schema block (`NL2Q_CARD_STATS`: "in 48% of patients").
- Neither moved the model away from OPTIONAL or towards code first: OPTIONAL in 5 and 4 of 11 compliant queries,
  against 3 of 10 with neither. Both off that afternoon.
- **The shape rule was switched on at 20:28**, together with code classes (6.3): with both, O02 ran in 0.14 s
  instead of timing out. **The data cues stay Off** (dynamic).

**4.9 Scan the data against SHACL 2026.1** (16:32). 43 classes of SHACL 2026.1 never occur in the data, 15 data
properties are not in 2026.1 (`hasAdmissionDateTime`, ...), and 4 required properties are missing everywhere. This
explains many empty results and divisions by zero, and the organizers asked us to document it. **On**, in the report.

## Sat 17 Oct, 17:30: "We also check the results"

The organizers reversed the morning answer: a compliant query must also return the right rows. Translation accuracy
became the top risk.

**5.1 Translation audit** (merged 18:34). Michele had reported that only 2 of 14 generated queries return the
reference's results. The audit found it optimistic:
- **0 of 9** official compliant questions with holdout on; **0 of 216** generated queries over the 24 overnight runs.
- No mismatch came from formatting or a reference bug. The 2024.2/2026.1 gap blocks judging O05 and adds to O06 and
  O07, but each of those also has a real error.
- Four causes recur: the card lacks the record type, code nodes compared or grouped as codes, a value-set root used
  as a value, SQL syntax (`CASE WHEN`) that retries do not repair.
- With its own example visible, 3-4 of 9 become right: the bank teaches the answer, holdout hides it.
- **On** as tooling (`rowcheck`) and as the plan for the evening. Michele applied its prompt patch on his branch
  (19:58).

## Sat 17 Oct, evening: fixing the free translator

Three tracks ran at once: Lorenzo on the free generator, Priyanshi's branch on retrieval and a JSON route, Michele
on the plan path (8.3).

**6.1 Better card retrieval, synthetic patients, an A/B tool** (`priya/synthetic-data`, 18:44-19:05). The card found
the record types a request needs on 100% of the bench instead of 59%; synthetic patients and a local endpoint let us
run queries without the real data; `tools.ab` compares two runs request by request.
- A/B against `main` at 5k and 100k: the same answers (N01 and N02 only), the same F1 (0.40), 0.28 s faster.
- **Branch.** The retrieval gain changed nothing end to end.

**6.2 Schema linking and a code lookup** (19:52, `NL2Q_LINK`). BM25 over class definitions instead of block text, a
lookup of codes by label (built locally, never committed: licensed labels), and more precise `validate()` feedback
(a property the class lacks, SQL words, unbound variables).
- The card shows 92.4% of the classes the references need, against 73.9%. Same answer: still **0/9**.
- A check on the overnight traces showed why: card misses explain at most 5 of the 9 errors, and none alone; each
  also has a separate error in how the query is written.
- **On** since 20:24 (Lorenzo's call). A second step, letting the model choose the classes, was not started.

**6.3 Code nodes become their classes** (20:28, `NL2Q_CODE_CLASS`). The model grouped by or compared the code node
(one per record, its IRI a record ID) instead of the code. A static rewrite replaces the node with its class.
- Verdicts 25/26, only N10 wrong. 0 false compliants over 279 generated queries and the fuzz, leak and mutation
  sets. O02 ran in 0.14 s instead of timing out (with the shape rule).
- Answers still 0/9: it fixes verdicts, not rows.
- **On**, merged 20:45 (612b126).

**6.4 Medical synonyms for the card.** The last card misses are word gaps between a question and the SPHN
definitions (a disease named in plain words, a citizen for a nationality). Reuse the terminology synonyms already in
the code index. **Parked**: "only if spare time", never built.

**6.5 Rewrite the forms Virtuoso refuses** (21:43, `NL2Q_DIALECT`). 4 of the 9 official queries failed in Virtuoso,
not on the schema: `ROUND(x, 2)` (SP030) and `EXISTS {..} && EXISTS {..}` (SQ074). The parser's hint was also
misleading, and O07 spent its 3 tries on it.
- A static rewrite to forms Virtuoso accepts, before the check. **On.**
- The queries now run, but most return no rows: the errors left are about meaning.

**6.6 Three extra conventions in the prompt** (`NL2Q_RULES2`). O04 went from 4,952 to 0 rows in 2 of 2 runs. **Off.**

**6.7 JSON spec route** (`NL2Q_IR`, 21:06, `priya/synthetic-data`). The model fills a small JSON spec (records,
values, filters, grouping) and code writes the SPARQL; a request the spec cannot express goes to free generation.
25/26 verdicts with a stand-in generator on 300 synthetic patients. **Branch**: never measured with the real
generator.

## Sat 17 Oct, 21:00-23:40: repair after generation

**7.1 A static semantic verifier** (`lorenzo/verify`, `NL2Q_VERIFY`). Checks with stable codes (wrong RDF term, wrong
negation, property not on its class, ...), a typed path resolver over SHACL ranges, measurement and concept catalogs,
8 semantic rules, output-shape and cost gates. Each finding goes back to the generator once, as a hint.
- +1-2 verdicts, +1-2 s per request, +18-25% tokens. Correct answers 2/12 at 5k and 100k, the same as `main`.
- The first attempts were identical with and without it, so the gain could only come from repair, and repair did
  not fix meaning. Two regressions: O10 (a hint on the last try) and O04 (2x slower).
- **Branch.** Lesson: a check after generation cannot raise first-pass accuracy; only guidance before it can.

**7.2 Five ideas at once** (22:00-23:00). One orchestrated workflow, one worktree per idea, a gate of "same answer
above the baseline (0/9) in both runs, verdicts not worse, at most +2 s per request". The baseline ran with the code
lookup off: with it on, verdicts were 48 against 49 over two runs (a deterministic O01 refusal), and same answer was
0 either way.

| Idea | What it did | Result |
|---|---|---|
| verify hints (`try-verify`) | the verifier on the latest `main`, plus checks for a name compared with an IRI, a value used as a class, a negation written as `!=` | hints fired on 2 of 26; the negation hint turned O04 from empty into a timeout; 0/9 |
| path check (`NL2Q_PATH_CHECK`) | class feedback for untyped nodes, from property ranges | fired 0 times in 104 requests; 0/9 |
| drafts vote (`NL2Q_DRAFTS=3,5`) | n sampled drafts in one call, majority vote | 0/9 in all 4 runs, empty or error results not fewer; n=3 added a false refusal (N02: a three-way tie returned the refused draft) |
| adapt an example (`NL2Q_ADAPT`) | the nearest bank example as the starting query | the model follows it a little more (term overlap 0.19 to 0.24), but under holdout the nearest example shares few terms; 0/9 |
| empty probe (dev tool, never in the pipeline) | bisects a query's WHERE to find where it goes empty | found the causes: O01 and O02 go empty at `hasAdmission / hasDateTime` (2026.1, absent from the data), O04 compares the type of two Country nodes (always equal) instead of their codes |

**All five failed the gate**, so the planned "stack the winners" step never ran. They stay on their branches. The
probe's finding was the useful part: the errors are paths and values, and 9 questions are too few to measure a fix.

## Sun 18 Oct, 00:00-02:30: measure differently, then freeze

**8.1 A synthetic evaluation set grounded in the knowledge graph** (`kgsynth`, merged 01:42). We stopped tuning on
the 9 official questions and built the out-of-sample set the organizers asked for.
- Paths of 1-3 hops are sampled from the 2026.1 schema and the data scan; code compiles each into a gold query,
  checked on the 5k store; Apertus v1.5 8B writes the question from the triplets alone; Qwen translates it back.
- Items come in minimal pairs: A aggregate (compliant), B identifier exposed (non-compliant), C identifier used safely
  (compliant, tests false refusals). The label comes from the stratum, not from our checker.
- First 50 items, 3 runs: **150/150 verdicts**, 0 false compliant, 0 false refusal; **55%** return the gold's rows
  (numbers 83%, dates 38%, value-set individuals 36%).
- **Extended to 100 at 03:20** (`build --extend`, seed 1). The 50 new items sit on the 20 record classes not used yet,
  and the reviewed first half is unchanged. Lorenzo signed off on both halves.
  - 9 items were regenerated after review, and 2 had their operator switched because Apertus never named the
    identifiers.
  - K97 was kept although it is ambiguous by the schema's own wording ("fluid balance"): no item was dropped for
    scoring badly.
- 100 items, 3 runs: **300/300 verdicts**, 0 false compliant, 0 false refusal; **65%** same rows.
  - By leaf: numbers 83%, dates 70%, value-set individuals 35%.
  - 23 items are never right. The top cause is value-set individuals (9 items): the card lists them, yet the model
    invents a terminology code or a string match, and only 5 of 12 such items get it right in any run. Then sibling
    or similar classes (5) and the record's date used for a part's date (4).
- **On** as the evaluation set. Our own audit says the official mismatches are our translation errors (only O05 is
  blocked by the data), so the set is not presented as proof that the examples are impossible.

**8.2 Apertus 8B as the translator.** The organizers said Qwen is hard to justify to a hospital's legal team, and a
table of how well Apertus 8B writes the queries would be a plus. There is no Apertus 2.5; v1.5 8B (FP8) runs on our
Blackwell GPUs in the swiss-ai vLLM image after re-splitting the checkpoint. **Parked as a translator**; used to write
the synthetic questions, so the model that writes a question never translates it.

**8.3 The plan path** (Michele, 15:07 on Sat to the merge at 02:32 on Sun, `NL2Q_PLAN`). Three of the audit's four
error kinds are about how SPHN stores data, not about meaning. So a model writes only a short JSON plan of what is
asked, and a tested compiler writes the SPHN pattern: one sub-query per record, code first, a flag per record, then
the count.
- A Qwen3.5-9B LoRA, five versions (v21-v25) trained on synthetic (question, plan) pairs made by code (24,786 in v25).
- Official: 0/9 exact (the asked number right on 1). Easy set of 30 questions written blind: 15/29 exact, 15/18
  where the plan format knows the record types. Fast at 100k: O04 in 19 s against more than 10 minutes for the
  organizers' query.
- Tried and dropped in that track: the 9B writing SPARQL directly (it wrote DELETE or DESCRIBE), reasoning before the
  plan (6x tokens, worse), running the query and retrying when it is empty (not static, +0 correct), a vote over 5
  plans (they agree on the same wrong plan), a style hint line.
- **Off**: it needs the fine-tuned plan server. Full account: [report/translation.md](report/translation.md).

**The freeze.** The LaTeX report (01:59), the rewritten README (02:05) and the plan path (02:32) went into `main`.
Today's `main`: 25/26 verdicts (N10 false compliant), 2 of 12 compliant requests with the reference's rows (0 of the 9
official), 3.4 s and 8.1k tokens per request; synthetic set 150/150 verdicts and 55% same rows. After the freeze the
set grew to 100 items: 300/300 verdicts, 65% same rows. The plan path changes nothing by default (`NL2Q_PLAN` and
`NL2Q_GRAMMAR` off), so these numbers still describe `main`.

## What the story says
1. **Read the scoring, and read it again.** The brief removed the answer-side privacy work (0.1), and the 17:30
   answer moved the target from verdicts and times to correct rows. Each time, a day's plan changed.
2. **Code for the verdict held up.** On the bench, every wrong verdict since the first dry run came from the
   translation; the one persistent false compliant (N10) is a query that lost its `SERVICE` clause, judged right. Fuzzing (2.2) and a review (3.1)
   found leaks the tests had missed.
3. **Static rewrites beat prompt hints.** Code classes, the dialect rewrite and the flag pass each removed a whole
   class of errors; the data cues and most prompt rules moved little.
4. **Measure at the jury's scale, idle, cold against cold.** 5k timings, and timings under someone else's load,
   misled us by up to 100x.
5. **Retrieval was not the bottleneck; meaning is.** The card went from 74% to 92% recall and the answers did not
   move. Repair after generation (7.1, 7.2) did not move them either.
6. **Build the out-of-sample set first.** With 9 questions, one question is 11 points; the synthetic set came last
   and was the first measurement that could show a difference.
7. **The "static" rule pruned the data-driven ideas.** Each stayed as a switch (k-anonymity, data-mode optimizer,
   slow path, card cues), so the report can show what they would add.

## Still open
- **Correct results:** 0 of 9 official questions; 65% on the 100-item synthetic set. The next levers named by both
  tracks:
  - a static check that the object of a value-set property is one of its individuals (the card already lists them);
  - the missing record types (drug prescriptions first);
  - codes returned as full IRIs;
  - the organizers' answer conventions written down.
- **One failing test on `main` since 02:32:** `tests/translation/test_plan_values.py::test_the_resource_is_what_the_schema_says`.
  The committed `translation/resources/coded_values.json` differs from what the schema builds (ProblemCondition,
  RespiratoryRateMeasurement, ...). It only affects the plan path, which is off by default; Michele's layer.
- **N10:** the generator drops the remote `SERVICE`; reasoning (THINK) fixes it at about 4x the latency.
- **Switches to decide for the evaluation:** the optimizer in schema mode with `flag`, and the prefix-cache patches
  (both static, both measured as gains); d3 for the demo only.
- **Unmerged branches:** `lorenzo/verify`, `lorenzo/try-verify`, `lorenzo/path-check`, `lorenzo/multi-draft`,
  `lorenzo/adapt-example`, `lorenzo/empty-probe`, `priya/synthetic-data`.
- **Parked:** medical synonyms for the card (6.4), Apertus as the translator (8.2).
