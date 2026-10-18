"""NL2Q_DIALECT: a static rewrite of two forms the model writes that Virtuoso refuses, before the query is checked and
judged (both tested on the 5k store, 17 Oct, with patterns that match nothing):
  ROUND(x, 2)            -> (ROUND((x) * 100.0) / 100.0)   SP030: ROUND takes one argument (in SPARQL 1.1 too)
  EXISTS {A} && B        -> IF(EXISTS {A}, B, false)       SQ074: a positive EXISTS left of && or || in an expression
  EXISTS {A} || B        -> IF(EXISTS {A}, true, B)        (IF, BIND, SELECT); FILTER's own condition is accepted
Same meaning: EXISTS never raises an error. A query the tokenizer does not read, or a rewrite that no longer parses
when the original did, leaves it unchanged."""
import logging
from .. import settings as S
from ..compliance.rules import Invalid, parse
from ..optimize.sparql import Unsupported, tokenize

log = logging.getLogger(__name__)
OPEN, CLOSE = "({[", ")}]"


def fix(query):
    """The query with ROUND(x, n) and a positive EXISTS left of && / || rewritten for Virtuoso; else unchanged."""
    if not S.DIALECT or not query:
        return query
    try:
        out = query
        for _ in range(20):  # one rewrite per pass: the next pass reads the new text
            new = _round(out) or _logic(out)
            if new is None:
                break
            out = new
    except Unsupported:
        return query
    except Exception as e:  # fail safe: a bug here must never cost a query
        log.warning(f"dialect rewrite skipped: {type(e).__name__}: {e}")
        return query
    if out == query or (_parses(query) and not _parses(out)):
        return query
    log.info("Virtuoso dialect rewritten")
    return out


def _parses(q):
    try:
        parse(q)
        return True
    except Invalid:
        return False


def _match(toks):
    """{index of an opening bracket: index of its closing one}."""
    stack, out = [], {}
    for i, t in enumerate(toks):
        if t.kind == "punct" and t.text in OPEN:
            stack.append(i)
        elif t.kind == "punct" and t.text in CLOSE and stack:
            out[stack.pop()] = i
    return out


def _top(toks, a, b, match):
    """Indexes of the tokens in [a, b) outside every bracket nested in the range."""
    i = a
    while i < b:
        yield i
        i = match.get(i, i) + 1


def _round(q):
    toks, _ = tokenize(q)
    match = _match(toks)
    for i, t in enumerate(toks[:-1]):
        if t.kind == "name" and t.text.upper() == "ROUND" and toks[i + 1].text == "(" and i + 1 in match:
            o, c = i + 1, match[i + 1]
            commas = [k for k in _top(toks, o + 1, c, match) if toks[k].text == ","]
            if len(commas) == 1 and commas[0] + 2 == c and toks[c - 1].kind == "number" and toks[c - 1].text.isdigit():
                x, n = q[toks[o + 1].start:toks[commas[0]].start].strip(), int(toks[c - 1].text)
                new = f"ROUND({x})" if n == 0 else f"(ROUND(({x}) * 1{'0' * n}.0) / 1{'0' * n}.0)"
                return q[:t.start] + new + q[toks[c].end:]
    return None


def _positive_exists(toks, a, b, match):
    """Whether [a, b) holds EXISTS without NOT or ! before it, outside braces."""
    i = a
    while i < b:
        t = toks[i]
        if t.kind == "punct" and t.text == "{":
            i = match.get(i, i) + 1
            continue
        if t.kind == "name" and t.text.upper() == "EXISTS" and not (
                i > a and (toks[i - 1].text.upper() == "NOT" or toks[i - 1].text == "!")):
            return True
        i += 1
    return False


def _logic(q):
    toks, _ = tokenize(q)
    match = _match(toks)
    for o, c in sorted(match.items()):
        if toks[o].text != "(" or o > 0 and toks[o - 1].text.upper() == "FILTER":
            continue
        top = list(_top(toks, o + 1, c, match))
        bounds = [o] + [k for k in top if toks[k].text == ","] + [c]
        for a, b in zip(bounds, bounds[1:]):  # each argument (a, b): tokens a+1 .. b-1
            a += 1
            end = next((k for k in _top(toks, a, b, match) if toks[k].text.upper() == "AS"), b)
            ops = [k for k in _top(toks, a, end, match) if toks[k].text in ("&&", "||")]
            if not ops:
                continue
            op = "||" if any(toks[k].text == "||" for k in ops) else "&&"  # || binds looser: split there first
            cuts = [k for k in ops if toks[k].text == op]
            parts = list(zip([a] + [k + 1 for k in cuts], cuts + [end]))
            if not any(_positive_exists(toks, s, e, match) for s, e in parts[:-1]):
                continue
            text = [q[toks[s].start:toks[e - 1].end].strip() for s, e in parts]
            new = text[-1]
            for p in reversed(text[:-1]):
                new = f"IF({p}, true, {new})" if op == "||" else f"IF({p}, {new}, false)"
            return q[:toks[a].start] + new + q[toks[end - 1].end:]
    return None
