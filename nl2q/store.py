"""Virtuoso over the SPARQL protocol. Used by the evaluation only: the API never runs the queries it writes."""
import time
import requests
from . import settings as S


def run(query, timeout=300):
    """(columns, rows) with the values as strings."""
    r = requests.post(S.SPARQL_ENDPOINT, data={"query": query}, headers={"Accept": "application/sparql-results+json"}, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    cols = j["head"]["vars"]
    return cols, [tuple(b[c]["value"] if c in b else None for c in cols) for b in j["results"]["bindings"]]


def timed(query, n=10):
    """Mean wall time in seconds over n runs (the jury averages 10 isolated runs)."""
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        run(query)
        ts.append(time.perf_counter() - t0)
    return sum(ts) / n
