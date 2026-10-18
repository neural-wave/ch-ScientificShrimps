"""A small SPARQL reader for the optimizer: the tokens of a SELECT query with their positions, and its group graph
patterns as elements (triples blocks with their triples, FILTER, OPTIONAL, MINUS, BIND, VALUES, GRAPH, sub-groups,
UNION, sub-selects). Enough to move triple patterns inside a group and to rewrite them in place. Anything it does not
fully understand raises Unsupported, and the optimizer then returns the query unchanged."""
import re
from dataclasses import dataclass, field


class Unsupported(Exception):
    pass


TOKEN = re.compile(r'''
 (?P<ws>\s+)
|(?P<comment>\#[^\n]*)
|(?P<iri><[^<>"{}|^`\\\s]*>)
|(?P<string>"""(?:[^"\\]|\\.|"(?!""))*"""|\'\'\'(?:[^'\\]|\\.|'(?!''))*\'\'\'|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')
|(?P<var>[?$]\w+)
|(?P<bnode>_:\w(?:[\w.-]*\w)?)
|(?P<pname>(?:[A-Za-z](?:[\w.-]*[\w-])?)?:(?:[\w:%-](?:[\w.:%-]*[\w:%-])?)?)
|(?P<number>\d+\.\d*[eE][+-]?\d+|\.?\d+[eE][+-]?\d+|\d*\.\d+|\d+)
|(?P<lang>@[A-Za-z]+(?:-[A-Za-z0-9]+)*)
|(?P<name>[A-Za-z_]\w*)
|(?P<punct>\^\^|&&|\|\||!=|<=|>=|[{}()\[\].,;^|/*+?!=<>-])
''', re.X)
FORBIDDEN = {"CONSTRUCT", "DESCRIBE", "ASK", "INSERT", "DELETE", "LOAD", "CLEAR", "CREATE", "DROP", "COPY", "MOVE",
             "ADD", "WITH", "SERVICE", "USING"}  # not a plain SELECT: left alone


@dataclass
class Tok:
    kind: str
    text: str
    start: int
    end: int


def tokenize(q):
    """(tokens without whitespace and comments, comment spans)."""
    toks, comments, i = [], [], 0
    while i < len(q):
        m = TOKEN.match(q, i)
        if not m:
            raise Unsupported(f"unexpected character {q[i]!r}")
        kind = m.lastgroup
        if kind == "comment":
            comments.append((m.start(), m.end()))
        elif kind != "ws":
            toks.append(Tok(kind, m.group(), m.start(), m.end()))
        i = m.end()
    return toks, comments


@dataclass
class Term:
    kind: str  # var, iri, pname, bnode, literal, a, path
    text: str  # as written
    steps: tuple = ()  # path: the tokens; a sequence path of plain IRIs has kind "path" and steps of iri/pname tokens


@dataclass
class Triple:
    s: Term
    p: Term
    o: Term
    option: str = ""  # Virtuoso's OPTION (...) after the object, as written

    def vars(self):
        return {t.text for t in (self.s, self.p, self.o) if t.kind in ("var", "bnode")}

    def text(self):
        return f"{self.s.text} {self.p.text} {self.o.text}" + (f" {self.option}" if self.option else "") + " ."


@dataclass
class Element:
    kind: str  # triples, filter, optional, minus, bind, values, graph, group, union, subselect
    start: int  # character span in the query, end exclusive
    end: int
    triples: list = field(default_factory=list)
    groups: list = field(default_factory=list)  # nested groups (sub-groups, union branches, EXISTS bodies, ...)
    vars: set = field(default_factory=set)  # variables it binds (triples, values, bind, groups)


@dataclass
class Group:
    start: int  # position of "{"
    end: int  # position after "}"
    elements: list
    graph: str = ""  # the variable of GRAPH ?g { ... } around this group


class Reader:
    def __init__(self, q):
        self.q = q
        self.t, self.comments = tokenize(q)
        names = {t.text.upper() for t in self.t if t.kind == "name"}
        if names & FORBIDDEN or "SELECT" not in names:
            raise Unsupported("not a plain SELECT query")

    def kw(self, i, *words):
        return i < len(self.t) and self.t[i].kind == "name" and self.t[i].text.upper() in words

    def p(self, i, *chars):
        return i < len(self.t) and self.t[i].kind == "punct" and self.t[i].text in chars

    def tok(self, i):
        if i >= len(self.t):
            raise Unsupported("unexpected end of query")
        return self.t[i]

    def query(self):
        """The WHERE group of the outer SELECT."""
        i = next(i for i, t in enumerate(self.t) if t.kind == "name" and t.text.upper() == "SELECT")
        i = self.where(i)
        g, i = self.group(i)
        self.skip_modifiers(i, len(self.t))
        return g

    def where(self, i):
        """From SELECT, the index of the "{" that opens its WHERE group (the first one outside parentheses)."""
        depth = 0
        while True:
            t = self.tok(i)
            if t.kind == "punct" and t.text in "(":
                depth += 1
            elif t.kind == "punct" and t.text == ")":
                depth -= 1
            elif t.kind == "punct" and t.text == "{" and depth == 0:
                return i
            elif t.kind == "punct" and t.text == "{" or t.kind == "name" and t.text.upper() == "EXISTS":
                raise Unsupported("a group inside the SELECT clause")
            i += 1

    def skip_modifiers(self, i, stop):
        """GROUP BY, HAVING, ORDER BY, LIMIT, OFFSET, VALUES after a WHERE group: no group to optimize there."""
        for t in self.t[i:stop]:
            if t.kind == "name" and t.text.upper() == "EXISTS":
                raise Unsupported("EXISTS in a solution modifier")

    def balanced(self, i, open_, close):
        """Index after the bracket that closes the one at i (no group inside)."""
        depth = 0
        while True:
            t = self.tok(i)
            if t.kind == "punct" and t.text == open_:
                depth += 1
            elif t.kind == "punct" and t.text == close:
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1

    def expr(self, i, groups):
        """Index after the parenthesised expression at i; EXISTS bodies inside are read as groups."""
        depth = 0
        while True:
            t = self.tok(i)
            if t.kind == "punct" and t.text == "(":
                depth += 1
            elif t.kind == "punct" and t.text == ")":
                depth -= 1
                if depth == 0:
                    return i + 1
            elif t.kind == "name" and t.text.upper() == "EXISTS":
                if not self.p(i + 1, "{"):
                    raise Unsupported("EXISTS without a group")
                g, i = self.group(i + 1)
                groups.append(g)
                continue
            elif t.kind == "punct" and t.text in "{}":
                raise Unsupported("brace inside an expression")
            i += 1

    def group(self, i):
        """(Group, index after its "}") for the "{" at i."""
        if not self.p(i, "{"):
            raise Unsupported("expected {")
        start, j, els = self.t[i].start, i + 1, []
        if self.kw(j, "SELECT"):
            k = self.where(j)
            inner, k = self.group(k)
            end = self.close_after(k)
            els.append(Element("subselect", self.t[j].start, self.t[end - 2].end, groups=[inner],
                               vars=self.vars_between(j, k)))
            return Group(start, self.t[end - 1].end, els), end
        while True:
            t = self.tok(j)
            if t.kind == "punct" and t.text == "}":
                return Group(start, t.end, els), j + 1
            if t.kind == "punct" and t.text == ".":
                j += 1
                continue
            word = t.text.upper() if t.kind == "name" else ""
            if word == "FILTER":
                groups = []
                if self.p(j + 1, "("):
                    k = self.expr(j + 1, groups)
                elif self.kw(j + 1, "EXISTS"):
                    g, k = self.group(j + 2)
                    groups.append(g)
                elif self.kw(j + 1, "NOT") and self.kw(j + 2, "EXISTS"):
                    g, k = self.group(j + 3)
                    groups.append(g)
                elif self.tok(j + 1).kind in ("name", "pname", "iri") and self.p(j + 2, "("):
                    k = self.expr(j + 2, groups)
                else:
                    raise Unsupported("FILTER form")
                els.append(Element("filter", t.start, self.t[k - 1].end, groups=groups))
            elif word in ("OPTIONAL", "MINUS"):
                g, k = self.group(j + 1)
                els.append(Element(word.lower(), t.start, self.t[k - 1].end, groups=[g]))
            elif word == "GRAPH":
                name = self.tok(j + 1)
                if name.kind not in ("var", "iri", "pname"):
                    raise Unsupported("GRAPH name")
                g, k = self.group(j + 2)
                g.graph = name.text if name.kind == "var" else ""
                els.append(Element("graph", t.start, self.t[k - 1].end, groups=[g],
                                   vars=self.vars_between(j + 1, k)))
            elif word == "BIND":
                if not self.p(j + 1, "("):
                    raise Unsupported("BIND form")
                groups = []
                k = self.expr(j + 1, groups)
                els.append(Element("bind", t.start, self.t[k - 1].end, groups=groups, vars=self.vars_between(j, k)))
            elif word == "VALUES":
                k = j + 1
                if self.tok(k).kind == "var":
                    k += 1
                elif self.p(k, "("):
                    k = self.balanced(k, "(", ")")
                else:
                    raise Unsupported("VALUES form")
                k = self.balanced(k, "{", "}")
                els.append(Element("values", t.start, self.t[k - 1].end, vars=self.vars_between(j, k)))
            elif t.kind == "punct" and t.text == "{":
                g, k = self.group(j)
                groups = [g]
                while self.kw(k, "UNION"):
                    g, k = self.group(k + 1)
                    groups.append(g)
                els.append(Element("union" if len(groups) > 1 else "group", t.start, self.t[k - 1].end,
                                   groups=groups, vars=self.vars_between(j, k)))
            elif t.kind in ("var", "iri", "pname", "bnode"):
                triples, k = self.triples(j)
                els.append(Element("triples", t.start, self.t[k - 1].end, triples=triples,
                                   vars=set().union(*(x.vars() for x in triples))))
            else:
                raise Unsupported(f"unexpected {t.text!r} in a group")
            j = k

    def close_after(self, k):
        """Index after the "}" that closes a sub-select whose WHERE group ended at k (its modifiers in between)."""
        depth = 1
        while True:
            t = self.tok(k)
            if t.kind == "name" and t.text.upper() == "EXISTS":
                raise Unsupported("EXISTS in a solution modifier")
            if t.kind == "punct" and t.text == "{":
                depth += 1
            elif t.kind == "punct" and t.text == "}":
                depth -= 1
                if depth == 0:
                    return k + 1
            k += 1

    def vars_between(self, i, k):
        return {t.text for t in self.t[i:k] if t.kind == "var"}

    def term(self, i, subject=False):
        """(Term, next index) for a subject or an object."""
        t = self.tok(i)
        if t.kind in ("var", "iri", "pname", "bnode"):
            return Term(t.kind, t.text), i + 1
        if subject:
            raise Unsupported(f"subject {t.text!r}")
        if t.kind == "string":
            k = i + 1
            if self.tok(k).kind == "lang":
                k += 1
            elif self.p(k, "^^"):
                if self.tok(k + 1).kind not in ("iri", "pname"):
                    raise Unsupported("datatype")
                k += 2
            return Term("literal", self.q[t.start:self.t[k - 1].end]), k
        if t.kind == "number" or t.kind == "name" and t.text in ("true", "false"):
            return Term("literal", t.text), i + 1
        raise Unsupported(f"object {t.text!r}")

    def verb(self, i):
        """(Term, next index): a variable, `a`, an IRI, or a property path."""
        t = self.tok(i)
        if t.kind == "var":
            return Term("var", t.text), i + 1
        k = self.path_alt(i)
        toks = self.t[i:k]
        text = self.q[toks[0].start:toks[-1].end]
        if len(toks) == 1:
            return (Term("a", "a") if toks[0].kind == "name" else Term(toks[0].kind, text)), k
        return Term("path", text, tuple(toks)), k

    def path_alt(self, i):
        i = self.path_seq(i)
        while self.p(i, "|"):
            i = self.path_seq(i + 1)
        return i

    def path_seq(self, i):
        i = self.path_elt(i)
        while self.p(i, "/"):
            i = self.path_elt(i + 1)
        return i

    def path_elt(self, i):
        if self.p(i, "^"):
            i += 1
        t = self.tok(i)
        if t.kind in ("iri", "pname") or t.kind == "name" and t.text == "a":
            i += 1
        elif self.p(i, "("):
            i = self.path_alt(i + 1)
            if not self.p(i, ")"):
                raise Unsupported("path parentheses")
            i += 1
        else:
            raise Unsupported(f"predicate {t.text!r}")
        if self.p(i, "*", "+", "?"):
            i += 1
        return i

    def triples(self, i):
        """([Triple], index after the block) for a triples block starting with a subject at i."""
        out = []
        while True:
            s, i = self.term(i, subject=True)
            while True:
                p, i = self.verb(i)
                while True:
                    o, i = self.term(i)
                    option = ""
                    if self.kw(i, "OPTION") and self.p(i + 1, "("):
                        k = self.balanced(i + 1, "(", ")")
                        option, i = self.q[self.t[i].start:self.t[k - 1].end], k
                    out.append(Triple(s, p, o, option))
                    if not self.p(i, ","):
                        break
                    i += 1
                if not self.p(i, ";"):
                    break
                while self.p(i, ";"):
                    i += 1
                if self.p(i, ".", "}") or self.tok(i).kind == "name" and self.tok(i).text != "a":
                    break
            if not self.p(i, "."):
                return out, i
            i += 1
            if self.tok(i).kind not in ("var", "iri", "pname", "bnode"):
                return out, i


def groups(g):
    """Every group under g, g first (sub-groups, OPTIONAL and GRAPH bodies, UNION branches, EXISTS bodies, sub-selects)."""
    yield g
    for e in g.elements:
        for sub in e.groups:
            yield from groups(sub)


def read(q):
    """The WHERE group of q, or raise Unsupported."""
    r = Reader(q)
    return r.query(), r.comments
