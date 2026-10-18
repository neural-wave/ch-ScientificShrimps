> Copy of `/data/scratch/nw-data/EXPLORE.md`, the team guide to the shared data folder on the SUPSI GPU server. Paths below refer to that server; the server copy is the one to keep up to date.

# nw-data: read this first

Shared data for the Neural Wave 2026 SPHN challenge (secure NL→SPARQL over clinical
knowledge graphs). This folder contains 100,000 mock patients (58.9 GB of Turtle, 612M triples) and
the organisers' Virtuoso kit with ontologies. Everything below comes from a full pass over the data
(`tools/build_index.py`, 2026-10-16). **Answer questions from `stats/` and `MANIFEST.tsv` before
you open a patient file.**

Owner: lorenzo.varese@supsi.ch · Team: see `tools/team.txt` · Path: `/data/scratch/nw-data`

## Rules for agents (and humans)

1. **Never read a whole `.ttl` file.** Patient files range from 31 KB to **506 MB** (p99 6.7 MB). Use
   `head -n 200 FILE`, `head -c 20000 FILE` or `grep -m 20 PATTERN FILE`. Always pass a line limit
   to file-reading tools.
2. **Never `ls`, `find`, `du` or glob the patient folder**, because it holds 100,000 entries. Use `MANIFEST.tsv`.
3. **Stats first, samples second, the corpus last.** `samples/` has ready-made small, median and
   large patients. If a question needs every file, scan in parallel (see the recipes below).
4. **Restricted terminologies stay out of agent context.** Read nothing in
   `virtuoso-release/ontologies/` except `sphn_rdf_schema.ttl`. Never fetch labels or hierarchies of
   SNOMED CT, ICD-10-GM, LOINC, CHOP or UCUM into a prompt, not even through a SPARQL query. Reading
   them sends licensed content to a model provider. The licence forbids resharing, and SNOMED may not
   leave the hackathon. Code IRIs such as `snomed:15519005` in the patient data and in `stats/` are fine.
5. **Read-only.** `patients/`, `virtuoso-release/` and `archives/` are read-only, even for the owner.
   Write only under `shared/`.
6. Run Python on these files with `python3 -I`. Use only the standard library, or the project venv.
   A Turtle parser checked against rdflib already exists: `tools/build_index.py:parse()`.

## Map

| Path | What | Size |
|---|---|---|
| `EXPLORE.md` | this file | |
| `MANIFEST.tsv` | one row per patient: `patient graph path bytes triples subjects provider` | 100,001 lines |
| `stats/overview.txt` | totals, build time | 5 lines |
| `stats/sizes.txt` | size and triple percentiles, 20 largest files | |
| `stats/classes.tsv` | SPHN class → instances, patients, % patients | 164 rows |
| `stats/edges.tsv` | **the schema as used**: subject class → predicate → object class/datatype, with counts and literal min/max | 1,236 rows |
| `stats/predicates.tsv` | predicate → triples, patients | 199 rows |
| `stats/terminologies.tsv` | terminology family → distinct codes, instances, patients | 12 rows |
| `stats/terminology_namespaces.tsv` | the same per (versioned) namespace, e.g. `icd-10-gm/2021/*` | 34 rows |
| `stats/codes.tsv` | every code class used → instances, patients | 4,393 rows |
| `stats/individuals.tsv` | `sphn-individual:*` values per (class, predicate) | 289 rows |
| `stats/prefixes.ttl` | the prefix block, identical in every patient file | 62 prefixes |
| `stats/providers.tsv` | patients per data provider | 1 provider |
| `samples/*.ttl` | symlinks: `smallest` 31 KB, `p25` 103 KB, `median` 161 KB, `p75` 339 KB, `p99` 6.7 MB, `largest` **506 MB** | see `samples/INDEX.tsv` |
| `patients/gen_mock_data-1_1000001-1100000/` | 100,000 `gen_mock_data-1_<id>.ttl`, flat, one per patient | 58.9 GB |
| `virtuoso-release/` | organiser kit: Docker Virtuoso scripts, `README.md`, `API_specification.md`, `Query_Compliance.md`, `queries-example.md`, `queries/`, `shacl/`, `ontologies/` | 1 GB |
| `archives/` | the original `.tar.zst` and `.zip` files, plus `SHA256SUMS` | 4.9 GB |
| `shared/` | **team-writable** space for outputs, subsets and notes | |
| `tools/` | `build_index.py` (rebuilds MANIFEST, stats and samples in about 70 s), `acl.py` (access) | |

## What the data looks like

- **One file is one patient, which is one named graph.** The kit loads `X.ttl` into
  `<http://example.org/graph/X>`, and that IRI is in the `graph` column of `MANIFEST.tsv`. Per-patient
  counting means `GRAPH ?g { ... }` with `COUNT(DISTINCT ?g)`. See `virtuoso-release/queries-example.md`.
- **Size has a heavy tail.** The median patient has 1,635 triples, p99 has 71k and the largest has 5.2M.
  16 files are over 100 MB and 633 are between 10 and 100 MB.
- **There is one provider, `CHE-108_904_325`.** Resource IRIs look like
  `resource:CHE-108_904_325-sphn-<Class>-<uuid>`. The Turtle is rdflib-style, and every file declares
  the same 62 prefixes.
- **Turtle gotcha for grep:** long subject lines wrap, so `a` and its class are often on different
  lines (`resource:...-sphn-LabTestEvent-<uuid> a` followed by `    sphn:LabTestEvent;`). Searching for
  `'a sphn:LabTestEvent'` finds nothing, while `grep -w -F 'sphn:LabTestEvent'` matches the class count
  exactly. Class names never collide with predicates (`sphn:hasLabTest`) or IRIs (`-sphn-LabTest-`).
- **Small patients are backbone-only.** None of the 20,000 smallest files contains a LabTestEvent.
  Pick test patients from the median upwards when a query needs clinical content.
- **There is a fixed backbone.** 34 classes appear exactly in every patient. They include
  AdministrativeCase, AdministrativeSex, Age, Birth, BirthDate, Consent, DataProvider and
  SubjectPseudoIdentifier. Everything else varies. For example: DrugPrescription 60%, Diagnosis 58%,
  LabTestEvent 55%, BilledDiagnosis 48%, DeathDate 33%. `stats/classes.tsv` has all of them.
- **Codes are typed instances.** A concept points to a code node with `sphn:hasCode`, and that node is
  *typed* with the code class:
  ```turtle
  resource:...-sphn-BilledDiagnosis-<uuid> sphn:hasCode resource:...-sphn-Code-ICD-10-GM-2023-A00 .
  resource:...-sphn-Code-ICD-10-GM-2023-A00 a <https://biomedit.ch/rdf/sphn-resource/icd-10-gm/2023/A00> .
  ```
  ICD-10-GM and CHOP codes are **year-versioned**: 13 namespaces each, from 2013 to 2024. The official
  examples match a code across all versions through the ontology's inference rule set:
  `?dx_code a icd-10-gm:A00 OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global") .`
- **Where the codes are:**
  - `sphn:Diagnosis hasCode` points to a generic `sphn:Code`, which carries a string identifier and is filler.
  - ICD-10-GM codes are on `sphn:BilledDiagnosis` and `sphn:OncologyDiagnosis`.
  - Lab tests and administrative sex are coded in SNOMED CT.
  - The terminologies present are snomed (3,410 codes, every patient), ucum, edam, emdn, efo,
    icd-10-gm (94 codes, 49% of patients), obi, chop (35 codes, 32%), hgnc, geno, so and genepio.
  - **LOINC, ORDO and ATC never occur in the patient data**, although LOINC and ORDO ontologies ship with the kit.
- **Literal values are synthetic noise.** Strings are lorem ipsum. Birth years run from -0026 to 9996,
  and `Quantity hasValue` from 0 to 9999. Don't sanity-check query answers by plausibility. Compliance
  verdicts and execution time are what count. In `edges.tsv`, min/max are numeric for numbers and
  lexical for everything else.

## Recipes

```bash
cd /data/scratch/nw-data
T=$'\t'

# Which classes exist and how common they are
column -t -s"$T" stats/classes.tsv | head -60
# Everything hanging off a class (outgoing edges)
grep -P "^sphn:LabTestEvent\t" stats/edges.tsv | column -t -s"$T"
# Who points to a class (incoming edges)
awk -F"$T" '$3=="sphn:LabTest"' stats/edges.tsv | column -t -s"$T"
# Is a code present, and in how many patients
grep -F 'icd-10-gm/2021/A00' stats/codes.tsv
# Allowed values of an individual-valued property
grep -P "\tsphn:hasConsequences\t" stats/individuals.tsv
# File and graph IRI of one patient
grep -m1 -P "^gen_mock_data-1_1012345\t" MANIFEST.tsv
# The 20 smallest patients (backbone-only, good for smoke tests)
tail -n +2 MANIFEST.tsv | sort -t"$T" -k4,4n | head -20
# Real Turtle for one instance of a class (small patients are backbone-only: use p75/p99)
grep -m1 -B1 -A12 -w -F 'sphn:LabTestEvent' samples/p75.ttl
# Full-corpus scan (read-only, parallel; under a second when cached, about a minute cold).
# Patients having a class: grep the class name with -w, NOT 'a sphn:X' (see the Turtle gotcha)
tail -n +2 MANIFEST.tsv | cut -f3 | xargs -P 32 -n 500 grep -l -w -F 'sphn:LabTestEvent' | wc -l   # 55136
```

**Subset of patients for a smaller Virtuoso.** On this XFS volume, `cp --reflink=always` is instant
and costs no disk space. It works for anyone with read access:

```bash
D=/data/scratch/nw-data/shared/subsets/random-5000; mkdir -p "$D"
tail -n +2 MANIFEST.tsv | shuf -n 5000 --random-source=<(yes) | cut -f3 | xargs cp --reflink=always -t "$D"
```

## Running Virtuoso on this data

The kit's scripts can run in place, because they never write into their own folder (every ontology
is a `.ttl` file, so `.ontology_cache/` stays empty). Give your instance its own name, ports and RAM.
**Check `docker ps` first**: `hackathon-virtuoso` on 8890/1111 may already be taken, and so are
8000–8002 and 8899–8900.

```bash
cd /data/scratch/nw-data/virtuoso-release
export CONTAINER_NAME=nw-virtuoso-<yourname> HTTP_HOST_PORT=8891 ISQL_HOST_PORT=1112
export PATIENTS_DIR=/data/scratch/nw-data/patients/gen_mock_data-1_1000001-1100000   # or a subset under shared/
export VIRTUOSO_RAM_GB=64      # REQUIRED on this shared box: the default is 80% of host RAM (~400 GB)
./start.sh && ./load-patients.sh
./query.sh queries/count-per-graph.rq --out /data/scratch/nw-data/shared/<you>/out.txt
```

- Mounts are fixed when a container is created. To change `PATIENTS_DIR`, run `./destroy.sh` and then `./start.sh`.
- Consider sharing one fully loaded instance with the team instead of loading 612M triples per person.
- The kit README mentions `generate-patients.sh`, but that script is **not** in the release zip.

## Access and maintenance

- Only the team can read this folder (POSIX ACLs; group and other get nothing). `setfacl` and
  `getfacl` are not installed. Use `python3 -I tools/acl.py show <path>` to see an ACL. The owner adds
  someone with `python3 -I tools/acl.py grant <user@supsi.ch>`.
- Members of the `docker` group are effectively root and can read everything regardless of ACLs.
- `shared/`: files you create there inherit team read/write. If a teammate can't overwrite one of your
  files (for example after `cp -p` from a read-only source), run `chmod g+rw <file>`. On ACL files, the
  group bits act as the ACL mask.
- To add a new patient batch, the owner runs: `chmod u+w patients`, extracts it to
  `patients/<batch>/`, then runs `python3 -I tools/build_index.py /data/scratch/nw-data` and
  `python3 -I tools/acl.py apply`.
- To check the archives' integrity: `cd archives && sha256sum -c SHA256SUMS`.
