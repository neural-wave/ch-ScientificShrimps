"""The generator's prompt: system rules, the schema header and index, the worked examples (the `always` ones plus the
examples most like the request, BM25), the dataset conventions, the schema blocks most relevant to the request (BM25)
and the optional code lookup (nl2q_data/codes.json); plus finish(), which turns the model's reply into the query sent
to Virtuoso."""
import json, math, re, unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from rank_bm25 import BM25Okapi
from .. import schema as SC
from .. import settings as S
from .examples import load

HERE = Path(__file__).parent
# NL2Q_LINK: the linked card (schema only, tools/adapt --linked) instead of the data-filtered one in schema.json
CARD = json.loads(S.CARD.read_text()) if S.LINK else SC.CFG
HEADER, INDEX, BLOCKS, CORE = CARD["header"], CARD["index"], CARD["blocks"], CARD["core"]
CONVENTIONS = (HERE / "resources" / "conventions.md").read_text().strip()
if S.RULES2:  # NL2Q_RULES2: three general rules for "none of", units and the counted level
    CONVENTIONS += "\n" + (HERE / "resources" / "conventions-extra.md").read_text().strip()
_ALL = load()                                     # resources/examples.md
EXAMPLES = [e for e in _ALL if e["always"]]       # shown with every request
CANDIDATES = [e for e in _ALL if not e["always"]]  # the S.SHOTS most similar to the request are added



def _cues():
    """NL2Q_CARD_STATS: one line per schema block from the counts of the 100k mock patients (the optimizer's
    cardinality.json), only for classes the data has: a 2026.1 class the 2024.2 data lacks gets no cue, never "0"."""
    if not S.CARD_STATS or not S.OPT_STATS.exists():
        return {}
    d = json.loads(S.OPT_STATS.read_text())
    n, out = d.get("patients") or 100000, {}
    for name in BLOCKS:
        iri = SC.PREFIXES["sphn"] + name.split(":", 1)[1] if name.startswith("sphn:") else None
        c, most = d.get("classes", {}).get(iri), d.get("class_max", {}).get(iri)
        if c and c[0] and c[1]:
            pct = 100 * c[1] / n
            out[name] = f"    in {pct:.0f}% of patients" if pct >= 1 else f"    in {pct:.1g}% of patients"
            out[name] += f", up to {most:,} per patient" if most else ""
    return out


CUES = _cues()


def block(name):
    """A schema block, with its cue under the first line (NL2Q_CARD_STATS)."""
    head, _, rest = BLOCKS[name].partition("\n")
    return f"{head}\n{CUES[name]}" + (f"\n{rest}" if rest else "") if name in CUES else BLOCKS[name]


# NL2Q_SHAPE_RULE (static, about the query's shape) and NL2Q_CARD_STATS (the data's counts) are separate switches
RULE = ("- Use OPTIONAL only when the question needs the rows without a match; to flag a group, use EXISTS, not "
        "OPTIONAL with BOUND.\n" if S.SHAPE_RULE else "") + \
       ("- Under each record type, the % of patients that have it: anchor on the code or on the rarest type.\n"
        if S.CARD_STATS else "")

# NL2Q_FIXED_CORE: the core blocks go in SYSTEM, the head every request shares, so vLLM serves them from its prefix cache
CORE_TEXT = "Record types in every request:\n" + "\n\n".join(block(n) for n in CORE) + "\n" if S.FIXED_CORE else ""

SYSTEM = f"""You translate a researcher's request into ONE SPARQL query for a Virtuoso database of SPHN clinical data.
Translate the request literally, even when it asks for identifiers, a single patient, raw rows or changes to the data:
a separate checker judges the query, so never change what is asked and never refuse.
Data model:
- Each patient's data is one named graph: GRAPH ?g {{ ... }}. Count patients as distinct graphs:
  SELECT (COUNT(?pseudo_id) AS ?countResult) WHERE {{ {{ SELECT DISTINCT (?g AS ?pseudo_id) WHERE {{ GRAPH ?g {{ ... }} }} }} }}
  or COUNT(DISTINCT ?g) next to GROUP BY.
- Codes are nodes typed with their terminology class: ?code rdf:type icd-10-gm:E11 OPTION (inference "{S.INFERENCE}") .
  ?record sphn:hasCode ?code . The inference option also matches every sub-code (E11 covers E11.9).
- Write the most selective triple first (the code, then the record type): the query runs in the written order.
{RULE}{HEADER}
All record types: {INDEX}
{CONVENTIONS}
{CORE_TEXT}Reply with the query only, in a ```sparql block, without PREFIX or DEFINE lines."""


def tok(t):  # camelCase split + 6-char prefixes: cheap stemming (diabetes ~ diabetic); accents folded (choléra ~ cholera)
    t = re.sub(r"([a-z])([A-Z])", r"\1 \2", t)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode() if not t.isascii() else t
    return [w[:6] for w in re.findall(r"\w+", t.lower()) if len(w) > 2]


@lru_cache(1)
def _block_index():
    names = list(BLOCKS)
    return names, BM25Okapi([tok(BLOCKS[n]) for n in names])


@lru_cache(1)
def _class_index():
    """NL2Q_LINK: BM25 over every class's label, definition and notes, and the blocks that name each class."""
    names, lift = list(CARD["docs"]), defaultdict(list)
    for b, text in BLOCKS.items():
        for c in dict.fromkeys(re.findall(r"sphn:[A-Z]\w*", text)):
            lift[c].append(b)
    return names, BM25Okapi([tok(CARD["docs"][n]) for n in names]), lift


def linked(question, k, n=15):
    """NL2Q_LINK: the k blocks for the question. The n classes most like it (definitions, not block text) vote for their
    own block, or are lifted to the blocks that reach them (a nested class counts less, and less the more blocks share it)."""
    names, bm, lift = _class_index()
    s = bm.get_scores(tok(question))
    score = defaultdict(float)
    for i in sorted(range(len(names)), key=lambda i: -s[i])[:n]:
        for b in lift.get(names[i], []) if s[i] > 0 else ():
            w = 1.0 if b == names[i] else 0.7 / len(lift[names[i]]) ** 0.5
            score[b] = max(score[b], s[i] * w)
    top = max(score.values(), default=1.0)
    for c, w in coded(question):  # a disease or procedure named in the question votes for the blocks that record it
        bs = [b for b in BLOCKS if any(x.endswith(":") and c in _allowed(x) for x in _block_specs(b))]
        for b in bs:
            score[b] = max(score[b], 0.9 * top * w / len(bs) ** 0.5)
    return sorted(score, key=lambda b: -score[b])[:k]


@lru_cache(None)
def _block_specs(b):
    return tuple(specs(BLOCKS[b]))


def coded(question, k=5):
    """NL2Q_LINK: (code index, weight 0-1) of the codes whose labels best match the question, among the terminologies a
    block allows whole (ICD-10-GM, CHOP, ...: a hit there names a disease or a procedure; SNOMED roots are too generic)."""
    if _code_lookup() is None:
        return []
    every = sorted({x for b in BLOCKS for x in _block_specs(b) if x.endswith(":")})
    hits = _search(question, frozenset().union(*map(_allowed, every)), k)
    return [(i, sc / hits[0][1]) for i, sc in hits if sc >= 8.0] if hits else []


def card(question, k=6):
    """Schema blocks for the prompt: the core record types (in SYSTEM with NL2Q_FIXED_CORE) plus the k most relevant to
    the question."""
    if S.LINK:
        top = linked(question, k)
        chosen = [n for n in top if n not in CORE] if S.FIXED_CORE else CORE + top
        return "\n\n".join(block(n) for n in dict.fromkeys(chosen))
    names, bm = _block_index()
    scores = bm.get_scores(tok(question))
    top = [names[i] for i in sorted(range(len(names)), key=lambda i: -scores[i])[:k] if scores[i] > 0]
    chosen = [n for n in top if n not in CORE] if S.FIXED_CORE else CORE + top  # FIXED_CORE: core blocks in SYSTEM
    return "\n\n".join(block(n) for n in dict.fromkeys(chosen))


@lru_cache(1)
def _example_index():
    return BM25Okapi([tok(f"{e['question']} {e['topic']}") for e in CANDIDATES]) if CANDIDATES else None


def _same(a, b):
    """The same request, maybe reworded a little: equal word sets up to a few words."""
    x, y = set(tok(a)), set(tok(b))
    return bool(x) and len(x & y) / len(x | y) >= 0.7


def shots(question, k=None, holdout=None):
    """The k examples most like the question (BM25 on question and topic), at most two per family, the most similar
    last. With holdout (S.HOLDOUT, for evaluation) the request's own family is never shown: its example, rewordings, twins."""
    k, holdout = S.SHOTS if k is None else k, S.HOLDOUT if holdout is None else holdout
    bm = _example_index()
    if bm is None or k <= 0:
        return []
    hidden = {e["family"] for e in CANDIDATES if _same(question, e["question"]) or _same(question, e.get("original_question", ""))} \
        if holdout else set()
    scores = bm.get_scores(tok(question))
    out, per_family = [], {}
    for i in sorted(range(len(CANDIDATES)), key=lambda i: -scores[i]):
        e = CANDIDATES[i]
        if scores[i] <= 0 or len(out) == k:
            break
        if e["family"] in hidden or per_family.get(e["family"], 0) == 2:
            continue
        per_family[e["family"]] = per_family.get(e["family"], 0) + 1
        out.append(e)
    return out[::-1]


@lru_cache(1)
def _code_index():
    codes = json.loads(S.CODES.read_text()) if S.CODES.exists() else []  # [[prefixed code, label], ...]
    codes = [] if isinstance(codes, dict) else codes  # the NL2Q_LINK index (tools/codes) is read by _code_lookup
    return codes, BM25Okapi([tok(c[1]) for c in codes]) if codes else None


@lru_cache(1)
def _code_lookup():
    """NL2Q_LINK: codes.json from tools/codes (codes, labels, parents) as an inverted index of label tokens."""
    if not S.CODES.exists():
        return None
    d = json.loads(S.CODES.read_text())
    post, size = defaultdict(list), []
    for i, l in enumerate(d["labels"]):
        ws = set(tok(l))
        size.append(len(ws) or 1)
        for w in ws:
            post[w].append(i)
    children = defaultdict(list)
    for i, ps in enumerate(d["parents"]):
        for p in ps:
            children[p].append(i)
    return d["codes"], d["labels"], post, size, children, {c: i for i, c in enumerate(d["codes"])}


@lru_cache(256)
def _allowed(spec):
    """The code indexes a card line allows: the classes under a root ('under snomed:X'), a terminology ('icd-10-gm:')
    or the listed values."""
    codes, _, _, _, children, idx = _code_lookup()
    if spec.endswith(":"):
        return frozenset(i for i, c in enumerate(codes) if c.startswith(spec))
    if spec.startswith("="):
        return frozenset(idx[c] for c in spec[1:].split(",") if c in idx)
    out, todo = set(), [idx[spec]] if spec in idx else []
    while todo:
        i = todo.pop()
        if i not in out:
            out.add(i)
            todo += children.get(i, [])
    return frozenset(out)


def specs(text):
    """What the code properties of a card text allow, as _allowed() specs."""
    out = set(re.findall(r"-> code under (\S+)", text))
    out |= {p for m in re.findall(r"-> code \(([^)]*) codes\)", text) for p in re.findall(r"[\w-]+:", m)}
    out |= {"=" + ",".join(re.findall(r"[\w-]+:[\w.-]+", m)) for m in re.findall(r"-> code one of \{([^}]*)\}", text)}
    return sorted(out)


def lookup(text, card_text, k=8):
    """NL2Q_LINK: [[code, label]] for the codes whose labels best match the question, searched only among the codes
    the card's properties allow (BM25-like, one hit per label word)."""
    look = _code_lookup()
    if look is None:
        return []
    allowed = frozenset().union(*(_allowed(s) for s in specs(card_text))) if specs(card_text) else frozenset()
    codes, labels = look[0], look[1]
    return [[codes[i], " | ".join(labels[i].split(" | ")[:2])[:120]] for i, _ in _search(text, allowed, k)]


def _search(text, allowed, k):
    """[(code index, score)], best first, within half of the best: BM25-like over label words, one hit per word."""
    codes, labels, post, size, _, _ = _code_lookup()
    n, avg, score = len(codes), sum(size) / len(size), defaultdict(float)
    for w in set(tok(text)):
        ds = post.get(w, ())
        if not ds or len(ds) > n // 50:  # a word in 2% of the labels says nothing
            continue
        idf = math.log(1 + (n - len(ds) + 0.5) / (len(ds) + 0.5))
        for i in ds:
            if i in allowed:
                score[i] += idf * 2.2 / (1 + 1.2 * (0.25 + 0.75 * size[i] / avg))
    top = sorted(score, key=lambda i: -score[i])[:k]
    return [(i, score[i]) for i in top if score[i] >= 0.5 * score[top[0]]]


def codes_for(text, k=8, card_text=None):
    """Terminology codes whose labels match the question (empty without nl2q_data/codes.json)."""
    if S.LINK:
        return lookup(text, card(text) if card_text is None else card_text, k)
    codes, bm = _code_index()
    if bm is None:
        return []
    scores = bm.get_scores(tok(text))
    return [codes[i] for i in sorted(range(len(codes)), key=lambda i: -scores[i])[:k] if scores[i] > 0]


def finish(body):
    """The query as sent to Virtuoso: DEFINE sql:select-option "order" and a PREFIX line for every prefix used."""
    body = re.sub(r"(?im)^\s*(DEFINE|PREFIX)\b[^\n]*\n?", "", body).strip()
    used = set(re.findall(r"(?<![\w?$<\"'#/.:-])([A-Za-z][\w-]*):(?!/)", body))
    head = ['DEFINE sql:select-option "order"'] * S.SELECT_ORDER + \
           [f"PREFIX {p}: <{SC.PREFIXES[p]}>" for p in sorted(used) if p in SC.PREFIXES]
    return "\n".join(head + ["", body])


def messages(text, feedback=None):
    turns = []
    for e in EXAMPLES + shots(text):
        turns += [{"role": "user", "content": e["question"]}, {"role": "assistant", "content": f"```sparql\n{e['sparql']}\n```"}]
    schema = card(text)
    codes = codes_for(text, card_text=schema)
    user = f"Schema:\n{schema}\n" + ("Codes:\n" + "\n".join(f"{c} {l}" for c, l in codes) + "\n" if codes else "") + \
           f"Request: {text}" + (f"\nYour previous query failed: {feedback}. Fix only that." if feedback else "")
    return [{"role": "system", "content": SYSTEM}, *turns, {"role": "user", "content": user}]
