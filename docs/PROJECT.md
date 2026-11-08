# Project Record

## Objective

Implement a pure Python Git object store and packfile mechanics library with zero runtime dependencies, suitable for analysis and manipulation without spawning Git processes.

## Scope

- Loose objects (blob, tree, commit, tag) with SHA-1 and zlib compression.
- Packfile v2 reading (.idx v2, OFS_DELTA, REF_DELTA resolution).
- Symbolic references (`HEAD`) and packed-refs (`.git/packed-refs`).
- Rev-parse resolution (`HEAD~N`, `^N`, hash prefixes).
- Commit DAG traversal (date and topological order).
- Exact-rename tree diffing.
- Writing objects, commits, and packfiles (with index generation).
- Optional adapters for similarity rename detection (`trysac-near-dup-detector`) and commit linting (`trysac-commit-msg-lint`).

## Out-of-Scope

- Git network protocols (transfer-pack, etc.).
- Repository merging logic.
- Automated working tree checkout/indexing.
- Git submodules.

## Factory run u410 (measured from API usage)

| Phase | Model | Batch | Input | Output | Cache write | Cache read | Cost (USD) |
|---|---|---|---|---|---|---|---|
| analyst | gemini-3.8-flash | no | 3552 | 1458 | 0 | 0 | 0.0081 |
| generation | gemini-3.8-flash | yes | 8337 | 29447 | 0 | 0 | 0.0583 |
| fix-1 | gemini-3.8-flash | no | 179470 | 4070 | 0 | 3139578 | 0.3853 |
| fix-2 | gemini-3.8-flash | no | 40936 | 690 | 0 | 121136 | 0.0424 |
| fix-3 | gemini-3.8-flash | no | 118672 | 710 | 0 | 556603 | 0.1334 |
| fix-4 | gemini-3.8-flash | no | 102399 | 8221 | 0 | 975376 | 0.1808 |
| fix-5 | gemini-3.8-flash | no | 159420 | 16378 | 0 | 2529685 | 0.3707 |
| fix-6 | gemini-3.8-flash | no | 40543 | 103 | 0 | 36327 | 0.0335 |
| fix-1 | gemini-3.8-flash | no | 49631 | 301 | 0 | 183749 | 0.0521 |
| fix-2 | gemini-3.8-flash | no | 168372 | 6616 | 0 | 3242392 | 0.3943 |
| fix-3 | gemini-3.8-flash | no | 45106 | 287 | 0 | 77369 | 0.0407 |
| audit | gemini-3.8-flash | no | 145211 | 1499 | 0 | 1869584 | 0.2547 |
| docs | gemini-3.1-flash-lite | no | 147391 | 1340 | 0 | 0 | 0.0389 |

Total: $1.9933. Prices: Anthropic list prices.
