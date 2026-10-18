"""Fuzz the compliance rules for false compliants: rewrite every labelled query in ways that keep its meaning, judge
each variant with nl2q.compliance.judge, and list the variants whose verdict disagrees with the label they inherit
from their parent (a "flip"). Each flip is shrunk to the shortest rewrite chain that still flips it.
  python -m nl2q.tools.fuzz_rules --out DIR [--variants 240] [--seed 0] [--workers 32]
Standard library plus rdflib (through the checker), deterministic for a given seed, no model and no database.

The rewrites work on a light parse of the query text (groups, triple statements, everything else kept as tokens), so
DEFINE and OPTION (...) of Virtuoso survive. Each one keeps the meaning of the query on any data:
  rename         rename every variable consistently (sometimes with $, sometimes to rdflib's internal __agg_N__)
  alias_proj     BIND(?x AS ?y) at the end of a WHERE (or right after a triple binding ?x, inside GRAPH or { } at
                 most), and ?y in that SELECT's clause instead of ?x; the alias may be COALESCE(?x)
  alias_triple   BIND(?x AS ?y) after the first triple that binds ?x, and ?y in the later triples of that block
  alias_sub      inside a subquery, ?x renamed to ?x2 and projected as (?x2 AS ?x)
  subquery       wrap a WHERE in { SELECT * WHERE { ... } }
  reorder        reorder triples, predicate-object pairs, objects, or two adjacent joined patterns
  path           two triples through a variable used nowhere else -> a sequence path (with ^ where needed), or
                 one triple -> its inverse path (?o ^p ?s)
  rdftype        a <-> rdf:type <-> its full IRI
  iri_form       prefixed name -> full IRI, full IRI -> prefixed name, or a new label for a declared prefix
  dead_union     { P } UNION { FILTER(false) } in place of P
  optional_new   an OPTIONAL that binds only a new variable: { BIND(c AS ?new) } or a triple that never matches
  group_wrap     wrap a pattern, or the whole content of a group, in an extra { }
  count_form     COUNT(DISTINCT ?x) <-> the official form SELECT (COUNT(?p) ...) { SELECT DISTINCT (?x AS ?p) ... }
Left out because they may change the meaning: rewriting bif:/sql: names (Virtuoso resolves them itself), wrapping
an OPTIONAL in { } (it would lose its left side), wrapping the content of an OPTIONAL that has a FILTER (the FILTER
would lose the outer variables), paths through a variable a SELECT * could return."""
import argparse
import copy
import json
import random
import re
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PARENT_FILES = sorted((ROOT / "tests" / "bench").glob("*.jsonl")) + [ROOT / "tests" / "compliance" / "mutations.jsonl"]
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
NEVER = "urn:nl2q:fuzz#never"  # a predicate no store has: a triple with it never matches

PN = r"[A-Za-z][\w.-]*"  # prefix label (no trailing dot is checked by the local part)
LOCAL = r"(?:[\w:%\\-](?:[\w.:%\\-]*[\w:%\\-])?)?"
TOKEN = re.compile(rf'''
 (?P<ws>\s+)
|(?P<comment>\#[^\n]*)
|(?P<string>(?:"""(?:[^"\\]|\\.|"(?!""))*"""|\'\'\'(?:[^'\\]|\\.|'(?!''))*\'\'\'|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')
   (?:@[A-Za-z]+(?:-[A-Za-z0-9]+)*|\^\^(?:<[^<>"{{}}|^`\\\s]*>|(?:{PN})?:{LOCAL}))?)
|(?P<iri><[^<>"{{}}|^`\\\s]*>)
|(?P<var>[?$][A-Za-z0-9_·À-￿]+)
|(?P<number>\d+\.\d*[eE][+-]?\d+|\d*\.\d+(?:[eE][+-]?\d+)?|\d+(?:[eE][+-]?\d+)?)
|(?P<bnode>_:[\w.-]*\w)
|(?P<pname>(?:{PN})?:{LOCAL})
|(?P<word>[A-Za-z_]\w*)
|(?P<punct>\^\^|&&|\|\||<=|>=|!=|[{{}}()\[\].;,*/+\-=<>!^|?])
''', re.X)
STARTERS = {"GRAPH", "OPTIONAL", "MINUS", "FILTER", "BIND", "VALUES", "SERVICE"}
FORMS = {"SELECT", "CONSTRUCT", "DESCRIBE", "ASK", "INSERT", "DELETE", "WITH", "LOAD", "CLEAR", "DROP", "CREATE", "ADD",
         "MOVE", "COPY"}
JOINED = {"triples", "graph", "group", "union", "values"}  # patterns a group joins: their order does not matter


class Unfuzzable(Exception):
    """The light parser does not understand the query: the parent is skipped."""


class Tok:
    __slots__ = ("kind", "text", "ws")

    def __init__(self, kind, text, ws=True):
        self.kind, self.text, self.ws = kind, text, ws

    def __repr__(self):
        return self.text

    def up(self):
        return self.text.upper() if self.kind == "word" else self.text


def tokenize(q):
    out, i, ws = [], 0, True
    while i < len(q):
        m = TOKEN.match(q, i)
        if not m:
            raise Unfuzzable(f"cannot tokenize at {q[i:i + 20]!r}")
        if m.lastgroup in ("ws", "comment"):
            ws = True
        else:
            out.append(Tok(m.lastgroup, m.group(), ws))
            ws = False
        i = m.end()
    return out


def mk(text):
    """Tokens for new text, set apart from their neighbours."""
    t = tokenize(text)
    if t:
        t[0].ws = True
    return t


class Parser:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def at(self, *texts):
        p = self.peek()
        return p is not None and p.up() in texts

    def take(self):
        if self.i >= len(self.t):
            raise Unfuzzable("unexpected end of query")
        self.i += 1
        return self.t[self.i - 1]

    def expect(self, text):
        t = self.take()
        if t.text != text:
            raise Unfuzzable(f"expected {text}, found {t.text}")
        return t

    def balanced(self):
        out, depth = [], 0
        while True:
            t = self.take()
            out.append(t)
            if t.kind == "punct" and t.text in ("(", "[", "{"):
                depth += 1
            elif t.kind == "punct" and t.text in (")", "]", "}"):
                depth -= 1
            if depth <= 0:
                return out

    def query(self):
        prologue = []
        while self.peek() is not None and not (self.peek().kind == "word" and self.peek().up() in FORMS):
            prologue.append(self.take())
        if not self.at("SELECT"):
            return {"prologue": prologue, "raw": self.t[self.i:]}
        sel = self.select()
        if self.peek() is not None:
            raise Unfuzzable("tokens after the query")
        return {"prologue": prologue, "select": sel}

    def select(self):
        head, where = [self.take()], False
        while True:
            t = self.peek()
            if t is None:
                raise Unfuzzable("SELECT without WHERE")
            if t.up() == "WHERE":
                self.take()
                where = True
                break
            if t.text == "{" and head[-1].up() != "EXISTS":
                break
            head += self.balanced() if t.text in ("(", "[", "{") else [self.take()]
        body, mods = self.group(), []
        while self.peek() is not None and self.peek().text != "}":
            mods += self.balanced() if self.peek().text in ("(", "[", "{") else [self.take()]
        return {"head": head, "where": where, "body": body, "mods": mods}

    def group(self):
        self.expect("{")
        if self.at("SELECT"):
            sub = self.select()
            self.expect("}")
            return {"sub": sub}
        els = []
        while True:
            t = self.peek()
            if t is None:
                raise Unfuzzable("unclosed group")
            w = t.up()
            if t.text == "}":
                self.take()
                return {"els": els}
            if t.text == ".":
                self.take()
            elif w == "GRAPH":
                self.take()
                name = [self.take()]
                els.append({"t": "graph", "name": name, "g": self.group()})
            elif w in ("OPTIONAL", "MINUS"):
                self.take()
                els.append({"t": w.lower(), "g": self.group()})
            elif w == "SERVICE":
                kw = [self.take()]
                if self.at("SILENT"):
                    kw.append(self.take())
                kw.append(self.take())
                els.append({"t": "service", "kw": kw, "g": self.group()})
            elif w == "FILTER":
                toks = [self.take()]
                if self.at("("):
                    toks += self.balanced()
                elif self.at("NOT"):
                    toks += [self.take(), self.take()] + self.balanced()
                elif self.at("EXISTS"):
                    toks += [self.take()] + self.balanced()
                else:
                    toks += [self.take()] + self.balanced()
                els.append({"t": "filter", "toks": toks})
            elif w == "BIND":
                els.append({"t": "bind", "toks": [self.take()] + self.balanced()})
            elif w == "VALUES":
                toks = [self.take()]
                toks += self.balanced() if self.at("(") else [self.take()]
                els.append({"t": "values", "toks": toks + self.balanced()})
            elif t.text == "{":
                gs = [self.group()]
                while self.at("UNION"):
                    self.take()
                    gs.append(self.group())
                els.append({"t": "union", "gs": gs} if len(gs) > 1 else {"t": "group", "g": gs[0]})
            else:
                els.append({"t": "triples", "st": self.triples()})

    def stop(self):
        t = self.peek()
        return t is None or t.text in (".", "}", "{") or t.kind == "word" and t.up() in STARTERS

    def triples(self):
        sts = []
        while True:
            if self.peek() is not None and self.peek().text == ".":
                self.take()
                continue
            if self.stop():
                return sts
            sts.append(self.statement())

    def term(self):
        return self.balanced() if self.at("[", "(") else [self.take()]

    def statement(self):
        s, po = self.term(), []
        while not self.stop():
            if self.at(";"):
                self.take()
                continue
            v, os = self.path(), [self.obj()]
            while self.at(","):
                self.take()
                os.append(self.obj())
            po.append({"v": v, "os": os})
        if not po and s[0].text != "[":
            raise Unfuzzable("subject without predicate")
        return {"s": s, "po": po}

    def obj(self):
        o = self.term()
        if self.at("OPTION"):
            o = o + [self.take()] + self.balanced()
        return o

    def path(self):
        out = []

        def elt():
            if self.at("^"):
                out.append(self.take())
            if self.at("!"):
                out.append(self.take())
                out.extend(self.balanced() if self.at("(") else [self.take()])
            elif self.at("("):
                out.extend(self.balanced())
            else:
                out.append(self.take())
            p = self.peek()
            if p is not None and (p.text in ("*", "+") or p.text == "?" and not p.ws):
                out.append(self.take())

        elt()
        while self.at("/", "|"):
            out.append(self.take())
            elt()
        return out


def parse(text):
    return Parser(tokenize(text)).query()


def rt(toks):
    return "".join((" " if i and t.ws else "") + t.text for i, t in enumerate(toks))


def r_group(g):
    if "sub" in g:
        return "{ " + r_select(g["sub"]) + " }"
    return "{\n" + "\n".join(r_el(e) for e in g["els"]) + "\n}"


def r_st(s):
    return rt(s["s"]) + " " + " ;\n  ".join(rt(p["v"]) + " " + " , ".join(rt(o) for o in p["os"]) for p in s["po"])


def r_el(e):
    t = e["t"]
    if t == "triples":
        return "\n".join(r_st(s) + " ." for s in e["st"])
    if t == "graph":
        return "GRAPH " + rt(e["name"]) + " " + r_group(e["g"])
    if t in ("optional", "minus"):
        return t.upper() + " " + r_group(e["g"])
    if t == "service":
        return rt(e["kw"]) + " " + r_group(e["g"])
    if t == "group":
        return r_group(e["g"])
    if t == "union":
        return "\nUNION\n".join(r_group(g) for g in e["gs"])
    return rt(e["toks"])


def r_select(q):
    return rt(q["head"]) + (" WHERE " if q["where"] else " ") + r_group(q["body"]) + (" " + rt(q["mods"]) if q["mods"] else "")


def render(Q):
    return rt(Q["prologue"]) + "\n" + (r_select(Q["select"]) if "select" in Q else rt(Q["raw"]))


# ---- walking the parse --------------------------------------------------------------------------------------------
def toklists(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from toklists(v)
    elif isinstance(x, list) and x:
        if isinstance(x[0], Tok):
            yield x
        else:
            for v in x:
                yield from toklists(v)


def var_names(x):
    return {t.text[1:] for tl in toklists(x) for t in tl if t.kind == "var"}


def selects(x):
    if isinstance(x, dict):
        if "head" in x:
            yield x
        for v in x.values():
            yield from selects(v)
    elif isinstance(x, list):
        for v in x:
            if not isinstance(v, Tok):
                yield from selects(v)


def groups(x, ctx="top"):
    """(group, kind of the element that holds it)."""
    if isinstance(x, dict):
        if "els" in x or "sub" in x:
            yield x, ctx
        for k, v in x.items():
            yield from groups(v, x.get("t", ctx) if isinstance(v, (dict, list)) else ctx)
    elif isinstance(x, list):
        for v in x:
            if not isinstance(v, Tok):
                yield from groups(v, ctx)


def triples_els(Q):
    return [e for g, _ in groups(Q) for e in g.get("els", []) if e["t"] == "triples"]


def flat_triples(e):
    return [(st, p, o) for st in e["st"] for p in st["po"] for o in p["os"]]


def star(sel):
    depth = 0
    for t in sel["head"][1:]:
        depth += {"(": 1, ")": -1}.get(t.text, 0)
        if depth == 0 and t.text == "*":
            return True
    return False


def fresh(Q, rng, base="f"):
    taken = var_names(Q)
    while True:
        n = f"{base}{rng.randrange(100000)}"
        if n not in taken:
            return n


def retext(t, text):
    return Tok(t.kind, text, t.ws)


def subst(toks, x, y):
    """?x -> ?y in a token list, in place."""
    for i, t in enumerate(toks):
        if t.kind == "var" and t.text[1:] == x:
            toks[i] = retext(t, t.text[0] + y)


def cp(toks):
    return [Tok(t.kind, t.text, t.ws) for t in toks]


# ---- rewrites: each takes a parse (a copy) and a seeded Random, changes it in place, returns False if it cannot -----
def rw_rename(Q, rng):
    names = sorted(var_names(Q))
    if not names:
        return False
    style = rng.choice(["plain", "plain", "dollar", "internal"])
    new, used = {}, set()
    for n in names:
        while True:
            c = f"{rng.choice('vxrtn')}{rng.randrange(100000)}"
            if c not in used:
                break
        used.add(c)
        new[n] = c
    if style == "internal":
        new[rng.choice(names)] = f"__agg_{rng.randrange(3)}__"
    for tl in toklists(Q):
        for i, t in enumerate(tl):
            if t.kind == "var":
                sig = rng.choice("?$") if style == "dollar" else t.text[0]
                tl[i] = retext(t, sig + new[t.text[1:]])
    return True


def _as_targets(head):
    return {head[i + 1].text[1:] for i in range(len(head) - 1) if head[i].up() == "AS" and head[i + 1].kind == "var"}


def rw_alias_proj(Q, rng):
    cands = []
    for s in selects(Q):
        if star(s):
            continue
        top = s is Q.get("select")
        if top and any(t.up() == "VALUES" for t in s["mods"]):
            continue
        targets, occ, depth = _as_targets(s["head"]), [], 0
        for i, t in enumerate(s["head"]):
            depth += {"(": 1, ")": -1}.get(t.text, 0)
            if t.kind == "var" and (top or depth > 0) and t.text[1:] not in targets and s["head"][i - 1].up() != "AS":
                occ.append(t.text[1:])
        if top:
            occ += [t.text[1:] for t in s["mods"] if t.kind == "var" and t.text[1:] not in targets]
        cands += [(s, x, top) for x in sorted(set(occ))]
    if not cands:
        return False
    s, x, top = rng.choice(cands)
    y = fresh(Q, rng, "a")
    depth = 0
    for i, t in enumerate(s["head"]):
        depth += {"(": 1, ")": -1}.get(t.text, 0)
        if t.kind == "var" and t.text[1:] == x and (top or depth > 0) and s["head"][i - 1].up() != "AS":
            s["head"][i] = retext(t, t.text[0] + y)
    if top:
        subst(s["mods"], x, y)
    if "sub" in s["body"]:
        s["body"] = {"els": [{"t": "group", "g": s["body"]}]}
    bind = {"t": "bind", "toks": mk(f"BIND({_alias(x, rng)} AS ?{y})")}
    inner = [(g, i) for g in _plain_groups(s["body"]) for i, e in enumerate(g["els"])
             if e["t"] == "triples" and x in var_names(e)]
    if inner and rng.random() < 0.5:  # right after a triple that binds ?x, possibly inside GRAPH or { }
        g, i = rng.choice(inner)
        g["els"].insert(i + 1, bind)
    else:
        s["body"]["els"].append(bind)
    return True


def _alias(x, rng):
    """?x, or an expression that always has the value of ?x."""
    return rng.choice([f"?{x}", f"?{x}", f"COALESCE(?{x})"])


def _plain_groups(g):
    """g and the groups nested in it through GRAPH and { } only: a BIND there sees the bindings the WHERE sees."""
    if "els" in g:
        yield g
        for e in g["els"]:
            if e["t"] in ("graph", "group"):
                yield from _plain_groups(e["g"])


def rw_alias_sub(Q, rng):
    """Inside a subquery, rename a projected ?x to ?x2 and project (?x2 AS ?x): the subquery's variables are its own."""
    cands = []
    for s in selects(Q):
        if s is Q.get("select") or star(s):
            continue
        depth = 0
        for i, t in enumerate(s["head"]):
            depth += {"(": 1, ")": -1}.get(t.text, 0)
            if t.kind == "var" and depth == 0:
                cands.append((s, t.text[1:]))
    if not cands:
        return False
    s, x = rng.choice(cands)
    x2 = fresh(Q, rng, "s")
    for tl in toklists({"body": s["body"], "mods": s["mods"]}):
        subst(tl, x, x2)
    head, depth = [], 0
    for i, t in enumerate(s["head"]):
        depth += {"(": 1, ")": -1}.get(t.text, 0)
        if t.kind == "var" and t.text[1:] == x and depth == 0:
            head += mk(f"(?{x2} AS ?{x})")
        elif t.kind == "var" and t.text[1:] == x and s["head"][i - 1].up() != "AS":
            head.append(retext(t, t.text[0] + x2))
        else:
            head.append(t)
    s["head"] = head
    return True


def rw_alias_triple(Q, rng):
    cands = []
    for g, _ in groups(Q):
        for ei, e in enumerate(g.get("els", [])):
            if e["t"] != "triples":
                continue
            seen = {}
            for i, st in enumerate(e["st"]):
                for x in sorted(var_names(st)):
                    if x in seen and seen[x] is not None:
                        cands.append((g, ei, seen[x], x))
                        seen[x] = None
                    seen.setdefault(x, i)
    if not cands:
        return False
    g, ei, i, x = rng.choice(cands)
    e = g["els"][ei]
    y = fresh(Q, rng, "b")
    after = copy.deepcopy(e["st"][i + 1:])
    for tl in toklists(after):
        subst(tl, x, y)
    g["els"][ei:ei + 1] = [{"t": "triples", "st": e["st"][:i + 1]}, {"t": "bind", "toks": mk(f"BIND({_alias(x, rng)} AS ?{y})")},
                           {"t": "triples", "st": after}]
    return True


def rw_subquery(Q, rng):
    sels = list(selects(Q))
    if not sels:
        return False
    s = rng.choice(sels)
    s["body"] = {"els": [{"t": "group", "g": {"sub": {"head": mk("SELECT *"), "where": True, "body": s["body"], "mods": []}}}]}
    return True


def rw_reorder(Q, rng):
    opts = []
    for g, _ in groups(Q):
        els = g.get("els", [])
        for e in els:
            if e["t"] == "triples":
                if len(e["st"]) > 1:
                    opts.append(e["st"])
                for st in e["st"]:
                    if len(st["po"]) > 1:
                        opts.append(st["po"])
                    opts += [p["os"] for p in st["po"] if len(p["os"]) > 1]
        for i in range(len(els) - 1):
            if els[i]["t"] in JOINED and els[i + 1]["t"] in JOINED:
                opts.append(("swap", els, i))
    if not opts:
        return False
    o = rng.choice(opts)
    if isinstance(o, tuple):
        _, els, i = o
        els[i], els[i + 1] = els[i + 1], els[i]
    else:
        order = list(range(len(o)))
        rng.shuffle(order)
        if order == sorted(order):
            order.reverse()
        o[:] = [o[k] for k in order]
    return True


def _usage(Q):
    """How often each variable is used: once per triple it is in, once per other token."""
    c = Counter()
    trip = set()
    for e in triples_els(Q):
        trip.add(id(e))
        for st, p, o in flat_triples(e):
            for x in var_names([cp(st["s"]), cp(p["v"]), cp(o)]):
                c[x] += 1
    def walk(x):
        if isinstance(x, dict):
            if x.get("t") == "triples" and id(x) in trip:
                return
            for v in x.values():
                walk(v)
        elif isinstance(x, list) and x:
            if isinstance(x[0], Tok):
                for t in x:
                    if t.kind == "var":
                        c[t.text[1:]] += 1
            else:
                for v in x:
                    walk(v)
    walk(Q)
    return c


def _simple_verb(v):
    return len(v) == 1 and (v[0].kind in ("iri", "pname") or v[0].text == "a")


def _drop(e, st, p, o):
    p["os"] = [x for x in p["os"] if x is not o]
    if not p["os"]:
        st["po"] = [x for x in st["po"] if x is not p]
    if not st["po"]:
        e["st"] = [x for x in e["st"] if x is not st]


def _path(*parts):
    """Path tokens from ("^"|"/"|verb tokens) parts, glued."""
    out = []
    for x in parts:
        for t in ([Tok("punct", x)] if isinstance(x, str) else cp(x)):
            t.ws = not out
            out.append(t)
    return out


def rw_path(Q, rng):
    if any(star(s) for s in selects(Q)):
        return False
    use = _usage(Q)
    literal = lambda o: o[0].kind in ("string", "number") or o[0].up() in ("TRUE", "FALSE")
    cands = []
    for e in triples_els(Q):
        flat = [(st, p, o) for st, p, o in flat_triples(e) if len(st["s"]) == 1 and _simple_verb(p["v"]) and len(o) == 1]
        for a in flat:
            if not literal(a[2]):
                cands.append(("inverse", e, a, None))
            for b in flat:
                if a is b or a[2] is b[2]:
                    continue
                (s1, p1, o1), (s2, p2, o2) = a, b
                for kind, m1, m2 in (("seq", o1[0], s2["s"][0]), ("inv_first", s1["s"][0], s2["s"][0]),
                                     ("inv_last", o1[0], o2[0])):
                    ends = {"seq": (s1["s"], o2), "inv_first": (o1, o2), "inv_last": (s1["s"], s2["s"])}[kind]
                    if m1.kind == "var" and m2.kind == "var" and m1.text[1:] == m2.text[1:] and use[m1.text[1:]] == 2 \
                            and all(t[0].text[1:] != m1.text[1:] for t in ends) and not literal(ends[0]):
                        cands.append((kind, e, a, b))
    if not cands:
        return False
    kind, e, a, b = rng.choice(cands)
    (st1, p1, o1) = a
    if kind == "inverse":
        _drop(e, st1, p1, o1)
        e["st"].append({"s": cp(o1), "po": [{"v": _path("^", p1["v"]), "os": [cp(st1["s"])]}]})
        return True
    (st2, p2, o2) = b
    if kind == "seq":
        new = (st1["s"], _path(p1["v"], "/", p2["v"]), o2)
    elif kind == "inv_first":
        new = (o1, _path("^", p1["v"], "/", p2["v"]), o2)
    else:
        new = (st1["s"], _path(p1["v"], "/", "^", p2["v"]), st2["s"])
    _drop(e, st1, p1, o1)
    _drop(e, st2, p2, o2)
    e["st"].append({"s": cp(new[0]), "po": [{"v": new[1], "os": [cp(new[2])]}]})
    return True


def _prefixes(Q):
    p, out = Q["prologue"], {}
    for i in range(len(p) - 2):
        if p[i].up() == "PREFIX" and p[i + 1].kind == "pname" and p[i + 2].kind == "iri":
            out[p[i + 1].text[:-1]] = p[i + 2].text[1:-1]
    return out


def _body_toklists(Q):
    return list(toklists({k: v for k, v in Q.items() if k != "prologue"}))


def rw_rdftype(Q, rng):
    rdf = next((k for k, v in _prefixes(Q).items() if v == RDF_TYPE[:-4]), None)
    occ = []
    for e in triples_els(Q):
        for st in e["st"]:
            for p in st["po"]:
                for i, t in enumerate(p["v"]):
                    full = t.kind == "iri" and t.text == f"<{RDF_TYPE}>" or t.kind == "pname" and rdf and t.text == f"{rdf}:type"
                    if t.text == "a" or full:
                        occ.append((p["v"], i, len(p["v"]) == 1))
    if not occ:
        return False
    pick = [o for o in occ if rng.random() < 0.5] or [rng.choice(occ)]
    for v, i, alone in pick:
        t = v[i]
        forms = [f"<{RDF_TYPE}>"] + ([f"{rdf}:type"] if rdf else []) + (["a"] if alone or t.text != "a" else [])
        forms = [f for f in forms if f != t.text] or [f"<{RDF_TYPE}>"]
        f = rng.choice(forms)
        v[i] = Tok("word" if f == "a" else "iri" if f.startswith("<") else "pname", f, t.ws)
    return True


def rw_iri_form(Q, rng):
    pre = _prefixes(Q)
    keep = lambda lbl, ns: lbl not in ("bif", "sql") and not ns.startswith(("bif:", "sql:"))
    toks = [(tl, i, t) for tl in _body_toklists(Q) for i, t in enumerate(tl)]
    expand = sorted({t.text.split(":", 1)[0] for _, _, t in toks if t.kind == "pname" and "\\" not in t.text
                     and t.text.split(":", 1)[0] in pre and keep(t.text.split(":", 1)[0], pre[t.text.split(":", 1)[0]])})
    compact = sorted({lbl for _, _, t in toks if t.kind == "iri" for lbl, ns in pre.items()
                      if keep(lbl, ns) and t.text[1:-1].startswith(ns) and re.fullmatch(r"[A-Za-z0-9_][\w-]*", t.text[1 + len(ns):-1])})
    opts = [("expand", l) for l in expand] + [("compact", l) for l in compact] + [("relabel", l) for l in expand]
    if not opts:
        return False
    kind, lbl = rng.choice(opts)
    ns = pre[lbl]
    if kind == "expand":
        for tl, i, t in toks:
            if t.kind == "pname" and t.text.split(":", 1)[0] == lbl:
                tl[i] = Tok("iri", f"<{ns}{t.text.split(':', 1)[1]}>", t.ws)
    elif kind == "compact":
        for tl, i, t in toks:
            loc = t.text[1 + len(ns):-1]
            if t.kind == "iri" and t.text[1:-1].startswith(ns) and re.fullmatch(r"[A-Za-z0-9_][\w-]*", loc):
                tl[i] = Tok("pname", f"{lbl}:{loc}", t.ws)
    else:
        new = f"p{rng.randrange(1000)}x"
        Q["prologue"] = Q["prologue"] + mk(f"PREFIX {new}: <{ns}>")
        for tl, i, t in toks:
            if t.kind == "pname" and t.text.split(":", 1)[0] == lbl:
                tl[i] = Tok("pname", f"{new}:{t.text.split(':', 1)[1]}", t.ws)
            elif t.kind == "string" and re.search(rf"\^\^{re.escape(lbl)}:", t.text):
                tl[i] = Tok("string", re.sub(rf"\^\^{re.escape(lbl)}:", f"^^{new}:", t.text), t.ws)
    return True


def _wrappable(e):
    return e["t"] in ("graph", "group", "union") or e["t"] == "triples" and e["st"]


def rw_dead_union(Q, rng):
    cands = [(g, i) for g, _ in groups(Q) for i, e in enumerate(g.get("els", [])) if _wrappable(e)]
    cands += [(s, None) for s in selects(Q)]
    if not cands:
        return False
    x, i = rng.choice(cands)
    dead = {"els": [{"t": "filter", "toks": mk("FILTER(false)")}]}
    if i is None:
        gs = [x["body"], dead]
    else:
        gs = [{"els": [x["els"][i]]}, dead]
    if rng.random() < 0.5:
        gs.reverse()
    if i is None:
        x["body"] = {"els": [{"t": "union", "gs": gs}]}
    else:
        x["els"][i] = {"t": "union", "gs": gs}
    return True


def rw_optional_new(Q, rng):
    gs = [g for g, _ in groups(Q) if "els" in g]
    if not gs:
        return False
    g = rng.choice(gs)
    n = fresh(Q, rng, "o")
    xs = sorted(var_names([e for e in g["els"] if e["t"] == "triples"]))
    if xs and rng.random() < 0.5:
        inner = f"?{rng.choice(xs)} <{NEVER}> ?{n} ."
    else:
        const = rng.choice(["1", '"x"', "true"])
        inner = f"BIND({const} AS ?{n})"
    g["els"].insert(len(g["els"]), {"t": "optional", "g": Parser(mk("{ " + inner + " }")).group()})
    return True


def rw_group_wrap(Q, rng):
    opts = [(g, None) for g, ctx in groups(Q) if g.get("els")
            and not (ctx == "optional" and any(e["t"] == "filter" for e in g["els"]))]
    opts += [(g, i) for g, _ in groups(Q) for i, e in enumerate(g.get("els", [])) if _wrappable(e)]
    if not opts:
        return False
    g, i = rng.choice(opts)
    if i is None:
        g["els"] = [{"t": "group", "g": {"els": g["els"]}}]
    else:
        g["els"][i] = {"t": "group", "g": {"els": [g["els"][i]]}}
    return True


def _items(head):
    """(DISTINCT/REDUCED or None, [("var", name) | ("expr", tokens)]) of a SELECT clause, None if it has more."""
    i, mod, items = 1, None, []
    if i < len(head) and head[i].up() in ("DISTINCT", "REDUCED"):
        mod, i = head[i].up(), i + 1
    while i < len(head):
        t = head[i]
        if t.kind == "var":
            items.append(("var", t.text[1:]))
            i += 1
        elif t.text == "(":
            depth, j = 0, i
            while True:
                depth += {"(": 1, ")": -1}.get(head[j].text, 0)
                j += 1
                if depth == 0:
                    break
            items.append(("expr", head[i:j]))
            i = j
        else:
            return None
    return mod, items


def _shape(toks):
    return [("VAR" if t.kind == "var" else t.up()) for t in toks]


def _keys(mods):
    if not mods:
        return []
    if len(mods) >= 3 and mods[0].up() == "GROUP" and mods[1].up() == "BY" and all(t.kind == "var" for t in mods[2:]):
        return [t.text[1:] for t in mods[2:]]
    return None


def rw_count_form(Q, rng):
    cands = []
    for s in selects(Q):
        it, keys = _items(s["head"]), _keys(s["mods"])
        if it is None or keys is None or it[0] is not None:
            continue
        exprs = [x for k, x in it[1] if k == "expr"]
        plain = [x for k, x in it[1] if k == "var"]
        if len(exprs) != 1 or set(plain) != set(keys):
            continue
        e = exprs[0]
        if _shape(e) == ["(", "COUNT", "(", "DISTINCT", "VAR", ")", "AS", "VAR", ")"] and e[4].text[1:] not in keys:
            cands.append(("to_sub", s, e[4].text[1:], e[7].text[1:], keys))
        body = s["body"]
        if "els" in body and len(body["els"]) == 1 and body["els"][0]["t"] == "group" and "sub" in body["els"][0]["g"] \
                and _shape(e) in (["(", "COUNT", "(", "VAR", ")", "AS", "VAR", ")"],
                                  ["(", "COUNT", "(", "DISTINCT", "VAR", ")", "AS", "VAR", ")"]):
            inner = body["els"][0]["g"]["sub"]
            iit, ikeys = _items(inner["head"]), _keys(inner["mods"])
            p = e[len(e) - 5].text[1:]
            if iit is None or iit[0] != "DISTINCT" or ikeys != [] or inner["mods"]:
                continue
            ivars = [x for k, x in iit[1] if k == "var"]
            iexpr = [x for k, x in iit[1] if k == "expr"]
            if set(ivars) - {p} != set(keys):
                continue
            if p in ivars and not iexpr:
                cands.append(("to_direct", s, p, e[len(e) - 2].text[1:], keys, inner))
            elif len(iexpr) == 1 and _shape(iexpr[0]) == ["(", "VAR", "AS", "VAR", ")"] and iexpr[0][3].text[1:] == p:
                cands.append(("to_direct", s, iexpr[0][1].text[1:], e[len(e) - 2].text[1:], keys, inner))
    if not cands:
        return False
    c = rng.choice(cands)
    if c[0] == "to_sub":
        _, s, x, n, keys = c
        p = fresh(Q, rng, "c")
        ks = " ".join(f"?{k}" for k in keys)
        inner = {"head": mk(f"SELECT DISTINCT {ks} (?{x} AS ?{p})"), "where": True, "body": s["body"], "mods": []}
        s["head"] = mk(f"SELECT {ks} (COUNT(?{p}) AS ?{n})")
        s["body"] = {"els": [{"t": "group", "g": {"sub": inner}}]}
    else:
        _, s, x, n, keys, inner = c
        ks = " ".join(f"?{k}" for k in keys)
        s["head"] = mk(f"SELECT {ks} (COUNT(DISTINCT ?{x}) AS ?{n})")
        s["body"] = inner["body"]
    return True


REWRITES = {"rename": rw_rename, "alias_proj": rw_alias_proj, "alias_triple": rw_alias_triple, "alias_sub": rw_alias_sub,
            "subquery": rw_subquery,
            "reorder": rw_reorder, "path": rw_path, "rdftype": rw_rdftype, "iri_form": rw_iri_form,
            "dead_union": rw_dead_union, "optional_new": rw_optional_new, "group_wrap": rw_group_wrap,
            "count_form": rw_count_form}


# ---- driver -------------------------------------------------------------------------------------------------------
def apply_chain(Q0, chain):
    Q, applied = Q0, []
    for name, seed in chain:
        Q2 = copy.deepcopy(Q)
        if REWRITES[name](Q2, random.Random(seed)):
            Q = Q2
            applied.append((name, seed))
    return Q, applied


def verdict(text):
    """(reason or None, rules broken, invalid?) from the checker on the current tree."""
    from nl2q.compliance import analyse, judge
    r = judge(text)
    # thrown away: not SPARQL at all. A valid query the checker refuses to read (a reserved variable name) is a verdict
    invalid = r is not None and r.startswith("Not a valid SPARQL") and "reserved by the query parser" not in r
    try:
        rules = sorted({x for x, _ in analyse(text)[0]})
    except Exception as e:
        rules = [type(e).__name__]
    return r, rules, invalid


def chains(pid, n, n_single, seed):
    names = list(REWRITES)
    for k in range(n):
        rng = random.Random(f"{seed}:{pid}:{k}")
        picked = [names[k % len(names)]] if k < n_single else [rng.choice(names) for _ in range(rng.choice([2, 3]))]
        yield k, [(nm, f"{seed}:{pid}:{k}:{i}") for i, nm in enumerate(picked)]


def fuzz_parent(job):
    fname, rec, n, n_single, seed = job
    pid, label = rec["id"], rec["compliant"]
    out = {"file": fname, "parent": pid, "label": label, "variants": [], "skipped": None}
    base, base_rules, _ = verdict(rec["sparql"])
    out["parent_rules"] = base_rules
    try:
        Q0 = parse(rec["sparql"])
        text0 = render(Q0)
    except Unfuzzable as e:
        out["skipped"] = str(e)
        return out
    r0, rules0, _ = verdict(text0)
    out["roundtrip"] = {"same_verdict": (r0 is None) == (base is None), "same_reason": r0 == base, "reason": r0}
    seen = {text0}
    for k, chain in chains(pid, n, n_single, seed):
        try:
            Q, applied = apply_chain(Q0, chain)
            text = render(Q)
        except Exception as e:  # a fuzzer bug: count it, never stop
            out["variants"].append({"k": k, "chain": [c[0] for c in chain], "error": f"{type(e).__name__}: {e}"[:200]})
            continue
        if not applied or text in seen:
            out["variants"].append({"k": k, "chain": [c[0] for c in chain], "applied": [a[0] for a in applied],
                                    "dup": bool(applied)})
            continue
        seen.add(text)
        r, rules, invalid = verdict(text)
        v = {"k": k, "applied": [a[0] for a in applied], "seeds": [a[1] for a in applied], "sparql": text,
             "reason": r, "rules": rules, "invalid": invalid}
        if not invalid and (r is None) != label:
            v["flip"] = "false compliant" if r is None else "false refusal"
            v["min"] = shrink(Q0, applied, label)
        out["variants"].append(v)
    return out


def shrink(Q0, applied, label):
    """The shortest sub-chain (same seeds) that still flips, with its variant."""
    for r in range(1, len(applied) + 1):
        for sub in combinations(applied, r):
            Q, ap = apply_chain(Q0, list(sub))
            if len(ap) != len(sub):
                continue
            text = render(Q)
            reason, rules, invalid = verdict(text)
            if not invalid and (reason is None) != label:
                return {"applied": [a[0] for a in ap], "seeds": [a[1] for a in ap], "sparql": text, "reason": reason,
                        "rules": rules}
    return None


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("--out", required=True, type=Path)
    a.add_argument("--variants", type=int, default=240, help="rewrite chains tried per parent")
    a.add_argument("--single", type=int, default=60, help="of which single rewrites (cycling through all of them)")
    a.add_argument("--seed", default="0")
    a.add_argument("--workers", type=int, default=32)
    a.add_argument("--only", help="comma-separated parent ids")
    a = a.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    jobs = []
    for f in PARENT_FILES:
        for line in open(f):
            if line.strip():
                rec = json.loads(line)
                if "sparql" in rec and "compliant" in rec and (not a.only or rec["id"] in a.only.split(",")):
                    jobs.append((str(f.relative_to(ROOT)), rec, a.variants, a.single, a.seed))
    t0 = time.time()
    stats = Counter()
    by_rw, by_rule, failed_rw, flips = Counter(), Counter(), Counter(), []
    with open(a.out / "variants.jsonl", "w") as vf, ProcessPoolExecutor(max_workers=min(32, a.workers)) as ex:
        for res in ex.map(fuzz_parent, jobs, chunksize=1):
            stats["parents"] += 1
            if res["skipped"]:
                stats["parents skipped"] += 1
                print(f"skipped {res['parent']}: {res['skipped']}", file=sys.stderr)
            elif not res["roundtrip"]["same_verdict"]:
                stats["parents whose reformatting flips"] += 1
            elif not res["roundtrip"]["same_reason"]:
                stats["parents whose reformatting changes the reason"] += 1
            for v in res["variants"]:
                vf.write(json.dumps({"file": res["file"], "parent": res["parent"], "label": res["label"], **v}) + "\n")
                if "error" in v:
                    stats["fuzzer errors"] += 1
                elif "sparql" not in v:
                    stats["duplicates" if v.get("dup") else "not applicable"] += 1
                elif v["invalid"]:
                    stats["unparseable (thrown away)"] += 1
                    for r in set(v["applied"]):
                        failed_rw[r] += 1
                else:
                    stats["variants parsed"] += 1
                    if "flip" in v:
                        stats[v["flip"]] += 1
                        m = v["min"] or {"applied": v["applied"], "rules": v["rules"]}
                        by_rw[(v["flip"], "+".join(m["applied"]))] += 1
                        rules = res["parent_rules"] if v["flip"] == "false compliant" else m["rules"]
                        for r in rules or ["(none)"]:
                            by_rule[(v["flip"], r)] += 1
                        flips.append({"file": res["file"], "parent": res["parent"], "label": res["label"],
                                      "parent_rules": res["parent_rules"], **v})
    stats["variants generated"] = stats["variants parsed"] + stats["unparseable (thrown away)"]
    summary = {"seconds": round(time.time() - t0, 1), "stats": dict(stats),
               "flips by minimal chain": {f"{k[0]}: {k[1]}": n for k, n in by_rw.most_common()},
               "flips by rule": {f"{k[0]}: {k[1]}": n for k, n in sorted(by_rule.items())},
               "unparseable by rewrite": dict(failed_rw.most_common())}
    with open(a.out / "flips.jsonl", "w") as ff:
        for f in flips:
            ff.write(json.dumps(f) + "\n")
    (a.out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
