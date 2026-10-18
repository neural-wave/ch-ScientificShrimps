"""Training data for a model that writes plans (translation/plan.py): code makes every plan and so every answer; a
model only rewords the questions.
  python -m nl2q.training.make --plans 2000 --seed 0
1. sample: valid plans over the whole v1 language (one record with code, value and unit, age and date conditions;
   and / or / not / has-but-not / before with and without gap; record counts with first and last dates), about 30%
   with unsafe parts (every field of `unsafe`) and 15% requests a plan cannot say ({"route": "free"}). Codes, units
   and date ranges come from the inventory of the 5k-patient data; plans the benchmark (tests/bench) intends are
   never sampled.
2. verify: check() and compile(); the query (finish) parses and the compliance rules find it compliant when safe and
   not compliant when unsafe; safe plans run on the 5k-patient Virtuoso (SELECT only, 60 s), at most 30% empty.
   Unsafe plans are never run.
3. state: a fixed template sentence per plan (codes as "ICD-10-GM code E11", values with units, every unsafe part).
4. reword: the generator rewrites each template in two styles; a rewrite that loses or adds a code, number, date,
   unit, URL, graph name, identifier or unsafe cue is rejected. The template itself is a third question for ~20%.
Writes nl2q_data/training/ (gitignored): train.jsonl and val.jsonl (rows for sft_lora.py: system plan.TRAINED, the
question, the compact plan, plus meta; 5% of the plans in val), stats.md, samples.md, and cache/ (database results,
rewrites, plain names), so a rerun repeats no query and no model call.
  --v2   analytic plans over one record type (group, avg/min/max/sum, share, patients_if): train_v2.jsonl, ...
  --v21  plans on coded details (resources/coded.json: group by one, filter on one, share or patients_if of a value,
         patients_if of another record): train_v21.jsonl, ...
Both keep empty answers: 2026.1 terms (hasRankCode, hasAdmission, ...) match nothing in the 2024.2 mock data."""
import argparse, random, re, threading, time
from collections import Counter
# make.<name>: every name of the package, the underscore ones too (the shape generators use M.<name>)
from .tables import *
from .filters import *
from .wording import *
from .sample import *
from .analytic import *
from .common import *
from .filters import _num
from .sample import _value, _date_in
from .common import _code, _proper, _read_inventory

# ---- the run ----------------------------------------------------------------------------------------------------------

def sample(n, seed, inv, pool, names, v2=False, v21=None):
    """n items {id, kind, plan, template, keep, meta}: safe, unsafe and free by SHARES (v2: safe analytic plans only;
    v21, the coded details' values: safe plans on coded details only), no plan twice, none of the benchmark's."""
    rng = random.Random(seed)
    want = {k: round(n * s) for k, s in SHARES} if not (v2 or v21) else {"safe": n}
    want["safe"] += n - sum(want.values())
    items, seen, tries = [], set(), 0
    safe = (lambda *a: sample_v21(*a, v21)) if v21 else sample_v2 if v2 else sample_safe
    makers = {"safe": safe, "unsafe": sample_unsafe, "free": sample_free}
    for kind, count in want.items():
        made = 0
        while made < count and tries < n * 50:
            tries += 1
            plan, q, keep, meta = makers[kind](rng, inv, pool, names)
            canon = P._canonical(plan) if plan["route"] == "plan" else q
            if canon in seen or plan["route"] == "plan" and bench(plan):
                continue
            seen.add(canon)
            items.append({"id": f"{kind[0]}{len(items):05d}", "kind": kind, "plan": plan, "template": q,
                          "keep": keep.dump(), "meta": meta})
            made += 1
    return items


def run_database(items, cache, log):
    """Run every safe plan once (cached), one query at a time."""
    todo = [it for it in items if it["kind"] == "safe" and it.get("query")]
    t0 = time.time()
    for i, it in enumerate(todo):
        it["db"] = run_db(it["query"], cache)
        if (i + 1) % 100 == 0:
            log(f"database: {i + 1}/{len(todo)} in {time.time() - t0:.0f} s")
    return todo


def keep_database(items, rng, max_empty=MAX_EMPTY):
    """Drop the safe plans that failed on the database, and empty ones beyond MAX_EMPTY of the safe plans."""
    dropped = Counter()
    safe = [it for it in items if it["kind"] == "safe"]
    for it in safe:
        if not it["db"]["ok"]:
            it["rejected"] = "database: " + it["db"].get("error", "over the time limit")[:60]
            dropped["database error or timeout"] += 1
    ok = [it for it in safe if not it.get("rejected")]
    zero = [it for it in ok if empty(it["db"])]
    cap = len(zero) if max_empty >= 1 else int(max_empty * (len(ok) - len(zero)) / (1 - max_empty))
    rng.shuffle(zero)
    for it in zero[cap:]:
        it["rejected"] = "empty beyond the 30% cap"
        dropped["empty beyond the 30% cap"] += 1
    return dropped


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plans", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-llm", action="store_true", help="templates only (no rewording)")
    ap.add_argument("--no-db", action="store_true", help="do not run the safe plans")
    ap.add_argument("--style", choices=("mixed", "organizers"), default="mixed", help="organizers: every question "
                    "also in the organizers' voice (PAIRS_ORGANIZERS)")
    ap.add_argument("--tag", default="", help="suffix of the output files (train_v2_<tag>.jsonl, ...)")
    ap.add_argument("--v2", action="store_true", help="v2 analytic plans (one record type) into train_v2.jsonl, "
                    "val_v2.jsonl, stats_v2.md, samples_v2.md; empty answers kept (2026.1 attributes the mock data lacks)")
    ap.add_argument("--v21", action="store_true", help="v2.1 plans on coded details (resources/coded.json) into "
                    "train_v21.jsonl, val_v21.jsonl, stats_v21.md, samples_v21.md; empty answers kept")
    args = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    log = lambda m: print(f"[{time.time() - t_start:6.0f} s] {m}", flush=True)
    inv, source = inventory()
    pool = code_pool(inv)
    pool = code_pool(inv, known_codes(pool, Cache(CACHE / "known_codes.jsonl")))
    log(f"codes from {source}: " + ", ".join(f"{t} {len(c)}" for t, c in pool.items()))
    llm_cache, db_cache = Cache(CACHE / "rewrites.jsonl"), Cache(CACHE / "database.jsonl")
    names = {}
    if not args.no_llm:
        wake()
        icd = sorted({c for t in ("BilledDiagnosis", "OncologyDiagnosis") for c, _ in pool.get(t, [])[:80]})
        names = plain_names(icd, Cache(CACHE / "names.jsonl"))
        log(f"plain names: {len(names)} of {len(icd)} ICD-10-GM codes pass the round trip")
    dvals = None
    if args.v21:
        everything = detail_values({})
        known = known_codes({"details": [(c, 1) for vs in everything.values() for c, _ in vs if ":" in c]},
                            Cache(CACHE / "known_codes.jsonl"))
        dvals = detail_values(known)
        log("coded details: " + ", ".join(f"{t}.{a} {len(vs)}" for (t, a), vs in dvals.items()))
    items = sample(args.plans, args.seed, inv, pool, names, v2=args.v2, v21=dvals)
    if args.v2 or args.v21:
        for it in items:
            it["id"] = ("w" if args.v21 else "v") + it["id"]
    log(f"sampled {len(items)}: " + ", ".join(f"{k} {v}" for k, v in Counter(it['kind'] for it in items).items()))
    rejected = Counter()
    for it in items:
        if why := verify(it):
            it["rejected"] = why
            rejected[re.sub(r"[:'\"].*", "", why)[:60]] += 1
    log(f"static checks rejected {sum(rejected.values())}: {dict(rejected)}")
    alive = [it for it in items if not it.get("rejected")]
    db_thread = None
    if not args.no_db:
        db_thread = threading.Thread(target=run_database, args=(alive, db_cache, log))
        db_thread.start()
    rewrites = {}
    if not args.no_llm:
        t0 = time.time()
        rewrites, retried = reword_items(alive, llm_cache, PAIRS_ORGANIZERS if args.style == "organizers" else None)
        log(f"rewording: {len(alive)} plans, {retried} retried, {time.time() - t0:.0f} s")
    if db_thread:
        db_thread.join()
        rejected.update(keep_database(alive, random.Random(args.seed), 1.0 if args.v2 or args.v21 else MAX_EMPTY))
    else:
        for it in alive:
            it.setdefault("db", {"ok": True, "rows": [], "skipped": True})
    write(items, rewrites, rejected, args, time.time() - t_start, source, len(names))


if __name__ == "__main__":
    main()
