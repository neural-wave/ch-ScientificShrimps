# Hackathon Virtuoso

A ready-to-run Virtuoso triplestore for the hackathon. Drop your patient `.ttl` files in a
folder, run two scripts, and query them — with the same container setup, bulk-loading strategy
and RDFS inference as our production pipeline.

Docker is the only requirement. No Python, no build step.

## Quick start

```bash
./start.sh           # boots Virtuoso, loads the ontologies, registers the bulk loader
./load-patients.sh   # loads every patients/*.ttl, one named graph per file
./query.sh queries/count-per-graph.rq
```

Then browse to the SPARQL endpoint at <http://localhost:8890/sparql>.

To try it before you have data, use the two example files:

```bash
./start.sh && ./load-patients.sh
```

## Scripts

| Script | What it does |
| --- | --- |
| `./start.sh` | Starts the container (sizing the buffer pool from available RAM), waits until Virtuoso answers, registers the bulk-loader procedure, and loads the ontologies. Resumes a stopped container instead of recreating it. |
| `./load-patients.sh [--reset]` | Bulk-loads every `.ttl` in the patients folder. `--reset` clears previously loaded patient graphs first. |
| `./generate-patients.sh --patients N --concepts K` | Writes N synthetic patients into the patients folder, recombined from the existing ones — see [Scaling up the cohort](#scaling-up-the-cohort). |
| `./load-ontologies.sh [--force]` | Loads `ontologies/` (called by `start.sh`). Skips if already loaded; `--force` reloads. |
| `./query.sh <file.rq \| "SPARQL text"> [--out results.txt]` | Runs a query and prints the results plus Virtuoso's own server-side timing. |
| `./reset-patients.sh` | Clears all patient graphs, keeping the ontologies loaded. |
| `./status.sh` | Shows what is running and how much is loaded. |
| `./stop.sh` | Stops the container but **keeps** everything loaded — `./start.sh` resumes it. |
| `./destroy.sh` | Removes the container and its data. The next `./start.sh` starts empty and reloads the ontologies. |

Your `.ttl` files on disk are never modified or deleted by any of these.

## Your patient files

Put them in `patients/` (or point `PATIENTS_DIR` elsewhere). Requirements:

- **Turtle only.** Loading uses Virtuoso's Turtle parser; `.nt`, `.rdf` and `.jsonld` files are
  ignored. Anything not ending in `.ttl` is skipped, so a stray `.DS_Store` is harmless.
- **One file per patient**, flat in the folder (subfolders are not scanned).
- **The file name becomes the graph name**: `patient-42.ttl` is loaded into the named graph
  `http://example.org/graph/patient-42`. Keep names unique.

That per-file named graph is what lets a query count or filter per patient, e.g.
`SELECT DISTINCT ?g WHERE { GRAPH ?g { ... } }`.

## Scaling up the cohort

A thousand patients fit in memory and every reasonable query answers in milliseconds, so query
performance only shows with more data. `generate-patients.sh` adds synthetic patients next to the
existing ones:

```bash
./generate-patients.sh --patients 20000 --concepts 150 --seed 42       # total ~ normal(150, ~12)
./generate-patients.sh --patients 20000 --concepts 100-200            # total ~ normal(150, 16.7), within 100-200
./load-patients.sh
```

Each new patient gets a backbone (pseudo identifier, data provider, source system, administrative
case, administrative sex) plus patient-level records — diagnoses, lab test events, measurements,
procedures, ... — copied from a pool of existing patients together with everything they link to
(codes, quantities, units, results), under fresh IRIs and identifiers.

Unlike the mock data, where every patient has exactly one record of each type, **every concept type
gets its own normal distribution of records per patient**, without having to name any. A type's
mean is its Zipf-like share of the average total `--concepts`, so some concepts are common across
the cohort and others rare (`--skew 0` gives every type an equal share; higher values let a few
types dominate), and its standard deviation is √mean, the natural spread of a count. A patient's
total is the sum of these, itself normal. A draw below zero means the patient has none of that
concept, which keeps rare types rare; the draws are centred so that each type still averages its
mean. The script prints each type's distribution before generating, and how many types average
below one record per patient — those show up 0 or 1 times rather than as a visible bell curve, so
raise `--concepts` or lower `--skew` if you want one on every type.

Two things bend the plan, both to keep patients consistent. Birth, death and vital status appear at
most once per patient (their share goes to the other types). And a record that links to another
patient-level record — a lab test event to its sample, a diagnosis to the patient's age, a birth to
its date — reuses one the patient already has, or brings one along if there is none yet, so those
linked types average at least about one wherever something needs them; totals run a few percent
above `--concepts`.

`--concept TYPE=MEAN[:SD]` optionally pins a type to a distribution of its own instead of its share.
Types are the patient-level ones — `LabTestEvent`, not `LabTest`, which hangs off an event; the
script suggests the right name if you pick one that isn't.

| Option | Default | Notes |
| --- | --- | --- |
| `--patients N` | required | Number of patients to generate. |
| `--concepts K` or `MIN-MAX` | required | Average patient-level records per patient, shared out over all concept types. `MIN-MAX` averages its midpoint and keeps each patient's total within the range (patients outside it are redrawn). |
| `--concepts-sd SD` | ≈√`K` for `K`, `(MAX-MIN)/6` for `MIN-MAX` | Spread of the total, reached by scaling every type's spread; must be > 0. With many rare types the total comes out somewhat narrower. |
| `--skew S` | `1.0` | Zipf exponent for how unevenly the concept types share the total. |
| `--concept TYPE=MEAN[:SD]` | none | Optional: pin a type to ~ normal(`MEAN`, `SD`) per patient, `SD` defaulting to √`MEAN`; taken out of the total, repeatable. `LabTestEvent=2:0` gives every patient exactly two. |
| `--donors N` | `100` | Existing files to sample records from. |
| `--seed N` | random | Makes the output reproducible. |
| `--prefix NAME` | `generated` | Files are `NAME-000001.ttl`, ...; numbering continues after existing ones, so running again adds more. |

Sizes, measured on 1,000 generated patients (a mock patient is ~330 KB and ~3,400 triples):

| `--concepts` | Per patient | 100,000 patients |
| --- | --- | --- |
| `50` | ~6 ms, ~205 KB, ~2,100 triples | ~10 minutes, ~20 GB of disk, ~210 million triples |
| `150` | ~11 ms, ~550 KB, ~5,700 triples | ~18 minutes, ~55 GB of disk, ~570 million triples |

Size `VIRTUOSO_RAM_GB` and the disk accordingly. It needs
`python3`; without it the script runs the generator in a `python:3-alpine` container. To remove
generated patients again: `rm patients/generated-*.ttl && ./load-patients.sh --reset`.

## Ontologies and inference (optional but recommended)

Put the SPHN schema and any terminology files (`.ttl`, or `.zip` archives containing `.ttl`) into
`ontologies/`. `start.sh` loads them all into a single graph and registers that graph as an
inference rule set — one merged graph and one `RDFS_RULE_SET` call, exactly as production does,
so class- and property-hierarchy reasoning behaves the same way here.

A query then opts into reasoning per triple pattern:

```sparql
?lab_test rdf:type sphn:LabTest OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global") .
```

Without that clause, a query matches only the triples literally present in your data. Without any
ontology files loaded, a query using the clause fails with *"refers to undefined inference rule
set"* — load the ontologies, or drop the clause.

Extracted archives are cached in `.ontology_cache/`, so a large terminology is only unpacked once.
Loading SNOMED CT-sized files takes several minutes the first time; after that, `./stop.sh` and
`./start.sh` resume without reloading anything.

## Querying

Three interchangeable ways in:

```bash
./query.sh queries/classes.rq                          # from a file, with timing
./query.sh 'SELECT * WHERE { GRAPH ?g { ?s ?p ?o } } LIMIT 10'
docker exec -it hackathon-virtuoso isql 1111 dba dba   # interactive isql shell
```

…plus the HTTP endpoint at <http://localhost:8890/sparql>, which any SPARQL client or a plain
`curl` can use.

The `queries/` folder has four examples to start from: `count-triples.rq`, `count-per-graph.rq`,
`classes.rq`, and `with-inference.rq`.

The time `query.sh` reports is the figure isql prints — Virtuoso's own server-side elapsed time,
excluding Docker and connection overhead, so it's the number worth comparing between two queries.

In the interactive shell, prefix SPARQL with `SPARQL` and end with `;`:

```
SQL> SPARQL SELECT * WHERE { GRAPH ?g { ?s ?p ?o } } LIMIT 5;
```

## Configuration

Every setting in `config.sh` can be overridden from the environment:

```bash
PATIENTS_DIR=~/data/cohort-a ./start.sh
VIRTUOSO_VERSION=7.2.13 ./start.sh
HTTP_HOST_PORT=8891 ISQL_HOST_PORT=1112 CONTAINER_NAME=my-virtuoso ./start.sh
```

| Variable | Default | Notes |
| --- | --- | --- |
| `VIRTUOSO_VERSION` | `7.2.16` | Any `openlink/virtuoso-opensource-7` tag. |
| `CONTAINER_NAME` | `hackathon-virtuoso` | Change it to run several side by side (give each its own ports too). |
| `PATIENTS_DIR` | `./patients` | Fixed when the container is created — see below. |
| `ONTOLOGIES_DIR` | `./ontologies` | Same. |
| `ISQL_HOST_PORT` / `HTTP_HOST_PORT` | `1111` / `8890` | Change if the ports are taken. |
| `GRAPH_URI_PREFIX` | `http://example.org/graph/` | Prefix of the per-file graph names. |
| `RULE_SET_URI` | `https://biomedit.ch/rdf/sphn-resource/global` | Must match the `OPTION (inference ...)` value in your queries. |
| `LOAD_WORKER_COUNT` | `8` | Parallel loader workers. |
| `VIRTUOSO_RAM_GB` | auto | Buffer pool budget; defaults to 80% of the RAM Docker reports. |

Folders are bind-mounted when the container is **created**, so changing `PATIENTS_DIR` or
`ONTOLOGIES_DIR` afterwards needs `./destroy.sh` then `./start.sh`. `load-patients.sh` checks this
and tells you if they've drifted apart.

## How the loading works

Worth knowing if you're comparing timings or loading a lot of files:

- Files are loaded through Virtuoso's own `load_list` queue: the bulk-loader procedure queues one
  row per file, then **8 parallel `rdf_loader_run()` workers** each pull unclaimed rows off the
  queue. This is Virtuoso's documented way to parallelize a bulk load and is dramatically faster
  than loading files one at a time — it scales to hundreds of thousands of files.
- During the load, checkpointing and transaction logging are disabled and restored afterwards
  (standard bulk-load tuning). Your files are the source of truth, so the trade is free here.
- The buffer pool is sized from the RAM Docker reports, not left at Virtuoso's tiny default.
- Virtuoso's pre-execution cost-estimate guard is disabled, so an expensive exploratory query
  runs instead of being refused with *"the estimated execution time exceeds the limit"*.
- Reloading is additive: the same file loaded twice yields the same triples, but if you **edit** a
  file, use `./load-patients.sh --reset` so removed triples actually disappear.

## Troubleshooting

**`Docker is not running`** — start Docker Desktop (or your daemon) and retry.

**Port already in use** — something else holds 1111 or 8890:
`HTTP_HOST_PORT=8891 ISQL_HOST_PORT=1112 ./start.sh`.

**Some files failed to load** — `load-patients.sh` prints the offending files and Virtuoso's parse
error. Almost always malformed Turtle or a missing prefix declaration in that one file; fix it and
rerun. To see them again:

```bash
docker exec hackathon-virtuoso isql 1111 dba dba \
  "exec=SELECT ll_file, ll_error FROM DB.DBA.load_list WHERE ll_error IS NOT NULL;"
```

**A query returns 0 rows** — check `./status.sh` shows your graphs loaded, then confirm your
patterns match the data as written: `./query.sh queries/classes.rq` lists which types are actually
present. If you're relying on `OPTION (inference ...)`, confirm the ontologies loaded.

**`refers to undefined inference rule set`** — no ontology files were loaded. Put them in
`ontologies/` and run `./load-ontologies.sh`.

**A query is slow** — get the plan Virtuoso chose:

```bash
docker exec hackathon-virtuoso isql 1111 dba dba \
  "exec=EXPLAIN ('SPARQL SELECT * WHERE { GRAPH ?g { ?s ?p ?o } }');"
```

Adding `DEFINE sql:select-option "order"` at the top of a query forces Virtuoso to evaluate the
triple patterns in the order you wrote them, which is the usual lever when its own join order is
the problem.

**Container logs** — `docker logs hackathon-virtuoso`.


ATTENTION:  SNOMED-CT terminology must not use or distribute the SNOMED files outside the hackathon. If participants wish to use SNOMED outside the hackathon, they should also register via the MLDS and apply for a licence.
