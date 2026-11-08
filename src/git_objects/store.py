"""ObjectStore: managing loose objects, packfiles, refs and commit graphs."""

from __future__ import annotations

import heapq
import os
import re
import zlib
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

from git_objects.errors import (
    CorruptObjectError,
    InvalidRefError,
    ObjectNotFoundError,
    ObjectSizeLimitError,
    PackParseError,
    RefUpdateError,
)
from git_objects.models import GitCommit, GitObject, GitTreeEntry, TreeChange
from git_objects.pack import LRUCache, PackIndex, PackReader, write_packfile
from git_objects.parsers import (
    parse_commit,
    parse_tree,
    serialize_commit,
    serialize_tree,
)


class ObjectStore:
    """Core manager for Git loose objects, packed objects, refs, and packs."""

    def __init__(
        self,
        repo_path: str | Path,
        *,
        max_object_bytes: int = 67108864,
        max_delta_depth: int = 50,
        max_ref_file_bytes: int = 4096,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        path = Path(repo_path)
        if (path / ".git").is_dir():
            self.git_dir = path / ".git"
            self.work_tree: Path | None = path
        elif (path / "objects").is_dir() or (path / "HEAD").is_file():
            self.git_dir = path
            self.work_tree = None
        else:
            self.git_dir = path / ".git"
            self.work_tree = path

        self.objects_dir = self.git_dir / "objects"
        self.pack_dir = self.objects_dir / "pack"
        self.max_object_bytes = max_object_bytes
        self.max_delta_depth = max_delta_depth
        self.max_ref_file_bytes = max_ref_file_bytes
        self.on_event = on_event

        self._delta_cache = LRUCache(max_entries=1000)
        self._pack_readers: list[PackReader] | None = None

    def init_repo(self, bare: bool = False) -> None:
        """Initialize minimal directory structure for git store."""
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.pack_dir.mkdir(parents=True, exist_ok=True)
        (self.git_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
        (self.git_dir / "refs" / "tags").mkdir(parents=True, exist_ok=True)
        head_file = self.git_dir / "HEAD"
        if not head_file.exists():
            head_file.write_text("ref: refs/heads/main\n", encoding="utf-8")

    def _get_pack_readers(self) -> list[PackReader]:
        if self._pack_readers is not None:
            return self._pack_readers

        readers: list[PackReader] = []
        if self.pack_dir.is_dir():
            for idx_file in self.pack_dir.glob("*.idx"):
                pack_file = idx_file.with_suffix(".pack")
                if pack_file.is_file():
                    try:
                        idx = PackIndex(idx_file)
                        reader = PackReader(
                            pack_file,
                            idx,
                            max_object_bytes=self.max_object_bytes,
                            max_delta_depth=self.max_delta_depth,
                            lru_cache=self._delta_cache,
                            on_event=self.on_event,
                        )
                        readers.append(reader)
                    except (PackParseError, OSError):
                        continue
        self._pack_readers = readers
        return readers

    def hash_object(self, data: bytes, obj_type: str = "blob", *, write: bool = False) -> str:
        """Calculate SHA-1 for a git object, optionally writing it as loose."""
        if len(data) > self.max_object_bytes:
            raise ObjectSizeLimitError(
                f"Object size {len(data)} exceeds limit {self.max_object_bytes}"
            )

        import hashlib

        header = f"{obj_type} {len(data)}\x00".encode("ascii")
        full = header + data
        oid = hashlib.sha1(full, usedforsecurity=False).hexdigest()

        if write:
            obj_dir = self.objects_dir / oid[:2]
            obj_file = obj_dir / oid[2:]
            if not obj_file.exists():
                obj_dir.mkdir(parents=True, exist_ok=True)
                compressed = zlib.compress(full)
                temp_file = obj_dir / f"tmp_{oid[2:]}_{os.getpid()}"
                temp_file.write_bytes(compressed)
                temp_file.replace(obj_file)

            if self.on_event is not None:
                self.on_event(
                    {
                        "event": "write_loose_object",
                        "oid": oid,
                        "obj_type": obj_type,
                        "size": len(data),
                    }
                )

        return oid

    def read_object(self, oid_hex: str) -> GitObject:
        """Read a loose or packed object by 40-character hex SHA-1."""
        oid = oid_hex.lower()
        if len(oid) != 40:
            raise ObjectNotFoundError(f"Invalid object ID length: {oid_hex}")

        # 1. Try loose object
        loose_path = self.objects_dir / oid[:2] / oid[2:]
        if loose_path.is_file():
            return self._read_loose_object(loose_path, oid)

        # 2. Try packed objects
        for reader in self._get_pack_readers():
            if reader.contains(oid):
                return reader.read_object(oid, store_lookup=self.read_object)

        raise ObjectNotFoundError(f"Object {oid} not found")

    def _read_loose_object(self, path: Path, expected_oid: str) -> GitObject:
        import hashlib

        try:
            raw = path.read_bytes()
        except OSError as err:
            raise ObjectNotFoundError(f"Cannot read object {expected_oid}: {err}") from err

        try:
            decompressed = zlib.decompress(raw, bufsize=self.max_object_bytes)
        except zlib.error as err:
            raise CorruptObjectError(
                f"Corrupt loose object compression {expected_oid}: {err}"
            ) from err

        if len(decompressed) > self.max_object_bytes:
            raise ObjectSizeLimitError(
                f"Loose object {expected_oid} exceeds max limit {self.max_object_bytes}"
            )

        null_idx = decompressed.find(b"\x00")
        if null_idx == -1:
            raise CorruptObjectError(f"Malformed loose object header for {expected_oid}")

        header = decompressed[:null_idx].decode("ascii", errors="replace")
        parts = header.split(" ")
        if len(parts) != 2:
            raise CorruptObjectError(f"Malformed header format: {header}")
        obj_type, size_str = parts
        try:
            declared_size = int(size_str)
        except ValueError as err:
            raise CorruptObjectError(f"Invalid size in object header: {size_str}") from err

        data = decompressed[null_idx + 1 :]
        if len(data) != declared_size:
            raise CorruptObjectError(f"Size mismatch in loose object {expected_oid}")

        computed_sha = hashlib.sha1(decompressed, usedforsecurity=False).hexdigest()
        if computed_sha != expected_oid:
            raise CorruptObjectError(f"Hash mismatch for {expected_oid}: computed {computed_sha}")

        if self.on_event is not None:
            self.on_event(
                {
                    "event": "read_loose_object",
                    "oid": expected_oid,
                    "obj_type": obj_type,
                    "size": len(data),
                }
            )

        return GitObject(oid=expected_oid, type=obj_type, data=data)

    def validate_ref_name(self, ref_name: str) -> None:
        """Check ref name against Git naming rules (no '..', no trailing '/', etc)."""
        if not ref_name or ref_name == "@":
            raise InvalidRefError(f"Invalid ref name: {ref_name}")
        if ref_name.startswith("/") or ref_name.endswith("/"):
            raise InvalidRefError(f"Ref name cannot start or end with slash: {ref_name}")
        if ".." in ref_name:
            raise InvalidRefError(f"Ref name cannot contain '..': {ref_name}")
        if "@{ " in ref_name or ref_name.endswith(".lock") or ref_name.endswith("."):
            raise InvalidRefError(f"Ref name illegal format: {ref_name}")

        parts = ref_name.split("/")
        for part in parts:
            if not part or part.startswith("."):
                raise InvalidRefError(
                    f"Ref component cannot be empty or start with dot: {ref_name}"
                )
            # Illegal chars: space, ~, ^, :, ?, *, [, \, control chars
            if any(ord(c) < 32 or ord(c) == 127 or c in " ~^:?*\\@[" for c in part):
                raise InvalidRefError(f"Ref name contains illegal character: {ref_name}")

    def resolve_ref(self, ref_name: str) -> str:
        """Resolve a ref name to a 40-char SHA-1, reading loose refs and packed-refs."""
        if ref_name != "HEAD":
            self.validate_ref_name(ref_name)

        # 1. Check HEAD
        if ref_name == "HEAD":
            head_file = self.git_dir / "HEAD"
            if not head_file.exists():
                raise InvalidRefError("HEAD file not found")
            content = head_file.read_text(encoding="utf-8", errors="replace").strip()
            if content.startswith("ref:"):
                target = content[4:].strip()
                return self.resolve_ref(target)
            if len(content) == 40:
                return content.lower()
            raise InvalidRefError(f"Corrupt HEAD file: {content}")

        # Candidate paths
        candidates = [ref_name]
        if not ref_name.startswith("refs/"):
            candidates = [
                ref_name,
                f"refs/{ref_name}",
                f"refs/tags/{ref_name}",
                f"refs/heads/{ref_name}",
                f"refs/remotes/{ref_name}",
            ]

        # Check loose ref files
        for cand in candidates:
            loose_path = self.git_dir / cand
            if loose_path.is_file():
                if loose_path.stat().st_size > self.max_ref_file_bytes:
                    raise ObjectSizeLimitError("Ref file exceeds max size limit")
                text = loose_path.read_text(encoding="utf-8", errors="replace").strip()
                if text.startswith("ref:"):
                    return self.resolve_ref(text[4:].strip())
                if len(text) == 40:
                    return text.lower()

        # Check packed-refs
        packed_refs_file = self.git_dir / "packed-refs"
        if packed_refs_file.is_file():
            packed = self._parse_packed_refs()
            for cand in candidates:
                if cand in packed:
                    return packed[cand]

        raise InvalidRefError(f"Reference {ref_name} not found")

    def _parse_packed_refs(self) -> dict[str, str]:
        packed_refs_file = self.git_dir / "packed-refs"
        if not packed_refs_file.is_file():
            return {}

        result: dict[str, str] = {}
        lines = packed_refs_file.read_text(encoding="utf-8", errors="replace").splitlines()
        last_ref = None
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("^"):
                # Peeled tag hash for last_ref
                if last_ref:
                    peeled_oid = line[1:].strip().lower()
                    result[f"{last_ref}^{{}}"] = peeled_oid
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2:
                oid, name = parts[0].lower(), parts[1]
                result[name] = oid
                last_ref = name
        return result

    def rev_parse(self, rev: str) -> str:
        """Resolve revision string (HEAD, branch, tag, ~N, ^N, hex prefix >= 4)."""
        rev = rev.strip()
        if not rev:
            raise InvalidRefError("Empty revision expression")

        # Parse peel suffixes ~N and ^N
        match = re.search(r"^(.+?)([~^].*)$", rev)
        if match:
            base_rev = match.group(1)
            ops = match.group(2)
            base_oid = self.rev_parse(base_rev)
            curr_oid = base_oid

            pos = 0
            while pos < len(ops):
                op = ops[pos]
                pos += 1
                num_str = ""
                while pos < len(ops) and ops[pos].isdigit():
                    num_str += ops[pos]
                    pos += 1
                count = int(num_str) if num_str else 1

                if op == "~":
                    # N-th ancestor (first parent count times)
                    for _ in range(count):
                        obj = self.read_object(curr_oid)
                        while obj.type == "tag":
                            from git_objects.parsers import parse_tag

                            tag = parse_tag(obj.oid, obj.data)
                            obj = self.read_object(tag.object_oid)
                        if obj.type != "commit":
                            raise InvalidRefError(f"Cannot peel non-commit object {curr_oid}")
                        commit = parse_commit(obj.oid, obj.data)
                        if not commit.parents:
                            raise InvalidRefError(f"Commit {obj.oid} has no parent")
                        curr_oid = commit.parents[0]
                elif op == "^":
                    # N-th parent
                    obj = self.read_object(curr_oid)
                    while obj.type == "tag":
                        from git_objects.parsers import parse_tag

                        tag = parse_tag(obj.oid, obj.data)
                        obj = self.read_object(tag.object_oid)
                    if obj.type != "commit":
                        raise InvalidRefError(f"Cannot peel non-commit object {curr_oid}")
                    commit = parse_commit(obj.oid, obj.data)
                    if count == 0:
                        # ^0 means peeling to commit itself
                        curr_oid = commit.oid
                    else:
                        if count > len(commit.parents):
                            raise InvalidRefError(f"Commit {obj.oid} does not have parent #{count}")
                        curr_oid = commit.parents[count - 1]
            return curr_oid

        # Check if 40-char full hex oid
        if len(rev) == 40 and all(c in "0123456789abcdefABCDEF" for c in rev):
            try:
                self.read_object(rev.lower())
                return rev.lower()
            except ObjectNotFoundError:
                pass

        # Try resolving as ref name
        try:
            return self.resolve_ref(rev)
        except InvalidRefError:
            pass

        # Try prefix match if hex length >= 4
        if len(rev) >= 4 and all(c in "0123456789abcdefABCDEF" for c in rev):
            prefix = rev.lower()
            matches: list[str] = []
            # Check loose
            loose_dir = self.objects_dir / prefix[:2]
            if loose_dir.is_dir():
                for p in loose_dir.glob(f"{prefix[2:]}*"):
                    matches.append(prefix[:2] + p.name)
            # Check packs
            for reader in self._get_pack_readers():
                for o in reader.index.offsets:
                    if o.startswith(prefix) and o not in matches:
                        matches.append(o)
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise InvalidRefError(f"Ambiguous hex prefix: {rev}")

        raise InvalidRefError(f"Revision '{rev}' could not be resolved")

    def walk_commits(self, start_oid: str, *, order: str = "topo") -> Iterator[GitCommit]:
        """Traverse commit graph starting from start_oid in 'topo' or 'date' order."""
        start_oid = self.rev_parse(start_oid)
        visited: set[str] = set()

        if order == "date":
            # Max-heap ordered by commit timestamp
            # heapq is min-heap, so store -time
            heap: list[tuple[int, int, str]] = []
            counter = 0

            root_obj = self.read_object(start_oid)
            if root_obj.type != "commit":
                raise InvalidRefError(f"Object {start_oid} is not a commit")
            root_commit = parse_commit(start_oid, root_obj.data)
            heapq.heappush(heap, (-root_commit.committer_time, counter, start_oid))
            visited.add(start_oid)

            while heap:
                _, _, curr_oid = heapq.heappop(heap)
                obj = self.read_object(curr_oid)
                commit = parse_commit(curr_oid, obj.data)
                yield commit

                for parent_oid in commit.parents:
                    if parent_oid not in visited:
                        visited.add(parent_oid)
                        p_obj = self.read_object(parent_oid)
                        p_commit = parse_commit(parent_oid, p_obj.data)
                        counter += 1
                        heapq.heappush(heap, (-p_commit.committer_time, counter, parent_oid))

        elif order == "topo":
            # Topological sort ensuring child is always yielded before its parents
            commits: dict[str, GitCommit] = {}
            in_degree: dict[str, int] = {}
            children_map: dict[str, list[str]] = {}

            q = deque([start_oid])
            visited_setup: set[str] = {start_oid}

            while q:
                curr_oid = q.popleft()
                obj = self.read_object(curr_oid)
                if obj.type != "commit":
                    raise InvalidRefError(f"Object {curr_oid} is not a commit")
                c = parse_commit(curr_oid, obj.data)
                commits[curr_oid] = c
                if curr_oid not in in_degree:
                    in_degree[curr_oid] = 0

                for p in c.parents:
                    children_map.setdefault(p, []).append(curr_oid)
                    in_degree[p] = in_degree.get(p, 0) + 1
                    if p not in visited_setup:
                        visited_setup.add(p)
                        q.append(p)

            # Topological sort using Kahn's algorithm, priority given to commits reachable first
            zero_in: list[str] = [oid for oid, deg in in_degree.items() if deg == 0]
            while zero_in:
                curr = zero_in.pop(0)
                yield commits[curr]
                for p in commits[curr].parents:
                    in_degree[p] -= 1
                    if in_degree[p] == 0:
                        zero_in.append(p)
        else:
            raise InvalidRefError(f"Unsupported walk_commits order: {order}")

    def update_ref(self, name: str, new: str, old: str | None = None) -> None:
        """Atomically update a reference using a <ref>.lock file."""
        self.validate_ref_name(name)
        ref_path = self.git_dir / name
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.git_dir / f"{name}.lock"

        # Check current value
        current_val: str | None = None
        try:
            current_val = self.resolve_ref(name)
        except InvalidRefError:
            current_val = None

        if old is not None and current_val != old.lower():
            raise RefUpdateError(
                f"Ref mismatch: expected {old}, currently {current_val}",
                code="ref_mismatch",
                context={"name": name, "expected": old, "actual": current_val},
            )

        try:
            # Exclusive lock creation
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as err:
            raise RefUpdateError(f"Ref lock already exists for {name}", code="lock_failed") from err

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f"{new}\n")
            lock_path.replace(ref_path)
        except Exception:
            if lock_path.exists():
                lock_path.unlink()
            raise

    def commit_tree(
        self,
        tree: str,
        parents: Sequence[str],
        message: str,
        author: str,
        committer: str,
        author_time: int = 0,
        author_tz: str = "+0000",
        committer_time: int = 0,
        committer_tz: str = "+0000",
    ) -> str:
        """Create and write a commit object."""
        data = serialize_commit(
            tree=tree,
            parents=list(parents),
            message=message,
            author=author,
            committer=committer,
            author_time=author_time,
            author_tz=author_tz,
            committer_time=committer_time,
            committer_tz=committer_tz,
        )
        return self.hash_object(data, "commit", write=True)

    def write_tree(self, directory: str | Path | None = None) -> str:
        """Write a directory tree to the object store recursively."""
        dir_path = Path(directory) if directory is not None else self.work_tree
        if dir_path is None or not dir_path.is_dir():
            raise InvalidRefError("Cannot write tree: invalid directory")

        entries: list[GitTreeEntry] = []
        for item in sorted(dir_path.iterdir(), key=lambda p: p.name):
            if item.name == ".git":
                continue
            if item.is_symlink():
                # Git symlink mode 120000
                target = os.readlink(item)
                oid = self.hash_object(target.encode("utf-8"), "blob", write=True)
                entries.append(GitTreeEntry(mode="120000", name=item.name, oid=oid))
            elif item.is_dir():
                sub_oid = self.write_tree(item)
                entries.append(GitTreeEntry(mode="040000", name=item.name, oid=sub_oid))
            elif item.is_file():
                content = item.read_bytes()
                oid = self.hash_object(content, "blob", write=True)
                mode = "100755" if os.access(item, os.X_OK) else "100644"
                entries.append(GitTreeEntry(mode=mode, name=item.name, oid=oid))

        tree_data = serialize_tree(entries)
        return self.hash_object(tree_data, "tree", write=True)

    def get_tree_files(self, tree_oid: str, prefix: str = "") -> dict[str, tuple[str, str]]:
        """Recursively flatten a tree object into a dict mapping path -> (oid, mode)."""
        obj = self.read_object(tree_oid)
        if obj.type != "tree":
            raise InvalidRefError(f"Object {tree_oid} is not a tree")

        entries = parse_tree(obj.data)
        result: dict[str, tuple[str, str]] = {}
        for e in entries:
            rel_path = f"{prefix}{e.name}" if not prefix else f"{prefix}/{e.name}"
            if e.mode in ("40000", "040000"):  # Subtree
                sub = self.get_tree_files(e.oid, prefix=rel_path)
                result.update(sub)
            else:
                result[rel_path] = (e.oid, e.mode)
        return result

    def diff_trees(self, old_tree_oid: str | None, new_tree_oid: str | None) -> list[TreeChange]:
        """Compare two tree objects and return a list of TreeChanges with exact renames."""
        old_files = self.get_tree_files(old_tree_oid) if old_tree_oid else {}
        new_files = self.get_tree_files(new_tree_oid) if new_tree_oid else {}

        changes: list[TreeChange] = []
        deleted_files: dict[str, tuple[str, str]] = {}
        added_files: dict[str, tuple[str, str]] = {}

        all_paths = set(old_files.keys()) | set(new_files.keys())
        for path in sorted(all_paths):
            if path in old_files and path in new_files:
                old_oid, old_mode = old_files[path]
                new_oid, new_mode = new_files[path]
                if old_oid != new_oid or old_mode != new_mode:
                    changes.append(
                        TreeChange(
                            change_type="modified",
                            path=path,
                            old_path=path,
                            old_oid=old_oid,
                            new_oid=new_oid,
                            old_mode=old_mode,
                            new_mode=new_mode,
                        )
                    )
            elif path in old_files:
                deleted_files[path] = old_files[path]
            else:
                added_files[path] = new_files[path]

        # Exact-content rename detection
        matched_deleted: set[str] = set()
        matched_added: set[str] = set()

        # Map old oid to deleted paths
        old_oid_map: dict[str, list[str]] = {}
        for d_path, (d_oid, _) in deleted_files.items():
            old_oid_map.setdefault(d_oid, []).append(d_path)

        for a_path, (a_oid, a_mode) in added_files.items():
            if old_oid_map.get(a_oid):
                d_path = old_oid_map[a_oid].pop(0)
                d_mode = deleted_files[d_path][1]
                matched_deleted.add(d_path)
                matched_added.add(a_path)
                changes.append(
                    TreeChange(
                        change_type="renamed",
                        path=a_path,
                        old_path=d_path,
                        old_oid=a_oid,
                        new_oid=a_oid,
                        old_mode=d_mode,
                        new_mode=a_mode,
                        similarity=1.0,
                    )
                )

        for d_path, (d_oid, d_mode) in deleted_files.items():
            if d_path not in matched_deleted:
                changes.append(
                    TreeChange(
                        change_type="deleted",
                        path=d_path,
                        old_path=d_path,
                        old_oid=d_oid,
                        new_oid=None,
                        old_mode=d_mode,
                        new_mode=None,
                    )
                )

        for a_path, (a_oid, a_mode) in added_files.items():
            if a_path not in matched_added:
                changes.append(
                    TreeChange(
                        change_type="added",
                        path=a_path,
                        old_path=None,
                        old_oid=None,
                        new_oid=a_oid,
                        old_mode=None,
                        new_mode=a_mode,
                    )
                )

        return sorted(changes, key=lambda c: c.path)

    def write_pack(
        self,
        oids: Sequence[str],
        out_pack_path: str | Path,
        out_idx_path: str | Path,
    ) -> tuple[str, int]:
        """Write a packfile and index containing the specified objects."""
        objects: list[GitObject] = []
        for oid in oids:
            objects.append(self.read_object(oid))
        pack_path = Path(out_pack_path)
        idx_path = Path(out_idx_path)
        pack_path.parent.mkdir(parents=True, exist_ok=True)
        idx_path.parent.mkdir(parents=True, exist_ok=True)
        return write_packfile(objects, pack_path, idx_path)
