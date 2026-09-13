---
status: accepted
date: 2026-09-13
---

# Answer through our own API Worker, not AI Search's chat endpoint

The approved v1 spec said "no custom backend": the page would call AI Search's public `/chat/completions`
directly. In production, AI Search's hybrid search gives its vector leg a short time budget and silently
returns keyword-only results whenever the Workers AI query embedding is slow, which on 2026-09-13 was 7–8
searches in 10. Vector-only mode always returned vectors but failed every rule-number question (G202,
R503, E117). We decided to put a small Worker at `ftc.uprobotics.tech/api` that runs the keyword and
vector searches as two separate calls, waits for both, fuses them with Reciprocal Rank Fusion, and
generates the answer with the same Workers AI model and prompt. The page widgets call the Worker; the
public endpoint stays on only for `/mcp`.

## Considered options

- Vector-only retrieval: reliable vectors, but rule numbers are lexical and were lost.
- Keyword-only retrieval: correct on rule numbers, weak on paraphrased and dimension questions.
- Wait for Cloudflare to fix the hybrid time budget: no date, and students would see the degradation meanwhile.

## Consequences

- The page now depends on a Worker we maintain, with its own rate limit (60/min/IP) and prompt copy.
- The prompt exists in two places: the AI Search instance (for `/mcp` and search) and the Worker.
- Answer quality and link behaviour can be enforced in code, which the direct endpoint could not do.
