# Pure standard library packfile v2 and loose object implementation

Status: accepted (generated, pending human review)

## Context

External Git tools either shell out to the git CLI or require native C-extensions like libgit2/dulwich. We need zero runtime dependencies in pure Python with robust resource bounding.

## Decision

Implement the Git object model, packfile v2 parsing, .idx v2 generation, delta resolution with an LRU cache, and ref locking strictly using Python's standard library (struct, zlib, hashlib, pathlib, os). Isolate optional integrations into adapters.

## Consequences

Allows fully portable and auditable repository inspection without binary builds or external tool dependencies, while adhering strictly to Git's canonical on-disk formats.
