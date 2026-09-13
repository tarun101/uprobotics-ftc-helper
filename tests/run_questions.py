"""Run the 30-question test (and the bad-question list) against the Helper.

Usage: .venv/bin/python tests/run_questions.py [--endpoint URL] [--bad] [--out results.md] [--grade] [--fail-ping] [--commit]

The default endpoint is the API Worker the page uses. Without --grade, the script prints each answer with the
cited item keys for a human to grade against tests/questions-30.md.

--grade scores each question by whether one of the cited item keys matches the expected source (a retrieval
check: it catches a rule that stops being retrieved, not a wrong sentence; a human still judges answer text)
and exits 1 when fewer than PASS_MARK questions pass. --fail-ping sends the Monitor a failure signal
(<ping URL>/fail) in that case, so the same email path that reports a dead Mac mini reports a quality drop.
--commit adds the results file to git and commits it (no push).
"""
import argparse, json, re, subprocess, sys, time, pathlib
import requests

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_EP = "https://ftc.uprobotics.tech/api"
PASS_MARK = 27


def load_questions(path: pathlib.Path):
    rows = []
    for line in path.read_text().splitlines():
        m = re.match(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|", line)
        if m:
            rows.append((int(m.group(1)), m.group(2), m.group(3), m.group(4)))
    return rows


def load_bad(path: pathlib.Path):
    return [(int(m.group(1)), "bad", m.group(2).strip(), "decline/redirect") for m in
            (re.match(r"^(\d+)\.\s+(.*)$", l) for l in path.read_text().splitlines()) if m]


def ask(ep: str, q: str):
    r = requests.post(f"{ep.rstrip('/')}/chat/completions", json={"messages": [{"role": "user", "content": q}]}, timeout=120)
    r.raise_for_status()
    d = r.json()
    answer = d.get("choices", [{}])[0].get("message", {}).get("content", "")
    keys = [c.get("item", {}).get("key", "?") for c in d.get("chunks", [])]
    return answer, keys, d.get("usage", {}).get("neurons")


# ---------------------------------------------------------------- grading

def expected_prefixes(expected: str, category: str) -> list[str]:
    """Item-key prefixes that satisfy an 'Expected source(s)' cell of tests/questions-30.md."""
    pre: list[str] = []
    for m in re.finditer(r"#([A-Z]{1,2}\d{3})", expected):
        pre.append(f"manual--{m.group(1)}--")
    for m in re.finditer(r"[Ss]ection\s+(\d+(?:\.\d+)*)((?:\s*/\s*\d+(?:\.\d+)*)*)", expected):
        pre.append(f"manual--sec-{m.group(1)}")
        for extra in re.findall(r"\d+(?:\.\d+)*", m.group(2) or ""):
            pre.append(f"manual--sec-{extra}")
    if "team_update" in expected:
        pre.append("team_update--")
    if re.search(r"\bhub\b", expected):
        pre.append("hub--")
    if expected.lower().startswith("video"):
        pre.append("video--")
    if "Event Rules" in expected:
        pre.append("manual--E")
    if category == "Programming":
        pre.append("web--")           # FTC Docs, REV, and gm0 pages are valid programming sources too
    if not pre:
        pre.append("manual--")
    return pre


def grade(category: str, expected: str, keys: list[str], answer: str) -> tuple[bool, str]:
    pre = expected_prefixes(expected, category)
    hit = next((k for k in keys if any(k.startswith(p) for p in pre)), None)
    if not hit:
        return False, f"no cited key matches {pre}"
    if category == "Older season" and not re.search(r"earlier season|previous season|older season|20(1\d|2[0-5])", answer, re.I):
        return False, "answer does not say the video is from an earlier season"
    if not re.search(r"\]\(https?://", answer):
        return False, "answer has no Markdown link"
    return True, hit


def fail_ping():
    sys.path.insert(0, str(REPO))
    import ftc_index
    url = ftc_index.keychain("monitor-ping-url", "FTC_MONITOR_PING_URL")
    if not url:
        print("no monitor ping URL configured; cannot send the failure signal")
        return
    try:
        requests.get(url.rstrip("/") + "/fail", timeout=20)
        print("monitor failure signal sent")
    except Exception as e:
        print(f"monitor failure signal failed: {e}")


def commit_results(path: pathlib.Path):
    try:
        rel = path.resolve().relative_to(REPO)
        subprocess.run(["git", "-C", str(REPO), "add", str(rel)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(REPO), "commit", "-q", "-m", f"Weekly question test: {path.name}", "--", str(rel)],
                       check=True, capture_output=True)
        print(f"committed {rel}")
    except Exception as e:
        print(f"could not commit results: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default=DEFAULT_EP)
    ap.add_argument("--bad", action="store_true", help="run tests/bad-questions.md instead")
    ap.add_argument("--out")
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--grade", action="store_true", help="auto-grade by cited keys; exit 1 below the pass mark")
    ap.add_argument("--fail-ping", action="store_true", help="with --grade: signal the Monitor when below the pass mark")
    ap.add_argument("--commit", action="store_true", help="git commit the --out file (no push)")
    a = ap.parse_args()
    rows = load_bad(HERE / "bad-questions.md") if a.bad else load_questions(HERE / "questions-30.md")
    if a.only:
        rows = [r for r in rows if r[0] in a.only]
    stamp = time.strftime("%Y-%m-%d %H:%M")
    out = [f"# Results {stamp} — {'bad questions' if a.bad else '30 questions'} — endpoint {a.endpoint}\n"]
    passed, failures = 0, []
    for n, cat, q, expected in rows:
        try:
            answer, keys, neurons = ask(a.endpoint, q)
        except Exception as e:
            answer, keys, neurons = f"ERROR {e}", [], None
        verdict = ""
        if a.grade and not a.bad:
            ok, why = grade(cat, expected, keys, answer)
            passed += ok
            if not ok:
                failures.append(f"{n} ({why})")
            verdict = f"**Retrieval check:** {'pass' if ok else 'FAIL'} — {why}\n"
        block = f"\n## {n}. [{cat}] {q}\n**Expected:** {expected}\n**Cited:** {', '.join(dict.fromkeys(keys)) or '(none)'}\n{verdict}\n{answer.strip()}\n"
        out.append(block)
        print(block)
        sys.stdout.flush()
        time.sleep(1.2)  # stay under 60 requests/minute
    rc = 0
    if a.grade and not a.bad:
        line = f"**Score: {passed}/{len(rows)} (pass mark {PASS_MARK})**" + (f"; failed: {', '.join(failures)}" if failures else "")
        out.insert(1, line + "\n")
        print("\n" + line)
        if passed < PASS_MARK:
            rc = 1
            if a.fail_ping:
                fail_ping()
    if a.out:
        p = pathlib.Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(out))
        print(f"\nwrote {a.out}")
        if a.commit:
            commit_results(p)
    return rc


if __name__ == "__main__":
    sys.exit(main())
