# Architecture

`trysac-git-objects` provides standard-library Git object and packfile parsing.

## Components

1. **`git_objects.store`**: `ObjectStore` orchestrates loose files, packed refs (`.git/packed-refs`), and indexed packfiles (`.git/objects/pack/*.pack`).
2. **`git_objects.pack`**: Direct binary parsing and writing of Git Packfile v2 and Index v2 formats, resolving `OFS_DELTA` and `REF_DELTA` chains with an LRU cache.
3. **`git_objects.delta`**: Low-level variable-length integer and byte copy/insert instruction processor for pack delta decompression and generation.
4. **`git_objects.parsers`**: Serializers and deserializers for Git trees, commits, and annotated tags.
5. **`git_objects.adapters`**: Optional integration adapters with `trysac-near-dup-detector` (for fuzzy rename similarity diffing) and `trysac-commit-msg-lint` (for commit convention validation).
