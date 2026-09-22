// Run: node --test tests/worker_links.test.mjs   (checks the Worker's link verification and Sources list)
import test from "node:test";
import assert from "node:assert/strict";
import { withVerifiedLinks, retrievedSources, normUrl, renderAnswerMarkdown, categorizeQuestion, questionKey, answerSources, structuredQuestion } from "../page/src/index.js";

const manual = "https://ftc-resources.firstinspires.org/ftc/game/cm-html/BIOBUZZ%20Competition%20Manual%20-%20V1.htm#G202";
const chunks = [
  { item: { key: "manual--G202--abcd1234.md" }, text: `# G202 Follow the CIC\nSource: FTC BIOBUZZ Competition Manual V1\nLink: ${manual}\nType: rule\n---\n\n**G202** ...` },
  { item: { key: "video--abc-0300--9f8e7d6c.md" }, text: "# Game Breakdown — 5:00 to 7:30\nChannel: Brogan M. Pratt\nVideo: https://www.youtube.com/watch?v=abc\nLink (this moment): https://www.youtube.com/watch?v=abc&t=300s\nType: video transcript window\n---\n\nwords" },
  { item: { key: "manual--G202--abcd1234.md" }, text: "a later chunk of the same item with no header" },
  { item: { key: "web--gm0-intake--11112222.md" }, text: "no header here either" },
];

test("keeps links retrieval returned, strips the rest, appends Sources", () => {
  const raw = `Yes. [Rule G202](${manual}) says so.\nSee also [the archive](https://example.org/made-up) and https://example.org/bare.`;
  const r = withVerifiedLinks(raw, chunks);
  assert.equal(r.removed, 2);
  assert.match(r.text, /\[Rule G202\]\(https:\/\/ftc-resources/);
  assert.ok(!r.text.includes("example.org"), "invented links are gone");
  assert.ok(r.text.includes("See also the archive and ."), "label survives as plain text");
  assert.ok(r.text.includes("**Sources**"));
  assert.ok(r.text.includes(`- [G202 Follow the CIC](${manual})`));
  assert.ok(r.text.includes("- [Game Breakdown — 5:00 to 7:30 — Brogan M. Pratt](https://www.youtube.com/watch?v=abc&t=300s)"));
  assert.equal(r.listed, 2, "header-less items are not listed");
});

test("percent-encoded and trailing-punctuation forms of the same URL match", () => {
  const decoded = "https://ftc-resources.firstinspires.org/ftc/game/cm-html/BIOBUZZ Competition Manual - V1.htm#G202";
  assert.equal(normUrl(manual), normUrl(decoded + ")."));
  const r = withVerifiedLinks(`[G202](${decoded})`, chunks);
  assert.equal(r.removed, 0);
});

test("the official hub is always allowed; declines get no Sources list", () => {
  const r = withVerifiedLinks("The sources don't cover this. See the [Competition Manual](https://ftc-resources.firstinspires.org/ftc/game).", []);
  assert.equal(r.removed, 0);
  assert.ok(!r.text.includes("**Sources**"), "nothing retrieved, nothing listed");
  const d = withVerifiedLinks("Sorry, I can only help with FTC robotics questions.", chunks);
  assert.ok(!d.text.includes("**Sources**"), "an answer without links is left alone");
});

test("Sources list caps at five and dedupes by link", () => {
  const many = Array.from({ length: 8 }, (_, i) => ({ item: { key: `manual--R${i}--x.md` }, text: `# R${i} title\nLink: https://x.org/r#${i}\n---\nbody` }));
  many.push({ item: { key: "manual--dup--y.md" }, text: "# dup\nLink: https://x.org/r#0\n---\nbody" });
  assert.equal(retrievedSources(many).length, 8);
  const r = withVerifiedLinks("[R0](https://x.org/r#0)", many);
  assert.equal(r.listed, 5);
});

test("renders verified Markdown sources as safe clickable links", () => {
  const rendered = renderAnswerMarkdown(`See [Rule G202](${manual}) and <script>alert(1)</script>.\n\n**Sources**`);
  assert.match(rendered, /<a href="https:\/\/ftc-resources\.firstinspires\.org/);
  assert.match(rendered, /target="_blank"/);
  assert.match(rendered, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(rendered, /<strong>Sources<\/strong>/);
});

test("places questions in stable archive categories", () => {
  assert.equal(categorizeQuestion("How many points is a flower worth?"), "Game rules & scoring");
  assert.equal(categorizeQuestion("Can I modify this servo?"), "Robot build & inspection");
  assert.equal(categorizeQuestion("How do I program a mecanum drive?"), "Programming & software");
});

test("normalizes repeat questions to one public archive key", () => {
  assert.equal(questionKey("  How many points is a flower worth?  "), questionKey("how many points is a flower worth?"));
  assert.notEqual(questionKey("How many points is a flower worth?"), questionKey("How many points is a hive worth?"));
});

test("publishes machine-readable answers with canonical URLs and cited sources", () => {
  const answer = `Follow [Rule G202](${manual}).\n\n**Sources**\n- [Rule G202](${manual})`;
  const sources = answerSources(answer);
  assert.deepEqual(sources, [{ label: "Rule G202", url: manual }]);
  const structured = structuredQuestion(new URL("https://ftc.uprobotics.tech/questions.json"), { id: 42, question: "What does G202 require?", occurred_at: "2026-09-21T12:00:00.000Z" }, answer, "2026-09-21T12:01:00.000Z", "Game rules & scoring");
  assert.equal(structured["@type"], "QAPage");
  assert.equal(structured.url, "https://ftc.uprobotics.tech/questions/42");
  assert.equal(structured.mainEntity.acceptedAnswer.isBasedOn[0].url, manual);
});
