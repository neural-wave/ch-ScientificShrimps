"""One parallel pass over the 100,000 patient files for the optimizer's metadata v2 (not Virtuoso): per (subject class,
predicate) the subjects that have it and the most values one subject has, per class the most instances one patient has.
Counts only. It reuses the Turtle reader of nw-data/tools/build_index.py and writes two TSVs next to its stats:
  flock /data/scratch/lorenzo.varese@supsi.ch/virtuoso-8990.lock \\
    python -I -c "import sys; sys.path.insert(0, '<worktree>'); from nl2q.tools.metadata_scan import main; main()"
The scan reads ~59 GB: run it under the Virtuoso lock so that it never overlaps a timed query."""
import collections, importlib.util, os, sys, time
from multiprocessing import Pool

ROOT = "/data/scratch/nw-data"
spec = importlib.util.spec_from_file_location("build_index", f"{ROOT}/tools/build_index.py")
BI = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BI)


def scan(path):
    try:
        with open(path, "rb") as f:
            _, types, triples = BI.parse(f.read().decode("utf-8"))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    values = collections.Counter((s, p) for s, p, *_ in triples)  # values per (subject, predicate)
    has, most = collections.Counter(), {}
    for (s, p), n in values.items():
        for c in types.get(s, ()):
            if c.startswith("sphn:"):
                has[(c, p)] += 1
                most[(c, p)] = max(most.get((c, p), 0), n)
    inst = collections.Counter(c for cs in types.values() for c in cs if c.startswith("sphn:"))
    return {"has": has, "most": most, "inst": inst}


def main(workers=64):
    files = [e.path for b in sorted(os.listdir(f"{ROOT}/patients")) if os.path.isdir(f"{ROOT}/patients/{b}")
             for e in os.scandir(f"{ROOT}/patients/{b}") if e.name.endswith(".ttl")]
    files.sort(key=os.path.getsize, reverse=True)  # biggest first, so the slow ones do not straggle at the end
    t0, has, most, pmax, errors = time.time(), collections.Counter(), {}, {}, 0
    with Pool(workers) as pool:
        for i, r in enumerate(pool.imap_unordered(scan, files, chunksize=4), 1):
            if "error" in r:
                errors += 1
                continue
            has.update(r["has"])
            for k, n in r["most"].items():
                most[k] = max(most.get(k, 0), n)
            for c, n in r["inst"].items():
                pmax[c] = max(pmax.get(c, 0), n)
            if i % 20000 == 0:
                print(f"{i}/{len(files)} files, {time.time() - t0:.0f} s", flush=True)
    with open(f"{ROOT}/stats/subject_predicates.tsv", "w") as f:
        f.write("subject\tpredicate\tsubjects\tmax_per_subject\n")
        f.writelines(f"{c}\t{p}\t{n}\t{most[(c, p)]}\n" for (c, p), n in sorted(has.items()))
    with open(f"{ROOT}/stats/class_max.tsv", "w") as f:
        f.write("class\tmax_per_patient\n")
        f.writelines(f"{c}\t{n}\n" for c, n in sorted(pmax.items()))
    print(f"{len(files)} files in {time.time() - t0:.0f} s, {errors} parse errors; {len(has)} (class, predicate), "
          f"{len(pmax)} classes -> {ROOT}/stats/subject_predicates.tsv, class_max.tsv")


if __name__ == "__main__":
    main()
