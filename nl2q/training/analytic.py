"""The v2 and v2.1 plan samplers: analytic plans over one record type (group, avg/min/max/sum, share,
patients_if) and plans on coded details (resources/coded.json)."""
import json, math, re
from ..translation import plan as P
from .tables import (INVENTORY, TYPES, NO_VALUE, SYSTEMS, LAB_VALUES, VALUES, AGES, CUE_OPS, VNOUN, MAIN_WORD, CUE_V2,
    DETAILS, V21_TYPES, SYSNAME, SYSCUE, OUTCOME, CONSEQUENCES, ZYGOSITY)
from .filters import _num, merge, v2_bench, v21_bench
from .wording import describe, count_question
from .sample import _value, weighted, record, pick_types

# ---- v2: analytic plans over one record type (group, avg/min/max/sum, share, patients_if, order, limit) -------------

def v2_attrs(t):
    """The numbers of a record type that avg/min/max/sum may take."""
    spec = P.CURATED[t]
    return [a for a in ("value", "age", "hours", "days") if P.has_alias(spec, a) and not (a == "value" and t in NO_VALUE)]


def sample_v2(rng, inv, pool, names):
    """A v2 plan over one record type and its template: (plan, question, Keep, meta)."""
    form = weighted(rng, [("by_code", 20), ("by_sex", 12), ("by_year", 13), ("avg", 20), ("minmax", 7), ("sum", 4),
                          ("share", 15), ("patients_if", 9)])
    eligible = {"by_code": [t for t in pool if t not in ("BilledProcedure",)] + ["DrugAdministrationEvent"],
                "by_year": [t for t in P.CURATED if P.CURATED[t].get("date")],
                "share": [t for t in P.CURATED if v2_attrs(t) or P.CURATED[t].get("rank") or P.has_alias(P.CURATED[t], "in_stay")],
                "patients_if": [t for t in P.CURATED if v2_attrs(t) or P.CURATED[t].get("rank")]}.get(
        form, [t for t in P.CURATED if v2_attrs(t)] if form in ("avg", "minmax", "sum") else list(P.CURATED))
    if form == "sum":
        eligible = [t for t in eligible if {"hours", "days"} & set(v2_attrs(t))]
    t = pick_types(rng, inv, 1, exclude=[x for x in P.CURATED if x not in eligible or x == "AdministrativeSex"])[0]
    v, spec_t = TYPES[t][0], P.CURATED[t]
    spec, words, keep = record(rng, inv, pool, names, t, v, cond_p=0.3, code_p=0.5 if form != "by_code" else 0.0)
    plural, single, obj = describe(rng, t, words, plural=True), describe(rng, t, words, article=False), describe(rng, t, words)
    plan = {"route": "plan", "records": {v: spec}}
    meta = {"shape": f"v2 {form}", "types": [t]}
    out, group, q = [], [], None
    if form == "by_code":
        route = t == "DrugAdministrationEvent" and rng.random() < 0.3
        if route:
            if not spec.get("code") and not spec.get("where"):
                spec["code"] = [weighted(rng, [(c, math.sqrt(n) + 1) for c, n in pool[t][:60]])]
                keep.exact.add(spec["code"][0].split(":")[1])
                plural = f"{TYPES[t][2]} with SNOMED CT code {spec['code'][0].split(':')[1]}" + (f" {words[1]}" if words[1] else "")
            group = [{"code_class": f"{v}.route", "ns": "snomed"}]
            keep.cue("route", r"route|way|how")
            what = "per administration route"
        else:
            ns = pool[t][0][0].split(":")[0]
            group, what = [{"code_class": v, "ns": ns}], f"per {SYSTEMS[ns]} code"
            if ns == "icd-10-gm" and rng.random() < 0.35:
                parent = rng.choice(sorted({c.split(":")[1][:3] for c, _ in pool[t]}))
                if rng.random() < 0.5:
                    group[0]["match"] = f"^{parent[0]}"
                    plural, what = f"{TYPES[t][2]} with ICD-10-GM codes starting with {parent[0]}", f"per ICD-10-GM code"
                    keep.exact.add(parent[0]) if len(parent[0]) > 1 else keep.cue("letter", rf"\b{parent[0]}\b|{parent[0]}[ -]?codes|'{parent[0]}'|\"{parent[0]}\"")
                else:
                    spec["code"], group[0]["match"] = [f"icd-10-gm:{parent}"], f"^{parent}"
                    plural, what = f"{TYPES[t][2]} under ICD-10-GM code {parent}", "per sub-code"
                    keep.exact.add(parent)
                keep.cue("code", r"code|sub-?code|categor")
        aggs = rng.choice((["patients"], ["patients"], ["records"], ["patients", "records"]))
        top = rng.random() < 0.6
        n = rng.choice((3, 5, 10, 20)) if top and rng.random() < 0.7 else None
        meta["shape"] += " route" if route else ""
        counts = " and ".join({"patients": "the number of patients", "records": f"the number of {TYPES[t][2]}"}[a] for a in aggs)
        q = (f"Which are the {n} most frequent " if n else "What are the most frequent " if top else "") + \
            (f"administration routes of {plural}? Give {counts} for each." if route and top else
             f"codes of {plural}? Give {counts} per code." if top else
             f"For {plural}, give {counts} {what}." if rng.random() < 0.5 else f"How many {'patients' if aggs[0] == 'patients' else TYPES[t][2]} are there {what} among {plural}?")
        if q.startswith("How many records"):
            q = f"For {plural}, give {counts} {what}."
        out = [{"name": "patients", "agg": "patients"} if a == "patients" else {"name": f"{v}_records", "agg": "records", "of": v}
               for a in aggs]
        if top:
            plan_order = [{"by": out[0]["name"], "desc": True}]
            keep.cue("most", r"most|top|frequent|common|highest|largest|rank|order|sort")
            plan["order"] = plan_order
            if n:
                plan["limit"] = n
                keep.number(n)
        keep.cue("per code", CUE_V2["code"] if not route else r"route")
    elif form in ("by_sex", "by_year"):
        dim = "sex" if form == "by_sex" else "year"
        if dim == "year" and not P.has_alias(spec_t, "date"):
            return sample_v2(rng, inv, pool, names)
        group = [{"sex": True}] if dim == "sex" else [{"year": f"{v}.date"}]
        agg = rng.choice(("patients", "records"))
        out = [{"name": "patients", "agg": "patients"} if agg == "patients" else {"name": f"{v}_records", "agg": "records", "of": v}]
        if dim == "sex":
            q = rng.choice((f"How many patients have {obj}, by administrative sex?", f"Break down the patients with {obj} by sex.")) \
                if agg == "patients" else f"How many {plural} are there per administrative sex?"
        else:
            dv = TYPES[t][3] or "recorded"
            q = rng.choice((f"How many {plural} were {dv} per year?", f"Count the {plural} per calendar year.")) \
                if agg == "records" else f"How many patients had {obj}, per year?"
            if rng.random() < 0.3:
                n = rng.choice((3, 5))
                plan["order"], plan["limit"] = [{"by": out[0]["name"], "desc": True}], n
                q = f"Which {n} years had the most {plural if agg == 'records' else 'patients with ' + obj}?"
                keep.number(n)
                keep.cue("most", r"most|top|highest|largest")
        keep.cue(dim, CUE_V2[dim])
    elif form in ("avg", "minmax", "sum"):
        attrs = v2_attrs(t) if form != "sum" else [a for a in v2_attrs(t) if a in ("hours", "days")]
        a = rng.choice(attrs)
        thing = {"value": f"{VNOUN.get(t, 'value')}", "age": "patient age", "hours": "duration in hours",
                 "days": "duration in days"}[a]
        rnd = form == "avg" and rng.random() < 0.4
        if form == "avg":
            out = [{"name": f"avg_{a}", "agg": "avg", "of": f"{v}.{a}", **({"round": True} if rnd else {})}]
            q = rng.choice((f"What is the average {thing} of {plural}", f"What is the mean {thing} across {plural}"))
            keep.cue("avg", CUE_V2["avg"])
        elif form == "minmax":
            which = rng.choice((["min", "max"], ["min"], ["max"]))
            out = [{"name": f"{m}_{a}", "agg": m, "of": f"{v}.{a}"} for m in which]
            q = {"min": f"What is the lowest {thing} of {plural}", "max": f"What is the highest {thing} of {plural}"}[which[0]] \
                if len(which) == 1 else f"What are the lowest and the highest {thing} of {plural}"
            for m in which:
                keep.cue(m, CUE_V2[m])
        else:
            out = [{"name": f"total_{a}", "agg": "sum", "of": f"{v}.{a}"}]
            q = f"What is the total {thing} of all {plural}"
            keep.cue("sum", CUE_V2["sum"])
        q += ", rounded to a whole number" if rnd else ""
        if rnd:
            keep.cue("round", r"round|whole|integer|nearest")
        if rng.random() < 0.3:
            dim = rng.choice(("sex", "year") if P.has_alias(spec_t, "date") else ("sex",))
            group = [{"sex": True}] if dim == "sex" else [{"year": f"{v}.date"}]
            q += ", by administrative sex" if dim == "sex" else ", per year"
            keep.cue(dim, CUE_V2[dim])
        q += "?"
    else:  # share, patients_if: a condition on one attribute
        kinds = ([("main", 3)] if spec_t.get("rank") else []) + ([("in_stay", 2)] if P.has_alias(spec_t, "in_stay") else []) + \
                [(a, 2) for a in v2_attrs(t) if a in ("value", "age") or form == "share"]
        if not kinds:
            return sample_v2(rng, inv, pool, names)
        kind = weighted(rng, kinds)
        if kind == "main":
            test, words_if = {"attr": f"{v}.rank", "op": "=", "value": "main"}, f"{MAIN_WORD[t]}"
            keep.cue("main", CUE_V2["main"])
        elif kind == "in_stay":
            test, words_if = {"attr": f"{v}.in_stay", "op": "=", "value": True}, "started during the patient's hospital stay"
            keep.cue("in_stay", CUE_V2["in_stay"])
        else:
            op = rng.choice((">", ">=", "<"))
            x = {"value": rng.choice(VALUES.get(t, (LAB_VALUES, None))[0]), "age": rng.choice(AGES),
                 "hours": rng.choice((1, 2, 6, 12, 24, 48)), "days": rng.choice((1, 2, 3, 7, 14, 30))}[kind]
            test = {"attr": f"{v}.{kind}", "op": op, "value": _value(x)}
            noun_if = {"value": f"a {VNOUN.get(t, 'value')}", "age": "a patient age", "hours": "a duration", "days": "a duration"}[kind]
            unit = {"hours": " hours", "days": " days"}.get(kind, "")
            words_if = f"with {noun_if} {({'>': 'above', '>=': 'of at least', '<': 'below'})[op]} {_num(x)}{unit}"
            keep.number(x)
            keep.cue(f"if {op}", CUE_OPS[op] + (r"|older" if kind == "age" and op != "<" else r"|younger" if kind == "age" else ""))
        if form == "share":
            name = {"main": "main_pct", "in_stay": "in_stay_pct"}.get(kind, f"{kind}_pct")
            out = [{"name": name, "agg": "share", "if": test}]
            q = rng.choice((f"What percentage of {plural} are {words_if}?" if kind == "main" else
                            f"What percentage of {plural} {'were ' if kind == 'in_stay' else 'come '}{words_if}?",
                            f"What share of {plural} {'are' if kind == 'main' else 'were' if kind == 'in_stay' else 'come'} {words_if}?"))
            keep.cue("share", CUE_V2["share"])
            if rng.random() < 0.25 and P.has_alias(spec_t, "date"):
                group = [{"year": f"{v}.date"}]
                q = q[:-1] + ", per year?"
                keep.cue("year", CUE_V2["year"])
        else:
            out = [{"name": "patients", "agg": "patients"},
                   {"name": {"main": "patients_main", "in_stay": "patients_in_stay"}.get(kind, f"patients_{kind}"),
                    "agg": "patients_if", "if": test}]
            q = f"How many patients have {obj}, and how many of them have one " + \
                (f"that is {words_if}?" if kind == "main" else f"{'that ' + words_if if kind == 'in_stay' else words_if}?")
    if group:
        plan["group"] = group
    plan["output"] = out
    plan = {k: plan[k] for k in ("route", "records", "group", "output", "order", "limit") if k in plan}
    if v2_bench(plan):
        return sample_v2(rng, inv, pool, names)
    return plan, q, keep, meta


# ---- v2.1: coded details (resources/coded.json): grouped by, filtered on, shares, patients_if on a detail value or on
# another record -------------------------------------------------------------------------------------------------------

def detail_kind(t, a):
    return "code" if a in ("rank", "route") else P.coded(t)[a][1]


def detail_values(known):
    """{(record type, detail): [(value, patients)]}: the codes (classes of the loaded terminologies) and value-set
    individuals the 5k-patient data holds for each coded detail (rank: "main"; its 2026.1 path is not in the data)."""
    raw = json.loads(INVENTORY.read_text())["record_types"] if INVENTORY.exists() else {}
    out = {}
    for t, a in DETAILS:
        if a == "rank":
            out[(t, a)] = [("main", 1)]
            continue
        prop, d, vals = P.CURATED[t][a] if a == "route" else P.coded(t)[a][0], raw.get(t) or {}, []
        for c in d.get("codes") or []:
            if c["path"] == prop and detail_kind(t, a) == "code":
                vals = [(x["prefixed"], x["patients"]) for x in c["top_by_patients"]
                        if x["prefixed"].split(":")[0] in SYSNAME and known.get(x["prefixed"], True)]
        for v in d.get("value_sets") or []:
            if v["path"] == prop and detail_kind(t, a) == "value":
                vals = [(x["prefixed"].split(":", 1)[1], x["patients"]) for x in v["values"]]
        out[(t, a)] = vals
    return out


def detail_test(rng, t, v, a, dvals, keep):
    """({"attr", "op": "=", "value"}, words after the record's noun, words after "have") for one value of a detail."""
    if a == "rank":
        keep.cue("main", CUE_V2["main"])
        return {"attr": f"{v}.rank", "op": "=", "value": "main"}, DETAILS[(t, a)][1], \
            "are the main diagnosis" if t == "BilledDiagnosis" else "are the principal procedure"
    value = weighted(rng, [(x, math.sqrt(n) + 1) for x, n in dvals[(t, a)][:40]])
    test = {"attr": f"{v}.{a}", "op": "=", "value": value}
    if a == "outcome":
        word, cue, forbid = OUTCOME[value]
        keep.cue(f"outcome {word}", cue)
        if forbid:
            keep.forbid.append(forbid)
        return test, f"whose outcome is {word}", f"have the outcome {word}"
    if a == "consequences":
        phrase, cue = CONSEQUENCES[value]
        keep.cue(f"consequences {value}", cue)
        return test, phrase, phrase.replace("with ", "have ", 1) if phrase.startswith("with ") else phrase.replace("that ", "", 1)
    if value in ZYGOSITY and rng.random() < 0.4:
        keep.cue(ZYGOSITY[value], ZYGOSITY[value][:7])
        keep.forbid.append("homozyg" if value.endswith("135") else "heterozyg")
        return test, f"that is {ZYGOSITY[value]}", f"are {ZYGOSITY[value]}"
    system, local = value.split(":", 1)
    keep.exact.add(local)
    keep.cue("code system", SYSCUE[system])
    shown = f"{SYSNAME[system]} code {local}"
    return test, DETAILS[(t, a)][1].format(shown), f"have {DETAILS[(t, a)][0]} {shown}"


def nm(*parts):
    return re.sub(r"[^a-z0-9_]", "_", "_".join(parts).lower())[:24]


def sample_v21(rng, inv, pool, names, dvals):
    """A plan using coded details (group by one, filter on one, share or patients_if of a value, patients_if of another
    record) and its template: (plan, question, Keep, meta)."""
    form = weighted(rng, [("group", 25), ("patients_if", 18), ("has", 18), ("share", 15), ("count", 24)])
    t = weighted(rng, list(V21_TYPES.items()))
    usable = [a for (tt, a) in DETAILS if tt == t and (dvals.get((tt, a)) or form == "group" and DETAILS[(tt, a)][2])]
    groupable = [a for (tt, a) in DETAILS if tt == t and DETAILS[(tt, a)][2]]
    if not usable or form == "group" and not groupable or not any(dvals.get((t, b)) for b in usable):
        return sample_v21(rng, inv, pool, names, dvals)
    v = TYPES[t][0]
    spec, words, keep = record(rng, inv, pool, names, t, v, cond_p=0.2, code_p=0.3 if t != "BilledProcedure" else 0.0)
    plural, obj = describe(rng, t, words, plural=True), describe(rng, t, words)
    recs, out, group, meta = {v: spec}, [], [], {"shape": f"v2.1 {form}", "types": [t]}

    def other_record():
        x_t = pick_types(rng, inv, 1, exclude=[t, "LabResult"] + (["OncologyDiagnosis"] if t == "VariantDescriptor" else []))[0]
        x_v = TYPES[x_t][0] if TYPES[x_t][0] != v else TYPES[x_t][0] + "2"
        x_spec, x_words, x_keep = record(rng, inv, pool, names, x_t, x_v, cond_p=0.15)
        recs[x_v] = x_spec
        meta["types"].append(x_t)
        merged = merge([keep, x_keep])
        keep.__dict__.update(merged.__dict__)
        return x_v, x_t, (x_words[0] if x_t == "AdministrativeSex" else describe(rng, x_t, x_words))

    def group_by(a):
        kind = detail_kind(t, a)
        system = (dvals.get((t, a)) or [("snomed:x", 0)])[0][0].split(":")[0] if kind == "code" else None
        system = "snomed" if a in ("rank", "route") or system not in SYSNAME else system
        keep.cue(f"by {a}", r"per|by|each|break|for every|group|split|across|distribution|which")
        return {"code_class": f"{v}.{a}", **({"ns": system} if kind == "code" else {})}, DETAILS[(t, a)][0]

    if form == "group":
        a = rng.choice(groupable)
        dim, dw = group_by(a)
        group = [dim]
        aggs = rng.choice((["patients"], ["patients"], ["records"], ["patients", "records"]))
        out = [{"name": "patients", "agg": "patients"} if x == "patients" else {"name": f"{v}_records", "agg": "records", "of": v}
               for x in aggs]
        counts = " and ".join({"patients": "the number of patients", "records": f"the number of {TYPES[t][2]}"}[x] for x in aggs)
        extra, kind = "", weighted(rng, [(None, 4), ("share", 1.5), ("detail", 2), ("has", 2.5)])
        others = [b for b in usable if b != a and dvals.get((t, b)) and not (t == "AdverseEvent" and b == "consequences")]
        if kind in ("share", "detail") and others:
            b = rng.choice(others)
            test, with_, have = detail_test(rng, t, v, b, dvals, keep)
            if kind == "share":
                out.append({"name": nm(b, "pct"), "agg": "share", "if": test})
                extra = f", and the percentage of them {with_.replace('with ', 'that have ', 1) if with_.startswith('with ') else with_}"
                keep.cue("share", CUE_V2["share"])
            else:
                out.append({"name": nm("patients", b), "agg": "patients_if", "if": test})
                extra = f", and how many patients have one {with_}"
        elif kind == "has":
            x_v, x_t, x_obj = other_record()
            out.append({"name": nm("patients_with", x_v), "agg": "patients_if", "if": {"has": x_v}})
            extra = f", and how many {'of those ' if 'patients' in aggs else ''}patients also " \
                    f"{'are' if x_t == 'AdministrativeSex' else 'have'} {x_obj}"
            keep.cue("also", r"also|as well|too|additionally|both|of them|among them|of those|who")
        if rng.random() < 0.5:
            plan_order = [{"by": out[0]["name"], "desc": True}]
            n = rng.choice((3, 5, 10)) if rng.random() < 0.4 else None
            q = f"Which {dw} values are the most common among {plural}? Give {counts} for each{extra}." + \
                (f" Show the top {n}." if n else "")
            keep.cue("most", r"most|top|frequent|common|highest|rank|order|sort")
            meta["order"] = plan_order
            if n:
                meta["limit"] = n
                keep.number(n)
        else:
            q = rng.choice((f"For {plural}, give {counts} per {dw}{extra}.", f"Break down {plural} by {dw}: {counts}{extra}."))
            if aggs == ["patients"] and not extra and rng.random() < 0.5:
                q = f"How many patients have {obj}, broken down by {dw}?"
    elif form in ("patients_if", "share"):
        a = rng.choice([b for b in usable if dvals.get((t, b))])
        test, with_, have = detail_test(rng, t, v, a, dvals, keep)
        if form == "patients_if":
            out = [{"name": "patients", "agg": "patients"}, {"name": nm("patients", a), "agg": "patients_if", "if": test}]
            q = f"How many patients have {obj}, and how many of them have one {with_}?"
        else:
            out = [{"name": nm(a, "pct"), "agg": "share", "if": test}]
            q = rng.choice((f"What percentage of {plural} {have}?", f"What share of {plural} {have}?"))
            keep.cue("share", CUE_V2["share"])
        options = [b for b in groupable if b != a and not (t == "AdverseEvent" and "consequences" in (a, b))]
        g = weighted(rng, [(None, 5), ("sex", 1.5), ("year", 1 if P.CURATED[t].get("date") else 0), ("detail", 2 if options else 0)])
        if g == "sex":
            group, q = [{"sex": True}], q[:-1] + ", by administrative sex?"
            keep.cue("sex", CUE_V2["sex"])
        elif g == "year":
            group, q = [{"year": f"{v}.date"}], q[:-1] + ", per year?"
            keep.cue("year", CUE_V2["year"])
        elif g == "detail":
            dim, dw = group_by(rng.choice(options))
            group, q = [dim], q[:-1] + f", per {dw}?"
    elif form == "has":
        if dvals and rng.random() < 0.5:
            a = rng.choice([b for b in usable if dvals.get((t, b))])
            test, with_, _ = detail_test(rng, t, v, a, dvals, keep)
            spec.setdefault("where", []).append(test)
            obj, plural = f"{obj} {with_}", f"{plural} {with_}"
        x_v, x_t, x_obj = other_record()
        out = [{"name": "patients", "agg": "patients"}, {"name": nm("patients_with", x_v), "agg": "patients_if", "if": {"has": x_v}}]
        q = f"How many patients have {obj}, and how many of them also {'are' if x_t == 'AdministrativeSex' else 'have'} {x_obj}?"
        keep.cue("also", r"also|as well|too|additionally|both|of them|among them|of those|who")
        if groupable and rng.random() < 0.3:
            options = [b for b in groupable if b not in [w["attr"].split(".")[1] for w in spec.get("where", [])]]
            if options:
                dim, dw = group_by(rng.choice(options))
                group, q = [dim], q[:-1] + f", per {dw}?"
    else:  # count: v1 plans filtered by a detail value
        a = rng.choice([b for b in usable if dvals.get((t, b))])
        test, with_, _ = detail_test(rng, t, v, a, dvals, keep)
        spec.setdefault("where", []).append(test)
        obj, plural = f"{obj} {with_}", f"{plural} {with_}"
        shape = weighted(rng, [("one", 6), ("records", 1.5), ("and", 1.2), ("exclude", 1), ("or", 0.6)])
        meta["shape"] = f"v2.1 count {shape}"
        if shape == "records":
            out = [{"name": f"{v}_records", "agg": "records", "of": v}]
            q = rng.choice((f"How many {plural} are recorded?", f"Count the {plural}."))
        else:
            out = [{"name": "patients", "agg": "patients"}]
            if shape == "one":
                plan = {"route": "plan", "records": recs, "cohort": {"has": v}, "output": out}
                clause = f"have {obj}"
            else:
                x_v, x_t, x_obj = other_record()
                cohort = {"and": [{"has": v}, {"has": x_v}]} if shape == "and" else \
                    {"and": [{"has": v}, {"not": {"has": x_v}}]} if shape == "exclude" else {"or": [{"has": v}, {"has": x_v}]}
                verb = "are" if x_t == "AdministrativeSex" else "have"
                clause = {"and": f"have {obj} and {'are' if verb == 'are' else 'also have'} {x_obj}",
                          "exclude": f"have {obj} but {'are not' if verb == 'are' else 'do not have'} {x_obj}",
                          "or": f"have {obj} or {x_obj}"}[shape]
                keep.cue(shape, {"and": r"\band\b|also|both|as well|plus|who", "or": r"\bor\b|either|any",
                                 "exclude": r"\bnot\b|\bno\b|without|never|n't|exclud|except|lack|other than"}[shape])
                plan = {"route": "plan", "records": recs, "cohort": cohort, "output": out}
            if shape == "or" and x_t == "AdministrativeSex":
                return sample_v21(rng, inv, pool, names, dvals)
            plan = {k: plan[k] for k in ("route", "records", "cohort", "output")}
            if v21_bench(plan):
                return sample_v21(rng, inv, pool, names, dvals)
            return plan, count_question(rng, clause), keep, meta
    plan = {"route": "plan", "records": recs, **({"group": group} if group else {}), "output": out,
            **({"order": meta.pop("order")} if "order" in meta else {}), **({"limit": meta.pop("limit")} if "limit" in meta else {})}
    if v21_bench(plan):
        return sample_v21(rng, inv, pool, names, dvals)
    return plan, q, keep, meta
