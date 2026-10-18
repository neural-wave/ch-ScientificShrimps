"""The held-out clean test (nl2q_data/cleantest/clean.jsonl, built by someone who never tuned the system): run it ONCE,
on a frozen version, and report the score. It checks the file's SHA-256 first, and prints ids, verdicts, seconds and
tokens only, never the questions, so the session that runs it still has not seen them.
  python -m nl2q.tools.clean_test                 # end to end through pipeline.translate (settings from the environment)
  python -m nl2q.tools.clean_test --out nl2q_data/runs/NN-clean"""
import argparse, hashlib, json, statistics, time
from pathlib import Path
from .. import settings as S

FILE = S.DATA / "cleantest" / "clean.jsonl"
SHA256 = "1610c015634e7d0cf7201d0924a807e239574d0728c6364aaaffc445bd414f56"  # recorded when the test was built


def main():
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("--file", type=Path, default=FILE)
    a.add_argument("--out", type=Path, help="folder for summary.txt and trace.jsonl (no questions in either)")
    args = a.parse_args()
    raw = args.file.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SHA256:
        raise SystemExit(f"{args.file} changed since it was built (SHA-256 differs): not running it")
    from ..pipeline import translate
    rows = []
    for line in raw.decode().splitlines():
        t = json.loads(line)
        t0 = time.perf_counter()
        r = translate(t["question"])
        rows.append({"id": t["id"], "expected": t["compliant"], "rule": t["rule"], "got": r["is_compliant"],
                     "reason": (r["compliance_reason"] or "")[:80], "seconds": round(time.perf_counter() - t0, 2),
                     "tokens": r["tokens_consumed"], "attempts": len(r["attempts"])})
        x = rows[-1]
        print(f"{x['id']} {'ok   ' if x['got'] == x['expected'] else 'WRONG'} expected={'safe' if x['expected'] else x['rule']} "
              f"{x['seconds']:5.2f}s {x['tokens']:6d} tokens", flush=True)
    right = sum(x["got"] == x["expected"] for x in rows)
    summary = "\n".join([
        f"verdicts        {right}/{len(rows)} right",
        f"false compliant {sum(x['got'] and not x['expected'] for x in rows)} {[x['id'] for x in rows if x['got'] and not x['expected']]}",
        f"false refusal   {sum(x['expected'] and not x['got'] for x in rows)} {[x['id'] for x in rows if x['expected'] and not x['got']]}",
        f"seconds         mean {statistics.mean(x['seconds'] for x in rows):.2f}, median {statistics.median(x['seconds'] for x in rows):.2f}, max {max(x['seconds'] for x in rows):.2f}",
        f"tokens          mean {statistics.mean(x['tokens'] for x in rows):.0f}, median {statistics.median(x['tokens'] for x in rows):.0f}",
        f"settings        plan={S.PLAN} plan_model={S.PLAN_MODEL} candidates={S.PLAN_CANDIDATES} model={S.LLM_MODEL}"])
    print(summary)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "summary.txt").write_text(summary + "\n")
        (args.out / "trace.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))


if __name__ == "__main__":
    main()
