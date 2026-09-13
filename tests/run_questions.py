"""Run the 30-question test (and the bad-question list) against the public /chat/completions endpoint.

Usage: .venv/bin/python tests/run_questions.py [--endpoint URL] [--bad] [--out results.md]
Prints each answer with the cited item keys so a human can grade against tests/questions-30.md.
"""
import argparse, json, re, sys, time, pathlib
import requests

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_EP = "https://146c9951-a803-43ec-82a5-667148163702.search.ai.cloudflare.com"


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
    r = requests.post(f"{ep}/chat/completions", json={"messages": [{"role": "user", "content": q}]}, timeout=120)
    r.raise_for_status()
    d = r.json()
    answer = d.get("choices", [{}])[0].get("message", {}).get("content", "")
    keys = [c.get("item", {}).get("key", "?") for c in d.get("chunks", [])]
    return answer, keys, d.get("usage", {}).get("neurons")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default=DEFAULT_EP)
    ap.add_argument("--bad", action="store_true", help="run tests/bad-questions.md instead")
    ap.add_argument("--out")
    ap.add_argument("--only", type=int, nargs="*")
    a = ap.parse_args()
    rows = load_bad(HERE / "bad-questions.md") if a.bad else load_questions(HERE / "questions-30.md")
    if a.only:
        rows = [r for r in rows if r[0] in a.only]
    out = [f"# Results {time.strftime('%Y-%m-%d %H:%M')} — {'bad questions' if a.bad else '30 questions'}\n"]
    for n, cat, q, expected in rows:
        try:
            answer, keys, neurons = ask(a.endpoint, q)
        except Exception as e:
            answer, keys, neurons = f"ERROR {e}", [], None
        block = f"\n## {n}. [{cat}] {q}\n**Expected:** {expected}\n**Cited:** {', '.join(dict.fromkeys(keys)) or '(none)'}\n\n{answer.strip()}\n"
        out.append(block)
        print(block)
        sys.stdout.flush()
        time.sleep(1.2)  # stay under 60 requests/minute
    if a.out:
        pathlib.Path(a.out).write_text("\n".join(out))
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
