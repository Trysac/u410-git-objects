# Security

## Threat Model

- **Defended**:
  - **Ref name injection**: Strictly validates ref names to prevent directory traversal (`..`), trailing slashes, and control characters.
  - **Zip/Zlib Bombs and Object Size Limits**: Enforces `max_object_bytes` (default 64 MiB) prior to and during decompression.
  - **Cyclic or Deep Deltas**: Pack delta chains are bounded by `max_delta_depth` (default 50) to prevent CPU starvation.
  - **Information Leakage**: Base exceptions truncate untrusted input to <= 200 characters; `on_event` metadata handlers omit object payloads.

- **Not Defended**:
  - Maliciously constructed packfiles that exceed memory limits *before* full parsing (resource exhaustion).
  - Parallel ref-update race conditions beyond file-based locks.