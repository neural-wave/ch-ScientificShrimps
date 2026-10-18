"""Shared helpers: the inventory and the code pool, caches, the 5k-patient database, the 27B rewording (reword,
reword_items) and plain names, training rows and the output files."""
import hashlib, json, random, re, threading, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import requests
from .. import schema as SC
from .. import settings as S
from ..translation import plan as P
from .tables import (OUT, CACHE, INVENTORY, NW_CODES, ENDPOINT, LLM_MODEL, WORKERS, LLM_ROOT, TEMPLATE_SHARE,
    VAL_SHARE, DB_TIMEOUT, DATA, DATES, PROPER, STYLES, PAIRS, REWRITE, NAME_SCHEMA, CODE_SCHEMA)
from .filters import empty, problems
from .sample import weighted

# ---- inventory ------------------------------------------------------------------------------------------------------

def _code(iri_or_code):
    """'icd-10-gm:A00.9' from a code IRI (versioned or not) or a prefixed code."""
    s = str(iri_or_code).strip("<>")
    for p, ns in sorted(SC.PREFIXES.items(), key=lambda x: -len(x[1])):
        if s.startswith(ns):
            local = s[len(ns):]
            return f"{p}:{re.sub(r'^(19|20)\d\d/', '', local)}"
    return s


def _proper(code):
    system, _, local = code.partition(":")
    return bool(re.match(PROPER.get(system, r"^\w[\w.-]*$"), local))


def inventory():
    """{record type: {"patients": n or None, "codes": {code: patients}, "dates": (min, max)}}: the inventory of the
    5k-patient data (nl2q_data/inventory/inventory.json) when there is one; else the codes recorded in
    config/schema.json plus the code counts of the 100k files (no database query)."""
    inv = {t: {"patients": None, "codes": {}, "dates": DATES.get(t), "pairs": [], "data": set(DATA.get(t, "").split())}
           for t in P.CURATED}
    if INVENTORY.exists():
        return _read_inventory(json.loads(INVENTORY.read_text()), inv), "inventory"
    for t in P.CURATED:
        block = SC.CFG["blocks"].get(f"sphn:{t}", "")
        for line in block.splitlines():
            if "recorded:" in line and re.match(r"^    sphn:hasCode", line):
                for c in re.findall(r"(?:snomed|icd-10-gm|chop|loinc):[\w.-]+", line.split("recorded:", 1)[1]):
                    if _proper(c):
                        inv[t]["codes"].setdefault(c, 1)
    lab = SC.CFG["blocks"].get("sphn:LabTestEvent", "")
    if m := re.search(r"sphn:hasLabTest.*?\n\s+sphn:hasCode[^\n]*recorded: ([^\n]+)", lab, re.S):
        inv["LabTestEvent"]["codes"] = {c: 1 for c in re.findall(r"snomed:\d+", m.group(1))}
    drug = SC.CFG["blocks"].get("sphn:DrugAdministrationEvent", "")
    if m := re.search(r"sphn:hasActiveIngredient.*?\n\s+sphn:hasCode[^\n]*recorded: ([^\n]+)", drug, re.S):
        inv["DrugAdministrationEvent"]["codes"] = {c: 1 for c in re.findall(r"snomed:\d+", m.group(1))}
    if NW_CODES.exists():
        for line in NW_CODES.read_text().splitlines()[1:]:
            family, _, iri, _, patients = line.split("\t")[:5]
            code = _code(iri)
            if family in ("icd-10-gm", "chop") and _proper(code):
                for t in (("BilledDiagnosis", "OncologyDiagnosis") if family == "icd-10-gm" else ("BilledProcedure",)):
                    inv[t]["codes"][code] = inv[t]["codes"].get(code, 0) + int(patients) // 20  # 100k -> 5k
    return inv, "fallback (schema.json recorded codes + nw-data code counts)"


def _read_inventory(raw, inv):
    """From the inventory (nl2q_data/inventory/README.md): per record type, the codes on the plan's code path (patients
    per code, versions merged), the range of the plan's date property (else of the type's main date) and the (code,
    unit) value quantiles on the plan's value path."""
    for t, spec in P.CURATED.items():
        d = raw["record_types"].get(t)
        if not d:
            inv[t]["patients"] = 0
            continue
        inv[t]["patients"] = d.get("patients", 0)
        path = "/".join(x for x in (spec.get("via"), spec.get("code")) if x)
        for c in d.get("codes") or []:
            if spec.get("code") and c.get("path") == path:
                for x in c.get("top_by_patients") or []:
                    code = _code(x["iri"])
                    if _proper(code) and code.split(":")[0] in spec["systems"]:
                        inv[t]["codes"][code] = inv[t]["codes"].get(code, 0) + x.get("patients", 1)
        dates = {x["property"]: (x["min"][:10], x["max"][:10]) for x in d.get("dates") or [] if x.get("min")}
        inv[t]["dates"] = dates.get(spec.get("date")) or dates.get(d.get("main_date_property")) or inv[t]["dates"]
        value = "/".join(x for x in (spec.get("via"), spec.get("value")) if x) + "/sphn:hasValue"
        inv[t]["pairs"] = [(p.get("code_prefixed"), p["unit_prefixed"], p["p10"], p["median"], p["p90"], p["patients"])
                           for v in d.get("numeric_values") or [] if v.get("value_path") == value
                           for p in v.get("pairs") or [] if p.get("unit_prefixed")]
        # the attributes the plan's paths reach in this data
        inv[t]["data"] = {a for a, has in (("code", inv[t]["codes"]), ("date", spec.get("date") in dates),
                                           ("value", inv[t]["pairs"]), ("age", spec.get("age") and d.get("age"))) if has}
    return inv


def code_pool(inv, known=None):
    """{record type: [(code, weight)]}: the data's codes plus the ICD-10-GM categories and CHOP codes above them (a
    code matches its sub-codes), the most frequent first; with known, only the classes the loaded terminologies have
    (plan.unknown_codes would reject the others: e.g. unversioned CHOP leaf codes, recent SNOMED CT concepts)."""
    pool = {}
    for t, d in inv.items():
        codes = dict(d["codes"])
        for c, n in d["codes"].items():
            parent = c.split(".")[0] if c.startswith("icd-10-gm:") and "." in c else \
                c[:-1] if re.match(r"^chop:\d\d\.\d\d$", c) else None
            if parent:
                codes[parent] = codes.get(parent, 0) + n
        if known is not None:
            codes = {c: n for c, n in codes.items() if known.get(c)}
        if t not in ("AdministrativeSex",) and codes:
            pool[t] = sorted(((c, n) for c, n in codes.items() if n > 0), key=lambda x: -x[1])
    return pool


def known_codes(pool, cache):
    """{code: whether the loaded terminologies have its class} for every code of the pool (cached)."""
    todo = sorted({c for cs in pool.values() for c, _ in cs} - set(cache.data))
    for i in range(0, len(todo), 100):
        chunk = todo[i:i + 100]
        plan = {"route": "plan", "records": {f"r{j}": {"type": "BilledDiagnosis", "code": [c]} for j, c in enumerate(chunk)}}
        unknown = set(P.unknown_codes(plan))
        for c in chunk:
            cache.put(c, P._iri(c) not in unknown)
    return dict(cache.data)


# ---- caches and the 5k-patient database ------------------------------------------------------------------------------

class Cache:
    """A JSONL file of {"key", "value"}: what is already done, so a rerun skips it."""

    def __init__(self, path):
        self.path, self.data, self.lock = path, {}, threading.Lock()
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    d = json.loads(line)
                    self.data[d["key"]] = d["value"]
                except (ValueError, KeyError):
                    pass

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        with self.lock:
            self.data[key] = value
            with self.path.open("a") as f:
                f.write(json.dumps({"key": key, "value": value}) + "\n")


def sha(*parts):
    return hashlib.sha1("\x1f".join(map(str, parts)).encode()).hexdigest()[:16]


def run_db(query, cache):
    """{"ok", "rows" (the first rows), "n", "s"} of a safe SELECT on the 5k-patient Virtuoso (cached)."""
    key = sha(query)
    if (hit := cache.get(key)) is not None:
        return hit
    body = re.sub(r"(?im)^\s*(DEFINE|PREFIX)\b[^\n]*\n?", "", query).lstrip()
    assert body.startswith("SELECT"), "only SELECT queries run"
    t0 = time.time()
    try:
        r = requests.post(ENDPOINT, data={"query": query}, headers={"Accept": "application/sparql-results+json"},
                          timeout=DB_TIMEOUT)
        r.raise_for_status()
        j = r.json()
        cols = j["head"]["vars"]
        rows = [[b[c]["value"] if c in b else None for c in cols] for b in j["results"]["bindings"]]
        out = {"ok": time.time() - t0 < DB_TIMEOUT, "rows": rows[:3], "n": len(rows), "s": round(time.time() - t0, 2)}
    except Exception as e:  # timeout, HTTP error, bad JSON
        out = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}", "s": round(time.time() - t0, 2)}
    cache.put(key, out)
    return out


# ---- rewording --------------------------------------------------------------------------------------------------------

def chat(messages, schema, seed, temperature=0.8, max_tokens=400):
    """One answer of the generator (thinking off; JSON following schema, or free text when schema is None)."""
    body = {"model": LLM_MODEL, "messages": messages, "temperature": temperature, "top_p": 0.95, "max_tokens": max_tokens,
            "seed": seed, "chat_template_kwargs": {"enable_thinking": False}}
    if schema is not None:
        body["structured_outputs"] = {"json": schema}
    last = None
    for attempt in range(4):
        try:
            r = requests.post(LLM_ROOT + "/v1/chat/completions", json=body, timeout=300)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except (requests.RequestException, KeyError, ValueError) as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"generator: {last}")


def wake():
    """The generator sleeps when idle (serving/idle_sleep.py): wake it."""
    try:
        if requests.get(LLM_ROOT + "/is_sleeping", timeout=10).json().get("is_sleeping"):
            requests.post(LLM_ROOT + "/wake_up", timeout=600)
    except (requests.RequestException, ValueError):
        pass


def reword(item, cache, attempt=0, pairs=None):
    """Two rewrites of the item's template in two styles of pairs (default PAIRS), cached: [(style, text)]."""
    rng = random.Random(sha(item["id"], attempt))
    styles = weighted(rng, pairs or PAIRS)
    if rng.random() < 0.5:
        styles = styles[::-1]
    names = sorted(item["keep"]["names"])
    note = f"\nCondition names to keep (grammar may change): {'; '.join(names)}" if names else ""
    user = f"Original: {item['template']}{note}\nWrite two rewrites of the original:\n1. {STYLES[styles[0]]}\n" \
           f"2. {STYLES[styles[1]]}\nReply with JSON only: {{\"rewrites\": [\"...\", \"...\"]}}"
    key = sha("reword", LLM_MODEL, REWRITE, user, attempt)
    if (hit := cache.get(key)) is None:
        try:
            out = parse_rewrites(chat([{"role": "system", "content": REWRITE}, {"role": "user", "content": user}], None,
                                      seed=int(sha(item["id"], attempt), 16) % 2**31))
        except (RuntimeError, ValueError, KeyError, TypeError) as e:
            return [], f"generator error: {e}"
        hit = [str(x).strip() for x in out][:2]
        cache.put(key, hit)
    return list(zip(styles, hit)), None


def reword_items(items, cache, pairs=None, workers=WORKERS):
    """({id: [(style, text)]}, retried): two rewrites per item, a second try for an item with fewer than two good
    ones."""
    def run(todo, attempt):
        with ThreadPoolExecutor(workers) as ex:
            got = ex.map(lambda it: reword(it, cache, attempt, pairs)[0], todo)
            return {it["id"]: g for it, g in zip(todo, got)}
    out = run(items, 0)
    retry = [it for it in items if sum(not problems(x, it) for _, x in out[it["id"]]) < 2]
    for i, got in run(retry, 1).items():
        out[i] += got
    return out, len(retry)


def row(question, plan):
    """A training row for sft_lora.py: system plan.TRAINED, the question, the compact plan."""
    return {"messages": [{"role": "system", "content": P.TRAINED}, {"role": "user", "content": question},
                         {"role": "assistant", "content": json.dumps(plan, separators=(",", ":"))}]}


def parse_rewrites(text):
    """The two rewrites of an answer: its JSON object (plain decoding: a JSON grammar let the model pad with
    whitespace until the token limit)."""
    m = re.search(r"\{.*\}", text, re.S)
    out = json.loads(m.group(0))["rewrites"] if m else None
    if not isinstance(out, list) or len(out) < 2 or not all(isinstance(x, str) for x in out):
        raise ValueError("no two rewrites")
    return out


# ---- plain names --------------------------------------------------------------------------------------------------

def plain_names(codes, cache):
    """{icd-10-gm code: plain English name} for the codes the generator names and finds again from that name (round
    trip): the only codes a template may write in words. Names stay in the cache, never printed."""
    def one(code):
        key = sha("name-v2", LLM_MODEL, code)
        if (v := cache.get(key)) is None:
            local = code.split(":", 1)[1]
            try:
                a = json.loads(chat([{"role": "user", "content": f"What does ICD-10-GM code {local} denote? Give the everyday "
                                      "name a physician would use in conversation: lower case, 1 to 4 words, no qualifier "
                                      "such as unspecified or other, no code. Set known to false unless you are certain."}],
                                    NAME_SCHEMA, 0, 0.0, 60))
                v = {"name": a["name"].strip(), "known": bool(a["known"]), "back": None}
                if v["known"] and v["name"]:
                    b = json.loads(chat([{"role": "user", "content": f"Which ICD-10-GM code denotes: {v['name']}? "
                                          "Answer with the code only."}], CODE_SCHEMA, 0, 0.0, 20))
                    v["back"] = b["code"].strip().upper().removeprefix("ICD-10-GM").strip(" :")
            except (RuntimeError, ValueError, KeyError) as e:
                return code, {"known": False, "error": str(e)[:80]}
            cache.put(key, v)
        return code, v
    with ThreadPoolExecutor(WORKERS) as ex:
        found = dict(ex.map(one, codes))
    return {c: v["name"] for c, v in found.items() if v.get("known") and v.get("back") == c.split(":", 1)[1]}


# ---- output files ----------------------------------------------------------------------------------------------------

def held_out():
    """The benchmark's and the worked examples' questions: no training question may resemble one."""
    bench_q = [json.loads(line)["question"] for f in sorted((S.HERE.parent / "tests" / "bench").glob("*.jsonl"))
               for line in f.read_text().splitlines() if line.strip()]
    return bench_q + re.findall(r"(?m)^Q: (.+)$", S.EXAMPLES.read_text() if S.EXAMPLES.exists() else "")


def write(items, rewrites, rejected, args, seconds, source, n_names):
    """train.jsonl, val.jsonl, stats.md, samples.md (with --v2: train_v2.jsonl, ...)."""
    from ..translation.prompt import _same
    rng, sfx, held = random.Random(args.seed + 1), ("_v21" if args.v21 else "_v2" if args.v2 else "") + \
        (f"_{args.tag}" if args.tag else ""), held_out()
    lines, rw = {"train": [], "val": []}, Counter()
    reasons = Counter()
    for it in items:
        if it.get("rejected"):
            continue
        accepted, seen = [], set()
        for style, text in rewrites.get(it["id"], []):
            bad = problems(text, it)
            rw["rejected" if bad else "accepted"] += 1
            for b in bad:
                reasons[b.split(":")[0]] += 1
            if not bad and text.lower() not in seen and text != it["template"] and len(accepted) < 2:
                seen.add(text.lower())
                accepted.append((style, text))
        if not accepted or rng.random() < TEMPLATE_SHARE:
            accepted.append(("template", it["template"]))
        near = [q for q in accepted if any(_same(q[1], h) for h in held)]
        reasons["like a benchmark or examples.md question"] += len(near)
        accepted = [q for q in accepted if q not in near]
        it["questions"] = accepted
        if not accepted:
            continue
        split = "val" if int(sha("split", it["id"]), 16) % 100 < VAL_SHARE * 100 else "train"
        db = it.get("db") or {}
        meta = {"plan_id": it["id"], "kind": it["kind"], **it["meta"], "rows": db.get("rows", [None])[:1] if db else None,
                "empty": empty(db) if db.get("ok") and not db.get("skipped") else None}
        for style, text in accepted:
            lines[split].append(row(text, it["plan"]) | {"meta": meta | {"question": style}})
    for split, rows in lines.items():
        rng.shuffle(rows)
        (OUT / f"{split}{sfx}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    kept = [it for it in items if not it.get("rejected") and it.get("questions")]
    stats = ["# Training data: statistics", "",
             f"`python -m nl2q.training.make --plans {args.plans} --seed {args.seed}{' --v2' * args.v2}{' --v21' * args.v21}` in {seconds / 60:.0f} min; "
             f"codes from {source}; {n_names} ICD-10-GM codes with a plain name (round trip).", "",
             f"- plans sampled: {len(items)}, kept: {len(kept)}, rejected: {len(items) - len(kept)}",
             f"- lines: train {len(lines['train'])}, val {len(lines['val'])}",
             f"- rewrites: {rw['accepted']} accepted, {rw['rejected']} rejected", ""]
    table = lambda title, c: [f"## {title}", "", "| | n |", "|---|---|", *[f"| {k} | {v} |" for k, v in c.most_common()], ""]
    stats += table("Rejected plans", rejected)
    stats += table("Kept plans by kind", Counter(it["kind"] for it in kept))
    stats += table("Kept plans by shape", Counter(it["meta"]["shape"] for it in kept))
    stats += table("Unsafe kinds (kept plans)", Counter(k for it in kept for k in it["meta"].get("unsafe", [])))
    stats += table("Free kinds (kept plans)", Counter(it["meta"]["free"] for it in kept if it["kind"] == "free"))
    stats += table("Record types (kept plans)", Counter(t for it in kept for t in it["meta"]["types"]))
    safe = [it for it in kept if it["kind"] == "safe" and it.get("db", {}).get("ok") and not it["db"].get("skipped")]
    stats += [f"Safe plans run: {len(safe)}, empty: {sum(empty(it['db']) for it in safe)}, mean "
              f"{sum(it['db']['s'] for it in safe) / max(1, len(safe)):.2f} s per query.", ""]
    stats += table("Rewrite rejections", reasons)
    stats += table("Questions by source", Counter(s for it in kept for s, _ in it.get("questions", [])))
    (OUT / f"stats{sfx}.md").write_text("\n".join(stats))
    pick = rng.sample(kept, min(30, len(kept)))
    sample_lines = ["# Training data: 30 random pairs", ""]
    for it in pick:
        style, text = rng.choice(it["questions"])
        sample_lines += [f"## {it['id']} ({it['kind']}, {it['meta']['shape']}"
                         f"{', ' + '+'.join(it['meta'].get('unsafe', [])) if it['meta'].get('unsafe') else ''}; {style})",
                         "", f"Q: {text}", "", f"Template: {it['template']}", "", "```json",
                         json.dumps(it["plan"], separators=(",", ":")), "```", ""]
    (OUT / f"samples{sfx}.md").write_text("\n".join(sample_lines))
    (CACHE / f"items{sfx}.json").write_text(json.dumps([{k: v for k, v in it.items() if k != "query"} for it in items]))
    print(f"{OUT}: train {len(lines['train'])}, val {len(lines['val'])} lines from {len(kept)} plans")
