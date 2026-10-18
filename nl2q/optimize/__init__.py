"""Optimizer: deterministic rewrites of the generated query, before it is judged and returned, so that Virtuoso runs
it faster with the same results (NL2Q_OPTIMIZE=1). Under DEFINE sql:select-option "order" Virtuoso joins the triple
patterns in the written order, so the order decides how many patient graphs it visits.
- paths: a sequence path p1/p2 becomes two triple patterns joined by a fresh variable (the SPARQL algebra of a
  sequence path); then reorder can move each step.
- reorder: inside each group, a contiguous run of triple patterns (and FILTERs, which stay after them) is put in
  greedy order from the data's cardinalities (stats.key): the pattern found in the fewest patients first (usually the
  code), then checks on bound nodes, cheap links from bound variables (fan-out, fan-in), then the rarest scan.
  Nothing moves across OPTIONAL, MINUS, BIND, VALUES, UNION or a group.
- pragma: a query with OPTIONAL and no pattern the data never matches loses DEFINE sql:select-option "order":
  measured at 100k, the written order made such queries slower (four timeouts at 60 s ran in 1.7-3 s without it),
  while a pattern that matches nothing, put first, is what makes the order pay off.
- flag (static, not in the default passes yet): an OPTIONAL used only through BOUND(?v) becomes EXISTS (flag.py). It
  fixes a double count, so rows change where the OPTIONAL matched twice for one row.
Knowledge (NL2Q_OPT_STATS): `schema` (default, static) orders by the schema and SHACL alone (shacl.py), so it holds on
any database with the SPHN 2026.1 schema; `data` orders by the 100k mock patients' counts (stats.py) and also puts
first, and keeps the pragma for, a pattern this data never matches. The report compares the two.
Anything the reader does not fully understand leaves the query unchanged. python -m nl2q.tools.optbench measures it."""
import logging, re
from .. import schema as SC
from .. import settings as S
from ..compliance.rules import VIRTUOSO_PREFIXES
from . import shacl, stats
from .sparql import Term, Triple, Unsupported, groups, read, tokenize
from .flag import flag

log = logging.getLogger(__name__)
PASSES = ("flag", "paths", "reorder", "pragma")  # in this order; the default set is S.OPT_PASSES
PRAGMA = re.compile(r'(?im)^[ \t]*DEFINE[ \t]+sql:select-option[ \t]+"order"')  # its line stays (empty): the checker
# sets DEFINEs aside the same way, so the positions in its reason for an invalid query do not move
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
KNOWN_PREFIXES = {**VIRTUOSO_PREFIXES, **SC.PREFIXES}  # a query may leave out the PREFIX lines of these


def optimize(query, passes=None):
    """(query, [names of the passes that changed it]). Off (NL2Q_OPTIMIZE=0): the query unchanged."""
    if passes is None:
        passes = S.OPT_PASSES if S.OPTIMIZE else ()
    if not passes or not query:
        return query, []
    applied = set()
    try:
        if "flag" in passes and (flagged := flag(query)) is not None:
            query, applied = flagged, {"flag"}
        root, comments = read(query)
        ctx = Context(query, root, passes, comments)
        out = apply(query, ctx.walk(root, False, set(), False), 0, len(query))
        optional = any(e.kind == "optional" for g in groups(root) for e in g.elements)
        if "pragma" in ctx.passes and optional and PRAGMA.search(out) \
                and not Context(out, read(out)[0], (), []).matches_nothing():  # on the rewritten query: paths expanded
            out = PRAGMA.sub("", out, count=1)
            ctx.applied.add("pragma")
        return out, [p for p in PASSES if p in ctx.applied | applied]
    except Unsupported:
        return query, sorted(applied)
    except Exception as e:  # fail safe: a bug here must never cost a query
        log.warning(f"optimizer skipped the query: {type(e).__name__}: {e}")
        return query, sorted(applied)


class Context:
    def __init__(self, q, root, passes, comments):
        self.q, self.root, self.passes, self.comments, self.applied = q, root, set(passes), comments, set()
        self.prefixes = dict(KNOWN_PREFIXES)
        toks = tokenize(q)[0]
        for i, t in enumerate(toks[:-2]):
            if t.kind == "name" and t.text.upper() == "PREFIX" and toks[i + 2].kind == "iri":
                self.prefixes[toks[i + 1].text[:-1]] = toks[i + 2].text[1:-1]
        names = {t.text[1:] for t in toks if t.kind == "var"}
        self.inference = any(t.kind == "pname" and t.text.lower() == "input:inference" for t in toks)  # global DEFINE
        self.fresh = (f"path{n}" for n in range(1, 10 ** 6) if f"path{n}" not in names)
        star = any(t.kind == "punct" and t.text == "*" and i and (toks[i - 1].kind == "name" and toks[i - 1].text.upper()
                   in ("SELECT", "DISTINCT", "REDUCED")) for i, t in enumerate(toks))
        if star:  # SELECT * or COUNT(DISTINCT *) would see the fresh variable of an expanded path
            self.passes.discard("paths")

    def matches_nothing(self):
        """Is there a triple pattern the data never matches (stats.triple: no patient), inside a patient graph? Only with
        NL2Q_OPT_STATS=data: the schema alone cannot tell."""
        if S.OPT_SOURCE != "data":
            return False
        def walk(g, in_graph):
            in_graph = in_graph or bool(g.graph)
            return any(stats.triple(t, self.iri, in_graph, self.inference)[1] == 0 for e in g.elements for t in e.triples) \
                or any(walk(sub, in_graph or e.kind == "graph") for e in g.elements for sub in e.groups)
        return walk(self.root, False)

    def iri(self, term):
        if term.kind == "a":
            return RDF + "type"
        if term.kind == "iri":
            return term.text[1:-1]
        prefix, _, local = term.text.partition(":")
        if prefix not in self.prefixes:
            raise Unsupported(f"unknown prefix {prefix}")
        return self.prefixes[prefix] + local

    def walk(self, g, in_graph, bound, graph_bound):
        """Edits (start, end, text) for group g and everything under it. bound: variables bound before g."""
        edits, bound, els, i = [], set(bound), g.elements, 0
        in_graph = in_graph or bool(g.graph)
        while i < len(els):
            if els[i].kind not in ("triples", "filter"):
                e = els[i]
                for sub in e.groups:
                    if e.kind == "subselect":  # evaluated on its own: nothing outside it is bound yet
                        edits += self.walk(sub, in_graph, set(), False)
                    else:
                        edits += self.walk(sub, in_graph or e.kind == "graph", bound,
                                           sub.graph in bound if sub.graph else graph_bound)
                if e.kind not in ("optional", "minus"):
                    bound |= e.vars
                    graph_bound = graph_bound or e.kind != "values"
                i += 1
                continue
            j = i
            while j < len(els) and els[j].kind in ("triples", "filter"):
                j += 1
            run, i = els[i:j], j
            inner = [x for f in run if f.kind == "filter" for sub in f.groups for x in self.walk(sub, in_graph, bound, graph_bound)]
            original = [t for e in run for t in e.triples]
            triples = self.paths(original) if "paths" in self.passes else original
            if triples is not original:
                self.applied.add("paths")
            if "reorder" in self.passes:
                ordered = self.order(triples, bound, graph_bound and in_graph, in_graph)
                if ordered != triples:
                    self.applied.add("reorder")
                triples = ordered
            bound |= {v for t in triples for v in t.vars()}
            graph_bound = graph_bound or bool(triples)
            start, end = run[0].start, run[-1].end
            if triples == original or any(start <= c < end for c, _ in self.comments):
                edits += inner
                continue
            line = self.q.rfind("\n", 0, start) + 1
            sep = "\n" + self.q[line:start] if not self.q[line:start].strip() else " "  # one per line, or one line
            filters = [apply(self.q, [x for x in inner if f.start <= x[0] < f.end], f.start, f.end) for f in run if f.kind == "filter"]
            edits.append((start, end, sep.join([t.text() for t in triples] + filters)))
        return edits

    def paths(self, triples):
        """Sequence paths of plain IRIs (p1/p2/...) as chains of triple patterns; anything else is left as written."""
        out, changed = [], False
        for t in triples:
            steps = t.p.steps
            plain = t.p.kind == "path" and not t.option and len(steps) >= 3 and all(
                (k % 2 == 0 and (x.kind in ("iri", "pname") or x.kind == "name" and x.text == "a")) or
                (k % 2 == 1 and x.kind == "punct" and x.text == "/") for k, x in enumerate(steps))
            if not plain:
                out.append(t)
                continue
            preds = [Term("a", "a") if x.kind == "name" else Term(x.kind, x.text) for x in steps[::2]]
            nodes = [t.s] + [Term("var", "?" + next(self.fresh)) for _ in preds[1:]] + [t.o]
            out += [Triple(nodes[k], p, nodes[k + 1]) for k, p in enumerate(preds)]
            changed = True
        return out if changed else triples

    def order(self, triples, bound, graph_bound, in_graph):
        """Greedy join order (stats.key), ties in written order."""
        if len(triples) < 2:
            return triples
        left, out, bound = list(range(len(triples))), [], set(bound)
        types = {}  # variable -> its sphn classes written in this run
        for t in triples:
            if t.p.kind in ("a", "iri", "pname") and t.o.kind in ("iri", "pname") and not t.option \
                    and self.iri(t.p) == stats.RDF_TYPE and self.iri(t.o).startswith(stats.SPHN):
                types.setdefault(t.s.text, set()).add(self.iri(t.o))

        data = S.OPT_SOURCE == "data"  # the 100k counts; otherwise the schema and SHACL only (shacl.py)
        doomed = {t.s.text for t in triples if stats.dead(t, self.iri, in_graph, types)} if data else set()
        rank = stats.key if data else shacl.key

        def key(k):
            return rank(triples[k], self.iri, in_graph, self.inference, bound, graph_bound, types, doomed) + (k,)

        while left:
            k = min(left, key=key)
            left.remove(k)
            out.append(triples[k])
            bound |= triples[k].vars()
            graph_bound = graph_bound or in_graph
        return out if any(a is not b for a, b in zip(out, triples)) else triples


def apply(q, edits, start, end):
    """q[start:end] with the (non-overlapping) edits inside it applied."""
    out, pos = [], start
    for s, e, text in sorted(edits):
        out += [q[pos:s], text]
        pos = e
    return "".join(out + [q[pos:end]])
