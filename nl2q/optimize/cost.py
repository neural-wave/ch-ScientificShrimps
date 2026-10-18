"""Predicted cost of a query at 100k: the rows Virtuoso visits when it joins the patterns in the written order (the
`order` pragma), from the data's cardinalities (stats.py). The first pattern brings its matches in the whole store, then
each pattern multiplies the rows by what it yields per row: its fan-out or fan-in from a bound variable, its matches per
patient graph once the graph is bound; an OPTIONAL, MINUS or EXISTS body is costed once per outer row, a sub-select on its
own. An inferred code scanned inside a bound graph also probes each of its sub-codes per row. Calibrated against optbench's
timings (python -m nl2q.tools.optbench calibrate): it ranks queries, it does not predict seconds."""
from . import PRAGMA, Context, optimize, stats
from .sparql import Unsupported, groups, read


def cost(query):
    """Expected rows visited, or None when the reader does not understand the query."""
    return (walk(query) or (None,))[0]


def culprit(query):
    """The pattern that costs the most, in words for the generator, with the data's counts (None if nothing stands out)."""
    w = walk(query)
    if not w or not w[1].top:
        return None
    _, kind, cls = w[1].top
    c = stats.data()["classes"].get(cls)
    if not c or not c[0]:
        return None  # never point the generator away from a 2026.1 term the 2024.2 data lacks
    name, counts = cls.replace(stats.SPHN, "sphn:"), f"{c[0]:,} records in {c[1]:,} patients"
    if kind == "anchor":
        return f"it starts from every {name} ({counts}); start from the code or the rarest record type instead"
    return (f"the {kind} over {name} runs once per outer row ({counts}); if it only marks a group, use EXISTS; "
            f"anchor on the code")


def walk(query):
    """(rows visited, Walk) or None."""
    try:
        q = optimize(query, ("paths",))[0]  # a sequence path costs what its chain of patterns costs
        root, comments = read(q)
        w = Walk(Context(q, root, (), comments), bool(PRAGMA.search(q)))
        return w.group(root, 1.0, set(), False, False)[0], w
    except Unsupported:
        return None


class Walk:
    def __init__(self, ctx, ordered):
        # ordered (the pragma): Virtuoso evaluates an OPTIONAL or EXISTS body without the outer bindings, once per outer
        # row (O08: 60 s cap with the pragma, 3 s without); without it, its own plan correlates the body
        self.ctx, self.d, self.ordered = ctx, stats.data(), ordered
        self.top, self.most = None, 0.0  # (rows, kind, class) of the costliest anchor, OPTIONAL or EXISTS

    def note(self, rows, kind, cls):
        """Remember the costliest anchor, OPTIONAL or EXISTS and the sphn class it ranges over."""
        if cls and rows > self.most:
            self.most, self.top = rows, (rows, kind, cls)

    def typed(self, t):
        """The sphn class of `?x a sphn:C`, else None."""
        ctx = self.ctx
        if t.o.kind in ("iri", "pname") and (t.p.kind == "a" or t.p.kind in ("iri", "pname") and ctx.iri(t.p) == stats.RDF_TYPE):
            return ctx.iri(t.o) if ctx.iri(t.o).startswith(stats.SPHN) else None
        return None

    def body_class(self, g):
        """The first sphn class typed in g or in a group under it (an OPTIONAL body's may sit in its GRAPH block)."""
        return next((c for sub in groups(g) for e in sub.elements for t in e.triples if (c := self.typed(t))), None)

    def empty(self, g, in_graph):
        """A required pattern with a 2026.1 sphn term the 2024.2 data lacks: Virtuoso returns nothing at once (O02#4)."""
        ctx = self.ctx
        for e in g.elements:
            for t in e.triples:
                term = t.o if t.p.kind == "a" or (t.p.kind in ("iri", "pname") and ctx.iri(t.p) == stats.RDF_TYPE) else t.p
                if term.kind in ("iri", "pname") and ctx.iri(term).startswith(stats.SPHN) \
                        and stats.triple(t, ctx.iri, in_graph or bool(g.graph), ctx.inference)[1] == 0:
                    return True
        return False

    def fan(self, t, bound, graph_bound, in_graph):
        """(rows per input row, extra probes per input row) of one triple pattern."""
        ctx, d = self.ctx, self.d
        n, pats = stats.triple(t, ctx.iri, in_graph, ctx.inference)
        vs = t.vars()
        if not pats:
            return 0.0, 0.0  # matches nothing: the run ends here
        if vs <= bound:
            return 1.0, 0.0  # a check
        if vs & bound:
            fan = n / d["patients"]
            if t.p.kind not in ("var", "path"):
                p = ctx.iri(t.p)
                if t.s.text in bound:
                    fan = min(fan, d["fanout"].get(p, 1.0))
                elif t.o.text in bound:
                    fan = min(fan, d["fanin"].get(p, fan))
            return fan, 0.0
        if not graph_bound:
            return float(n), 0.0  # the anchor: its matches in the whole store
        probes = 0.0
        if t.p.kind not in ("var", "path") and ctx.iri(t.p) == stats.RDF_TYPE and t.o.kind in ("iri", "pname") \
                and (ctx.inference or "inference" in t.option.lower()):
            c = stats.code(ctx.iri(t.o), True)
            probes = float(c[2]) if c else 0.0  # every sub-code of an inferred code, in every graph
        return n / d["patients"], probes

    def inner(self, bound, graph_bound):
        """What an OPTIONAL, MINUS or EXISTS body sees bound when it runs."""
        return (set(), False) if self.ordered else (bound, graph_bound)

    def group(self, g, rows, bound, graph_bound, in_graph):
        """(visited, rows out, bound out, graph bound out) of group g joined to `rows` input rows."""
        visited, bound = 0.0, set(bound)
        in_graph = in_graph or bool(g.graph)
        if self.empty(g, in_graph):
            return 0.0, 0.0, bound, graph_bound
        if g.graph and g.graph in bound:
            graph_bound = True
        for e in g.elements:
            if e.kind == "triples":
                for t in e.triples:
                    f, probes = self.fan(t, bound, graph_bound, in_graph)
                    anchor = not bound and not graph_bound
                    visited += rows * probes
                    rows *= f
                    visited += rows
                    if anchor:
                        self.note(rows, "anchor", self.typed(t))
                    bound |= t.vars()
                    graph_bound = graph_bound or in_graph
            elif e.kind in ("filter", "bind"):  # EXISTS bodies, once per row
                for sub in e.groups:
                    v = rows * self.group(sub, 1.0, *self.inner(bound, graph_bound), in_graph)[0]
                    visited += v
                    self.note(v, "EXISTS", self.body_class(sub))
                bound |= e.vars if e.kind == "bind" else set()
            elif e.kind in ("optional", "minus"):
                v, r, *_ = self.group(e.groups[0], 1.0, *self.inner(bound, graph_bound), in_graph)
                visited += rows * v
                self.note(rows * v, e.kind.upper(), self.body_class(e.groups[0]))
                rows *= max(1.0, r) if e.kind == "optional" else 1.0
            elif e.kind in ("graph", "group"):
                visited_, rows, bound, graph_bound = self.group(e.groups[0], rows, bound, graph_bound,
                                                                in_graph or e.kind == "graph")
                visited += visited_
            elif e.kind == "union":
                parts = [self.group(sub, rows, bound, graph_bound, in_graph) for sub in e.groups]
                visited += sum(p[0] for p in parts)
                rows = sum(p[1] for p in parts)
                bound |= set.intersection(*(p[2] for p in parts))
            elif e.kind == "subselect":  # evaluated on its own, then joined
                v, r, *_ = self.group(e.groups[0], 1.0, set(), False, in_graph)
                visited += v
                rows = r if rows <= 1 else max(rows, r)
                bound |= e.vars
            elif e.kind == "values":
                bound |= e.vars
        return visited, rows, bound, graph_bound
