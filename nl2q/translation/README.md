# Translation layer: question → SPARQL

This folder turns a researcher's question in plain English into a SPARQL query over SPHN 2026.1 data
(one named graph per patient). The compliance layer judges the query afterwards; this layer only translates.

Our three targets for every query: **correct** (it returns what was asked), **valid** (it parses and uses only
SPHN 2026.1 names) and **fast** (Virtuoso answers quickly).

## How it works

```
question ─► guard words? ── yes ─────────────────────────────────┐
   ▼ no                                                          │
9B writes a short JSON plan ─► checks ── fail twice ─────────────┤
   ▼ ok                                                          ▼
compiler (code) ─► SPARQL in the fast SPHN shape        free path: the model writes the SPARQL itself
```

1. **Guard words.** If the question asks for something no plan can say ("within 7 days", "per month", "bins",
   "median"), it goes straight to the free path.
2. **Plan.** A fine-tuned Qwen3.5-9B writes a short JSON plan: which records, which codes, which conditions,
   what to count or average, how to group.
3. **Checks.** Code checks the plan: real record types and attributes, allowed values, a record type the
   question mentions, unsafe parts that match the question. If a check fails, the model gets the reason and
   one more try.
4. **Compiler.** Code turns the plan into SPARQL, always in the same tested shape: one pattern per patient
   graph, the code first, no slow constructs.
5. **Free path.** For questions no plan can say, the model writes the SPARQL itself (only SPHN 2026.1 names are
   allowed by a grammar). This path is weaker: the plan path is preferred whenever it applies.

The model only decides *what* is asked. Code writes *how* it is asked. So when the plan is right, the query is
valid and fast by construction.

## What a plan looks like

"How many patients have cholera?"

```json
{"route": "plan",
 "records": {"dx": {"type": "BilledDiagnosis", "code": ["icd-10-gm:A00"]}},
 "output": [{"name": "patients", "agg": "patients"}]}
```

What a plan can say:
- **Counts** of patients or records, with codes, dates, values, units, ages, and/or/not, "A before B".
- **Groups and measures:** per code, sex, year, insurance or coded detail; average, min, max, sum, share,
  patients meeting a condition, order and limit.
- **Same hospital stay:** two records in one stay (`same_stay`, `stay_has`).
- **Time from A to B** and **what happens next** after a record (`gap`, `after`/`next`).
- **Compare two records** of a patient, for example birth country and nationality (`compare`).
- **Unsafe parts written literally** (one patient's graph, identifiers, other endpoints, ...), for the
  compliance layer to judge.

## How the 9B was trained

The training data is made by code, so every answer is right by construction:

```
code picks a plan ─► code writes a plain sentence ─► Qwen 27B rewords it ─► code rejects rewrites that changed it
```

- The plan comes first, built from the schema and from codes that exist in the data. The sentence is written
  from the plan, so the plan is exactly what the sentence means.
- The 27B only rewords: a rewrite that loses a code, number, date or condition is dropped.
- 18,350 examples: simple counts, groups and averages, coded details, unsafe parts, "cannot say" questions,
  the new shapes (stay, gap, compare, attributes) and look-alike pairs (hours vs days, count vs percent, ...).
- No benchmark question and no plan of one is in the training data.

## How we measure

`python -m nl2q.tools.query_check` runs our query and a trusted query on the same database and compares the
rows (correct), checks the query (valid) and times it (fast).

Results on the 5k-patient mock data, 17 Oct:

| | Old version (27B, examples in the prompt) | Plan version (9B) |
|---|---|---|
| Valid queries (14 official examples) | 13 of 14 | 14 of 14 |
| Same numbers as the organizers (14 official examples) | 1 of 14 | 3 of 14 (8 of 14 with hand-written right plans) |
| Queries over 30 s | 2 | 0 (slowest 29.6 s) |

Read these numbers with care: the 14 official examples are now practice questions (the new shapes were designed
from them), so they flatter us. The honest score comes from the sealed set (`nl2q_data/cleantest/`, never read
while building) and from new questions written by people.

## How to run it

Recommended setup: our plan first, the team's 27B as the fallback (the best of the measured setups: 97 exact on the
fresh, easy and synthetic sets, against 87 for the 27B alone and 80 for the plan with the 9B fallback; report
section 13).

1. Keep the team's 27B running as usual (`serving/serve_llm.sh`, :8001): it is the free-SPARQL fallback, main's
   default, so leave `OPENAI_BASE_URL` and `LLM_MODEL` unset.
2. Build the team's code index once, for the 27B's code look-up: `python -m nl2q.tools.codes` (writes
   `nl2q_data/codes.json`; terminology labels, never commit it).
3. Serve Qwen3.5-9B with the fine-tuned LoRA adapter (v26, in `/data/scratch/nw-data/shared/qwen35-9b-plan-v26/`)
   under the model name `plan` (multi-token prediction on, thinking off), then turn the plan path on:

```
vllm serve Qwen/Qwen3.5-9B --served-model-name base --port 8011 --max-model-len 16384 --language-model-only \
    --enable-lora --lora-modules plan=/data/scratch/nw-data/shared/qwen35-9b-plan-v26 --max-lora-rank 16 \
    --max-num-seqs 16 --default-chat-template-kwargs '{"enable_thinking": false}' \
    --speculative-config '{"method":"mtp","num_speculative_tokens":3}'
export NL2Q_PLAN=1 NL2Q_CODE_IRI=1 NL2Q_PLAN_URL=http://127.0.0.1:8011/v1 NL2Q_PLAN_MODEL=plan
```

Without the 27B (one GPU only), the 9B's base model can be the fallback instead: add
`NL2Q_GRAMMAR=1 OPENAI_BASE_URL=http://127.0.0.1:8011/v1 LLM_MODEL=base` (80 instead of 97 on the same sets).

Switches in `nl2q/settings.py` for this path:
- `NL2Q_PLAN` (off by default): the plan path. `NL2Q_GRAMMAR` (off): free SPARQL may only use SPHN 2026.1 names.
- `NL2Q_PLAN_URL`, `NL2Q_PLAN_MODEL`: the plan writer. If no fine-tuned adapter is served, leave `NL2Q_PLAN_MODEL`
  equal to `LLM_MODEL`: the generator then writes the plan from the instructions (`resources/plan.md`) and the
  `NL2Q_PLAN_SHOTS` (8) most similar stored plans (`resources/plans.jsonl`).

Removed on 17 Oct (all were off and measured worse): the run check and the code lookup (they queried the database
while answering), free SPARQL by the 9B, and voting over several sampled plans.

## For reviewers

While answering, the translation code is this folder (Python, about 2,300 lines), plus the shared `../schema.py` and
the SPARQL parser it borrows from `../compliance/` and `../optimize/`:
- plan path: `plan.py` (about 1,600 lines: plan format, checks, compiler), `generate.py` (about 90), `llm.py` (about 60);
- free path: `prompt.py` (about 290), `grammar.py` (about 10), `codeclass.py` and `dialect.py` (about 120 each);
- data read at start: `resources/` (plan instructions, stored plans, coded details and their allowed values, the
  generic record types and details of `schema_records.json`).

Offline, never called while answering:
- training data: `../training/` (`make.py` and its helpers; `shapes/*.py`: one generator per kind of question; the
  training set itself is not in the repo);
- resources: `../tools/coded_details.py` and `../tools/coded_values.py` rebuild `resources/coded*.json` from the schema;
  `../tools/schema_records.py` writes `resources/schema_records.json`: every other SPHN 2026.1 record type with data
  (after the 27 curated ones, alphabetical) and every schema property with data the curated tables lack, so a plan can
  name it (a number, date, code or value-set detail); curated entries always win. `NL2Q_CODE_IRI=1`: a breakdown by
  code returns the class IRI, as the organizers' queries do (ICD-10-GM and CHOP keep the short name);
- benchmark: `../tools/query_check.py` (correct, valid, fast against the trusted queries).

## Files

| File | What it does |
|---|---|
| `plan.py` | the plan format, the checks and the compiler |
| `generate.py` | the plan path first, then free generation with its retries |
| `prompt.py`, `grammar.py` | the free path's prompt and its SPARQL grammar |
| `resources/` | plan instructions, stored plans, coded details and their allowed values |
| `../training/` (`make.py`, helpers, `shapes/*.py`) | the training data generators |
| `../tools/query_check.py` | the benchmark: correct, valid, fast |

## Known limits

- The 9B still misreads long, multi-part questions (hours vs days, count vs percent, a missed part).
- Some questions need shapes the plan cannot say yet; they go to the free path.
- Some plans that start from lab tests are slow on large data; the "patients who also have X" shape is still slow
  for a few cases.
- The mock data is SPHN 2024.2 while queries are 2026.1: a 2026.1-only name returns nothing on it.
