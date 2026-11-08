"""Unit and conformance tests for git_objects core store functionality."""

from __future__ import annotations

import os
import shutil
import struct
import tempfile
import zlib
from pathlib import Path

import pytest

from git_objects.delta import apply_delta, create_delta
from git_objects.errors import (
    DeltaDepthExceededError,
    InvalidRefError,
    ObjectSizeLimitError,
    RefUpdateError,
)
from git_objects.models import GitTreeEntry
from git_objects.pack import PackIndex, PackReader
from git_objects.parsers import serialize_tree
from git_objects.store import ObjectStore


@pytest.fixture
def temp_store() -> tuple[ObjectStore, Path]:
    temp_dir = Path(tempfile.mkdtemp())
    store = ObjectStore(temp_dir)
    store.init_repo()
    yield store, temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_acceptance_hash_object_and_empty_tree(temp_store: tuple[ObjectStore, Path]) -> None:
    store, _ = temp_store
    # WHEN store.hash_object(b'hello\n', 'blob') runs THEN it returns 'ce013625030ba8dba906f756967f9e9ca394464a'
    oid = store.hash_object(b"hello\n", "blob")
    assert oid == "ce013625030ba8dba906f756967f9e9ca394464a"

    # and an empty tree hashes to '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
    empty_tree_oid = store.hash_object(b"", "tree")
    assert empty_tree_oid == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def test_acceptance_ofs_delta_in_pack(temp_store: tuple[ObjectStore, Path]) -> None:
    store, _repo_path = temp_store
    # WHEN a pack built in the test holds the base blob b'hello world\n' and an OFS_DELTA that copies 6 bytes from offset 0 and inserts b'git\n'
    # THEN read_object of the delta returns data b'hello git\n'
    base_data = b"hello world\n"
    target_data = b"hello git\n"

    # Build OFS delta payload: base_size 12, target_size 10
    # Delta header: base_size=12 (0x0c), target_size=10 (0x0a)
    # Copy 6 bytes from offset 0: cmd = 0x80 | 0x10 (size in byte 1), cp_size = 6
    # Insert 4 bytes literal: 0x04 b'git\n'
    delta_payload = bytes(
        [
            0x0C,  # base size 12
            0x0A,  # target size 10
            0x90,
            0x06,  # copy 6 bytes from offset 0
            0x04,
            ord("g"),
            ord("i"),
            ord("t"),
            ord("\n"),  # insert literal b'git\n'
        ]
    )
    assert apply_delta(base_data, delta_payload) == target_data

    # Construct packfile with base blob at offset 12 and OFS_DELTA right after
    pack_data = bytearray()
    pack_data.extend(struct.pack(">4sII", b"PACK", 2, 2))

    # Object 1: base blob (type 3)
    base_comp = zlib.compress(base_data)
    pack_data.append((3 << 4) | (len(base_data) & 0x0F))
    pack_data.extend(base_comp)

    delta_offset = len(pack_data)
    neg_offset = delta_offset - 12

    # Object 2: OFS_DELTA (type 6)
    # Header for delta object: type 6, size len(delta_payload)
    pack_data.append((6 << 4) | (len(delta_payload) & 0x0F))
    # OFS negative offset varint
    pack_data.append(neg_offset & 0x7F)
    delta_comp = zlib.compress(delta_payload)
    pack_data.extend(delta_comp)

    # Checksum
    import hashlib

    pack_sha = hashlib.sha1(pack_data, usedforsecurity=False).digest()
    pack_data.extend(pack_sha)

    pack_path = store.pack_dir / "pack-test.pack"
    idx_path = store.pack_dir / "pack-test.idx"
    pack_path.write_bytes(pack_data)

    # Object SHA1s
    base_sha = hashlib.sha1(b"blob 12\x00" + base_data, usedforsecurity=False).digest()
    delta_sha = hashlib.sha1(b"blob 10\x00" + target_data, usedforsecurity=False).digest()

    # Build minimal idx v2
    idx_data = bytearray(b"\xfftOc\x00\x00\x00\x02")
    sorted_shas = sorted([(base_sha, 12), (delta_sha, delta_offset)], key=lambda x: x[0])
    fanout = [0] * 256
    for s, _ in sorted_shas:
        fanout[s[0]] += 1
    for i in range(1, 256):
        fanout[i] += fanout[i - 1]
    for count in fanout:
        idx_data.extend(struct.pack(">I", count))
    for s, _ in sorted_shas:
        idx_data.extend(s)
    for _ in sorted_shas:
        idx_data.extend(struct.pack(">I", 0))
    for _, off in sorted_shas:
        idx_data.extend(struct.pack(">I", off))
    idx_data.extend(pack_sha)
    idx_sha = hashlib.sha1(idx_data, usedforsecurity=False).digest()
    idx_data.extend(idx_sha)
    idx_path.write_bytes(idx_data)

    # Now read the delta object
    target_hex = delta_sha.hex()
    obj = store.read_object(target_hex)
    assert obj.data == b"hello git\n"
    assert obj.type == "blob"


def test_acceptance_rev_parse_head_tilde_one(temp_store: tuple[ObjectStore, Path]) -> None:
    store, _ = temp_store
    # WHEN resolving the revision string 'HEAD~1' on a repository where HEAD points to 'refs/heads/main'
    # THEN rev_parse('HEAD~1') returns the 40-character commit hex hash of the first parent
    c1 = store.commit_tree(
        tree="4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        parents=[],
        message="initial commit",
        author="Tester <t@example.com>",
        committer="Tester <t@example.com>",
    )
    c2 = store.commit_tree(
        tree="4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        parents=[c1],
        message="second commit",
        author="Tester <t@example.com>",
        committer="Tester <t@example.com>",
    )
    store.update_ref("refs/heads/main", c2)

    resolved = store.rev_parse("HEAD~1")
    assert resolved == c1
    assert len(resolved) == 40


def test_acceptance_object_size_limit(temp_store: tuple[ObjectStore, Path]) -> None:
    store, _ = temp_store
    # IF uncompressed object payload size exceeds max_object_bytes defaulting to 67108864 THEN read_object raises ObjectSizeLimitError with code 'object_too_large'
    small_store = ObjectStore(store.git_dir.parent, max_object_bytes=10)
    with pytest.raises(ObjectSizeLimitError) as exc_info:
        small_store.hash_object(b"123456789012345", "blob", write=True)
    assert exc_info.value.code == "object_too_large"


def test_acceptance_delta_depth_exceeded() -> None:
    # IF a packfile delta chain traversal depth exceeds max_delta_depth defaulting to 50 THEN decode_pack_object raises DeltaDepthExceededError with code 'delta_depth_exceeded'
    reader = PackReader(Path("/fake/pack.pack"), None, max_delta_depth=2)  # type: ignore
    with pytest.raises(DeltaDepthExceededError) as exc_info:
        reader.read_object_at_offset(100, depth=3)
    assert exc_info.value.code == "delta_depth_exceeded"


def test_acceptance_walk_commits_date_and_topo(temp_store: tuple[ObjectStore, Path]) -> None:
    store, _ = temp_store
    # WHEN commits with committer times 100, 200 and 300 are chained with commit_tree
    # THEN walk_commits(head, order='date') yields them as 300, 200, 100, and order='topo' never yields a parent before its child
    empty_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    c1 = store.commit_tree(empty_tree, [], "commit 1", "A <a>", "C <c>", committer_time=100)
    c2 = store.commit_tree(empty_tree, [c1], "commit 2", "A <a>", "C <c>", committer_time=200)
    c3 = store.commit_tree(empty_tree, [c2], "commit 3", "A <a>", "C <c>", committer_time=300)

    # Date order
    date_walk = list(store.walk_commits(c3, order="date"))
    times = [c.committer_time for c in date_walk]
    assert times == [300, 200, 100]

    # Topo order
    topo_walk = list(store.walk_commits(c3, order="topo"))
    topo_oids = [c.oid for c in topo_walk]
    assert topo_oids == [c3, c2, c1]
    # Child never after parent
    assert topo_oids.index(c3) < topo_oids.index(c2) < topo_oids.index(c1)


def test_acceptance_invalid_ref_name(temp_store: tuple[ObjectStore, Path]) -> None:
    store, _ = temp_store
    # IF a requested ref name contains illegal sequences like '..' or trailing '/' THEN resolve_ref raises InvalidRefError with code 'invalid_ref_name'
    with pytest.raises(InvalidRefError) as exc1:
        store.resolve_ref("refs/heads/foo..bar")
    assert exc1.value.code == "invalid_ref_name"

    with pytest.raises(InvalidRefError) as exc2:
        store.resolve_ref("refs/heads/foo/")
    assert exc2.value.code == "invalid_ref_name"


def test_acceptance_write_pack_and_idx_magic(temp_store: tuple[ObjectStore, Path]) -> None:
    store, tmp = temp_store
    # WHEN write_pack writes 3 objects THEN a new ObjectStore reads each oid back with identical type and data, and the .idx file starts with the bytes b'\xfftOc\x00\x00\x00\x02'
    oid1 = store.hash_object(b"first blob content", "blob", write=True)
    oid2 = store.hash_object(b"second blob content", "blob", write=True)
    oid3 = store.hash_object(b"third blob content", "blob", write=True)

    out_pack = tmp / "out.pack"
    out_idx = tmp / "out.idx"
    pack_sha1, count = store.write_pack([oid1, oid2, oid3], out_pack, out_idx)
    assert count == 3
    assert len(pack_sha1) == 40

    idx_bytes = out_idx.read_bytes()
    assert idx_bytes.startswith(b"\xfftOc\x00\x00\x00\x02")

    # Create a fresh store with only this pack
    new_repo = tmp / "new_repo"
    new_store = ObjectStore(new_repo)
    new_store.init_repo()
    shutil.copy(out_pack, new_store.pack_dir / "pack-test.pack")
    shutil.copy(out_idx, new_store.pack_dir / "pack-test.idx")

    for expected_oid, expected_data in [
        (oid1, b"first blob content"),
        (oid2, b"second blob content"),
        (oid3, b"third blob content"),
    ]:
        read_obj = new_store.read_object(expected_oid)
        assert read_obj.oid == expected_oid
        assert read_obj.type == "blob"
        assert read_obj.data == expected_data


def test_acceptance_fuzz_random_bytes_pack_idx(temp_store: tuple[ObjectStore, Path]) -> None:
    _store, tmp = temp_store
    # IF FuzzPack feeds random bytes to the pack and idx parsers THEN only GitObjectsError subclasses such as 'pack_parse_error' are raised
    import secrets

    for _ in range(50):
        junk_len = 10 + secrets.randbelow(2039)
        junk = secrets.token_bytes(junk_len)
        junk_pack = tmp / "fuzz.pack"
        junk_idx = tmp / "fuzz.idx"
        junk_pack.write_bytes(junk)
        junk_idx.write_bytes(junk)

        try:
            idx = PackIndex(junk_idx)
            reader = PackReader(junk_pack, idx)
            reader.read_object_at_offset(secrets.randbelow(junk_len + 1))
        except Exception as exc:
            from git_objects.errors import GitObjectsError

            assert isinstance(exc, GitObjectsError)


def test_ref_delta_handling(temp_store: tuple[ObjectStore, Path]) -> None:
    _store, _ = temp_store
    base_data = b"base blob data"
    target_data = b"base blob data with extra suffix"
    delta_payload = create_delta(base_data, target_data)

    # Apply delta test
    applied = apply_delta(base_data, delta_payload)
    assert applied == target_data


def test_tree_changes_and_rename(temp_store: tuple[ObjectStore, Path]) -> None:
    store, _ = temp_store
    # Build tree 1 with file.txt
    blob1_oid = store.hash_object(b"unique content 12345", "blob", write=True)
    tree1_oid = store.hash_object(
        serialize_tree([GitTreeEntry(mode="100644", name="file.txt", oid=blob1_oid)]),
        "tree",
        write=True,
    )

    # Build tree 2 with renamed.txt having exact same content
    tree2_oid = store.hash_object(
        serialize_tree([GitTreeEntry(mode="100644", name="renamed.txt", oid=blob1_oid)]),
        "tree",
        write=True,
    )

    changes = store.diff_trees(tree1_oid, tree2_oid)
    assert len(changes) == 1
    assert changes[0].change_type == "renamed"
    assert changes[0].old_path == "file.txt"
    assert changes[0].path == "renamed.txt"
    assert changes[0].similarity == 1.0


def test_update_ref_locking_and_mismatch(temp_store: tuple[ObjectStore, Path]) -> None:
    store, _ = temp_store
    oid1 = store.hash_object(b"data 1", "blob", write=True)
    oid2 = store.hash_object(b"data 2", "blob", write=True)

    store.update_ref("refs/heads/feature", oid1)
    assert store.resolve_ref("refs/heads/feature") == oid1

    # Ref mismatch failure
    with pytest.raises(RefUpdateError) as exc:
        store.update_ref("refs/heads/feature", oid2, old=oid2)
    assert exc.value.code == "ref_mismatch"

    # Valid update with matching old
    store.update_ref("refs/heads/feature", oid2, old=oid1)
    assert store.resolve_ref("refs/heads/feature") == oid2


def test_write_tree_and_symlink(temp_store: tuple[ObjectStore, Path]) -> None:
    store, tmp = temp_store
    work = tmp / "work"
    work.mkdir()
    (work / "hello.txt").write_text("hello world")
    try:
        os.symlink("hello.txt", work / "link.txt")
    except OSError:
        pytest.skip("Symlink creation not permitted in this environment")

    tree_oid = store.write_tree(work)
    files = store.get_tree_files(tree_oid)
    assert "hello.txt" in files
    assert "link.txt" in files
    assert files["link.txt"][1] == "120000"


def test_cli_cat_file_and_hash_object(
    capsys: pytest.CaptureFixture[str],
    temp_store: tuple[ObjectStore, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from git_objects.__main__ import main

    _store, tmp = temp_store
    monkeypatch.chdir(tmp)

    test_file = tmp / "test.txt"
    test_file.write_text("cli test blob\n")

    # hash-object
    ret = main(["hash-object", "-w", str(test_file)])
    assert ret == 0
    captured = capsys.readouterr()
    blob_oid = captured.out.strip()
    assert len(blob_oid) == 40

    # cat-file -p
    ret = main(["cat-file", "-p", blob_oid])
    assert ret == 0
    captured = capsys.readouterr()
    assert captured.out.replace("\r\n", "\n") == "cli test blob\n"

    # cat-file -t
    ret = main(["cat-file", "-t", blob_oid])
    assert ret == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "blob"

    # cat-file -s
    ret = main(["cat-file", "-s", blob_oid])
    assert ret == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "15"


def test_cli_log_and_update_ref(
    capsys: pytest.CaptureFixture[str],
    temp_store: tuple[ObjectStore, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from git_objects.__main__ import main

    _store, tmp = temp_store
    monkeypatch.chdir(tmp)

    # Empty tree
    tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    ret = main(["commit-tree", tree, "-m", "Initial CLI commit"])
    assert ret == 0
    captured = capsys.readouterr()
    commit_oid = captured.out.strip()

    ret = main(["update-ref", "refs/heads/main", commit_oid])
    assert ret == 0

    ret = main(["log", "--date-order", "HEAD"])
    assert ret == 0
    captured = capsys.readouterr()
    assert commit_oid in captured.out


def test_adapters_import_error_message() -> None:
    import importlib

    # Test similarity adapter
    try:
        mod = importlib.import_module("git_objects.adapters.similarity")
        assert hasattr(mod, "diff_trees_with_similarity")
    except ImportError as exc:
        assert "'similarity' extra is required" in str(exc)

    # Test lint adapter
    try:
        mod = importlib.import_module("git_objects.adapters.lint")
        assert hasattr(mod, "lint_commits")
    except ImportError as exc:
        assert "'lint' extra is required" in str(exc)
