# git-objects

Pure Python zero-dependency reader and writer for Git object stores, packed refs, and packfiles v2.

## Problem

Tools analyzing or manipulating Git repositories without spawning shell processes often depend on native C extensions or full binary installations of Git that complicate deployment. Spawning external Git processes creates substantial subprocess overhead, platform-specific path issues, and security attack surfaces from unvalidated ref arguments. A native pure Python implementation provides deterministic, cross-platform access to loose objects, packfiles, and index structures using only standard library modules.

## Installation

```sh
pip install trysac-git-objects
```

Optional extras:
- `trysac-git-objects[similarity]`: near-duplicate detection for tree diff similarity.
- `trysac-git-objects[lint]`: commit message linting based on Conventional Commits.

## Usage

```python
from git_objects import ObjectStore

store = ObjectStore(".")
head_oid = store.rev_parse("HEAD")
for commit in store.walk_commits(head_oid):
    print(commit.oid, commit.committer, commit.message.strip())
    break
```

## Public API

### `ObjectStore`

- `ObjectStore(repo_path, max_object_bytes=67108864, max_delta_depth=50, on_event=None)`: Core manager for Git loose objects, packed objects, refs, and packs.
- `read_object(oid_hex: str) -> GitObject`: Reads object by 40-char hex SHA-1. Raises `ObjectNotFoundError` if missing, `CorruptObjectError` if corrupted, `ObjectSizeLimitError` if exceeding limits.
- `hash_object(data: bytes, obj_type: str = 'blob', *, write: bool = False) -> str`: Calculates SHA-1 Git object id.
- `rev_parse(rev: str) -> str`: Resolves symbolic references like `HEAD`, `main`, `HEAD~1`, and hash prefixes >= 4 chars. Raises `InvalidRefError` for bad ref formats.
- `walk_commits(start_oid: str, *, order: str = 'topo') -> Iterator[GitCommit]`: Traverses commit graph.
- `write_pack(oids: Sequence[str], out_pack_path, out_idx_path) -> tuple[str, int]`: Generates packfile v2 and index v2.
- `commit_tree(...) -> str`, `write_tree(directory) -> str`, `update_ref(...) -> None`, `diff_trees(old, new) -> list[TreeChange]`.

## Why not existing alternatives?

Dulwich contains large C-extension hooks and broad scope including network protocols and porcelain. git-objects focuses strictly on standard library zero-dependency object store and pack reading/writing with small memory footprint and auditability.

## Security

See [docs/SECURITY.md](docs/SECURITY.md).