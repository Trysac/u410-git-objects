"""Additional unit tests targeting branch and edge-case coverage."""

from __future__ import annotations

import shutil
import tempfile
import zlib
from pathlib import Path

import pytest

from git_objects.errors import (
    CorruptObjectError,
    InvalidRefError,
    ObjectNotFoundError,
    ObjectSizeLimitError,
    PackParseError,
    RefUpdateError,
)
from git_objects.models import GitCommit, GitObject, GitTag, GitTreeEntry, TreeChange
from git_objects.pack import (
    LRUCache,
    PackIndex,
)
from git_objects.parsers import (
    parse_commit,
    parse_tag,
    parse_tree,
    serialize_commit,
)
from git_objects.store import ObjectStore


@pytest.fixture
def temp_store() -> tuple[ObjectStore, Path]:
    temp_dir = Path(tempfile.mkdtemp())
    store = ObjectStore(temp_dir)
    store.init_repo()
    yield store, temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_errors_truncation_and_context() -> None:
    long_msg = "x" * 300
    err = CorruptObjectError(long_msg, context={"k": "v"})
    assert len(err.message) == 200
    assert err.code == "corrupt_object"
    assert err.context == {"k": "v"}
    assert str(err) == err.message

    nf = ObjectNotFoundError("not found")
    assert nf.code == "object_not_found"
    ru = RefUpdateError("mismatch", code="ref_mismatch")
    assert ru.code == "ref_mismatch"


def test_models_fields() -> None:
    obj = GitObject(oid="1234", type="blob", data=b"hi")
    assert obj.oid == "1234"
    assert obj.type == "blob"
    assert obj.data == b"hi"

    entry = GitTreeEntry(mode="100644", name="foo", oid="1234")
    assert entry.name == "foo"

    commit = GitCommit(
        oid="c1",
        tree="t1",
        parents=["p1"],
        author="A <a>",
        author_time=1,
        author_tz="+0000",
        committer="C <c>",
        committer_time=2,
        committer_tz="+0000",
        message="msg",
        raw=b"raw",
    )
    assert commit.author_time == 1

    tag = GitTag(
        oid="t1",
        object_oid="o1",
        object_type="commit",
        tag_name="v1.0",
        tagger="T <t>",
        message="tag msg",
    )
    assert tag.tag_name == "v1.0"

    change = TreeChange(
        change_type="modified",
        path="file.txt",
        old_path="file.txt",
        old_oid="o1",
        new_oid="o2",
        old_mode="100644",
        new_mode="100644",
    )
    assert change.similarity is None


def test_lru_cache() -> None:
    cache = LRUCache(max_entries=2)
    o1 = GitObject("1", "blob", b"1")
    o2 = GitObject("2", "blob", b"2")
    o3 = GitObject("3", "blob", b"3")

    cache.set("1", o1)
    cache.set("2", o2)
    assert cache.get("1") == o1  # Marks 1 as recently used
    cache.set("3", o3)  # Evicts 2
    assert cache.get("2") is None
    assert cache.get("1") == o1
    assert cache.get("3") == o3

    cache.set("1", o1)
    assert cache.get("4") is None


def test_parsers_tree_and_commit_edge_cases() -> None:
    with pytest.raises(CorruptObjectError, match="missing mode separator"):
        parse_tree(b"100644file")

    with pytest.raises(CorruptObjectError, match="missing name terminator"):
        parse_tree(b"100644 file")

    with pytest.raises(CorruptObjectError, match="truncated SHA"):
        parse_tree(b"100644 file\x00shortsha")

    c_raw = b"tree 4b825dc642cb6eb9a060e54bf8d69288fbee4904\nauthor Auth <a@b.com> invalid_time +0000\ncommitter Comm <c@b.com>\n"
    c = parse_commit("abc", c_raw)
    assert c.author_time == 0
    assert c.committer_time == 0
    assert c.message == ""

    c_raw2 = b"author Simple\ncommitter Simple\n\nCommit text"
    c2 = parse_commit("abc2", c_raw2)
    assert c2.author == "Simple"
    assert c2.committer == "Simple"
    assert c2.message == "Commit text"

    s1 = serialize_commit(
        tree="4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        parents=["parent1"],
        message="test msg",
        author="Author <a@b.com>",
        committer="Committer <c@b.com>",
        author_time=123,
        committer_time=456,
    )
    assert b"author Author <a@b.com> 123 +0000\n" in s1
    assert b"committer Committer <c@b.com> 456 +0000\n" in s1

    s2 = serialize_commit(
        tree="4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        parents=[],
        message="no email format",
        author="JustAuthor",
        committer="JustComm",
    )
    assert b"author JustAuthor 0 +0000\n" in s2

    tag_raw = (
        b"object 4b825dc642cb6eb9a060e54bf8d69288fbee4904\n"
        b"type tree\n"
        b"tag v0.1\n"
        b"tagger Tagger <t@t.com>\n"
        b"\n"
        b"Release tag message\n"
    )
    parsed_tag = parse_tag("tag123", tag_raw)
    assert parsed_tag.object_oid == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    assert parsed_tag.object_type == "tree"
    assert parsed_tag.tag_name == "v0.1"
    assert parsed_tag.tagger == "Tagger <t@t.com>"
    assert parsed_tag.message == "Release tag message\n"


def test_pack_index_and_reader_errors(temp_store: tuple[ObjectStore, Path]) -> None:
    _store, tmp = temp_store
    missing = tmp / "missing.idx"
    with pytest.raises(PackParseError, match="Cannot read idx file"):
        PackIndex(missing)

    small_idx = tmp / "small.idx"
    small_idx.write_bytes(b"short")
    with pytest.raises(PackParseError, match="Index file too small"):
        PackIndex(small_idx)

    bad_magic_idx = tmp / "bad_magic.idx"
    bad_magic_idx.write_bytes(b"\x00" * 2000)
    with pytest.raises(PackParseError, match="Unsupported pack index version"):
        PackIndex(bad_magic_idx)


def test_pack_reader_ref_delta_resolution(temp_store: tuple[ObjectStore, Path]) -> None:
    store, _ = temp_store
    base_data = b"Base object for ref delta resolution\n"
    target_data = b"Base object for ref delta resolution with modifications\n"
    base_oid = store.hash_object(base_data, "blob", write=True)

    from git_objects.delta import create_delta

    delta_payload = create_delta(base_data, target_data)

    import hashlib
    import struct

    pack_data = bytearray()
    pack_data.extend(struct.pack(">4sII", b"PACK", 2, 1))

    # Varint encode delta_payload length
    sz = len(delta_payload)
    b = (7 << 4) | (sz & 0x0F)
    sz >>= 4
    while sz > 0:
        pack_data.append(b | 0x80)
        b = sz & 0x7F
        sz >>= 7
    pack_data.append(b)

    pack_data.extend(bytes.fromhex(base_oid))
    pack_data.extend(zlib.compress(delta_payload))

    pack_sha = hashlib.sha1(pack_data, usedforsecurity=False).digest()
    pack_data.extend(pack_sha)

    pack_path = store.pack_dir / "pack-ref.pack"
    idx_path = store.pack_dir / "pack-ref.idx"
    pack_path.write_bytes(pack_data)

    target_sha = hashlib.sha1(
        f"blob {len(target_data)}\x00".encode() + target_data, usedforsecurity=False
    ).digest()

    idx_data = bytearray(b"\xfftOc\x00\x00\x00\x02")
    fanout = [0] * 256
    fanout[target_sha[0]] = 1
    for i in range(1, 256):
        fanout[i] += fanout[i - 1]
    for count in fanout:
        idx_data.extend(struct.pack(">I", count))
    idx_data.extend(target_sha)
    idx_data.extend(struct.pack(">I", 0))
    idx_data.extend(struct.pack(">I", 12))
    idx_data.extend(pack_sha)
    idx_sha = hashlib.sha1(idx_data, usedforsecurity=False).digest()
    idx_data.extend(idx_sha)
    idx_path.write_bytes(idx_data)

    target_hex = target_sha.hex()
    obj = store.read_object(target_hex)
    assert obj.data == target_data
    assert obj.type == "blob"


def test_loose_object_reading_errors(temp_store: tuple[ObjectStore, Path]) -> None:
    store, _ = temp_store

    with pytest.raises(ObjectNotFoundError, match="Invalid object ID length"):
        store.read_object("abc")

    with pytest.raises(ObjectNotFoundError, match="not found"):
        store.read_object("0" * 40)

    fake_oid = "1234567890123456789012345678901234567890"
    obj_file = store.objects_dir / fake_oid[:2] / fake_oid[2:]
    obj_file.parent.mkdir(parents=True, exist_ok=True)

    obj_file.write_bytes(b"not zlib")
    with pytest.raises(CorruptObjectError, match="Corrupt loose object"):
        store.read_object(fake_oid)

    obj_file.write_bytes(zlib.compress(b"blob 100"))
    with pytest.raises(CorruptObjectError, match="Malformed loose object header"):
        store.read_object(fake_oid)

    obj_file.write_bytes(zlib.compress(b"blob_extra_stuff\x00data"))
    with pytest.raises(CorruptObjectError, match="Malformed header format"):
        store.read_object(fake_oid)

    obj_file.write_bytes(zlib.compress(b"blob notanumber\x00data"))
    with pytest.raises(CorruptObjectError, match="Invalid size"):
        store.read_object(fake_oid)

    obj_file.write_bytes(zlib.compress(b"blob 10\x00data"))
    with pytest.raises(CorruptObjectError, match="Size mismatch"):
        store.read_object(fake_oid)

    obj_file.write_bytes(zlib.compress(b"blob 4\x00data"))
    with pytest.raises(CorruptObjectError, match="Hash mismatch"):
        store.read_object(fake_oid)


def test_packed_refs_and_peeling(temp_store: tuple[ObjectStore, Path]) -> None:
    store, _ = temp_store
    c_oid = store.commit_tree(
        tree="4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        parents=[],
        message="root",
        author="A <a>",
        committer="C <c>",
    )
    tag_data = (f"object {c_oid}\ntype commit\ntag v1.0.0\ntagger T <a>\n\nRelease\n").encode()
    tag_oid = store.hash_object(tag_data, "tag", write=True)

    packed_refs = store.git_dir / "packed-refs"
    packed_refs.write_text(
        f"# pack-refs with: peeled fully-peeled sorted\n"
        f"{tag_oid} refs/tags/v1.0.0\n"
        f"^{c_oid}\n"
        f"{c_oid} refs/heads/packed_branch\n",
        encoding="utf-8",
    )

    assert store.resolve_ref("refs/tags/v1.0.0") == tag_oid
    assert store.resolve_ref("refs/heads/packed_branch") == c_oid
    assert store.resolve_ref("packed_branch") == c_oid

    assert store.rev_parse("v1.0.0~0") == c_oid or store.rev_parse("v1.0.0^0") == c_oid
    assert store.rev_parse("packed_branch") == c_oid


def test_ref_symbolic_and_direct(temp_store: tuple[ObjectStore, Path]) -> None:
    store, _ = temp_store
    c_oid = store.commit_tree(
        tree="4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        parents=[],
        message="root",
        author="A <a>",
        committer="C <c>",
    )
    # Direct hash in HEAD
    (store.git_dir / "HEAD").write_text(f"{c_oid}\n", encoding="utf-8")
    assert store.resolve_ref("HEAD") == c_oid

    # Symbolic loose ref
    (store.git_dir / "refs" / "heads" / "sym").write_text(
        "ref: refs/heads/main\n", encoding="utf-8"
    )
    (store.git_dir / "refs" / "heads" / "main").write_text(f"{c_oid}\n", encoding="utf-8")
    assert store.resolve_ref("refs/heads/sym") == c_oid
    assert store.resolve_ref("sym") == c_oid


def test_walk_commits_invalid_order_and_write_tree_invalid(
    temp_store: tuple[ObjectStore, Path],
) -> None:
    store, tmp = temp_store
    c_oid = store.commit_tree(
        tree="4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        parents=[],
        message="root",
        author="A <a>",
        committer="C <c>",
    )
    with pytest.raises(InvalidRefError, match="Unsupported walk_commits order"):
        list(store.walk_commits(c_oid, order="invalid"))

    with pytest.raises(InvalidRefError, match="invalid directory"):
        store.write_tree(tmp / "nonexistent_dir")


def test_peeling_caret_and_tilde(temp_store: tuple[ObjectStore, Path]) -> None:
    store, _ = temp_store
    c1 = store.commit_tree(
        tree="4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        parents=[],
        message="c1",
        author="A <a>",
        committer="C <c>",
    )
    c2 = store.commit_tree(
        tree="4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        parents=[],
        message="c2",
        author="A <a>",
        committer="C <c>",
    )
    c3 = store.commit_tree(
        tree="4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        parents=[c1, c2],
        message="merge",
        author="A <a>",
        committer="C <c>",
    )
    store.update_ref("refs/heads/merge", c3)

    assert store.rev_parse("refs/heads/merge^1") == c1
    assert store.rev_parse("refs/heads/merge^2") == c2
    assert store.rev_parse("refs/heads/merge^0") == c3

    with pytest.raises(InvalidRefError, match="does not have parent"):
        store.rev_parse("refs/heads/merge^3")


def test_rev_parse_prefix_and_errors(temp_store: tuple[ObjectStore, Path]) -> None:
    store, _ = temp_store
    oid1 = store.hash_object(b"test prefix matching 1", "blob", write=True)
    oid2 = store.hash_object(b"test prefix matching 2", "blob", write=True)

    assert store.rev_parse(oid1) == oid1

    prefix = oid1[:7]
    if not oid2.startswith(prefix):
        assert store.rev_parse(prefix) == oid1

    with pytest.raises(InvalidRefError, match="Empty revision"):
        store.rev_parse("  ")

    with pytest.raises(InvalidRefError, match="could not be resolved"):
        store.rev_parse("nonexistent-ref")


def test_validate_ref_name_rules(temp_store: tuple[ObjectStore, Path]) -> None:
    store, _ = temp_store
    invalid_names = [
        "",
        "@",
        "/starts/slash",
        "ends/slash/",
        "contains/../dots",
        "refs/heads/.hidden",
        "refs/heads/has space",
        "refs/heads/has~tilde",
        "refs/heads/has^caret",
        "refs/heads/has:colon",
        "refs/heads/has?question",
        "refs/heads/has*star",
        "refs/heads/has[bracket",
        "refs/heads/has\\backslash",
        "refs/heads/ends.lock",
        "refs/heads/ends.",
        "refs/heads/@{",
    ]
    for name in invalid_names:
        with pytest.raises(InvalidRefError):
            store.validate_ref_name(name)


def test_write_tree_and_diff(temp_store: tuple[ObjectStore, Path]) -> None:
    store, tmp = temp_store
    work = tmp / "work_tree"
    work.mkdir()
    sub = work / "subdir"
    sub.mkdir()
    (sub / "subfile.txt").write_text("subcontent")
    (work / "file1.txt").write_text("content1")
    (work / "file2.txt").write_text("content2")

    tree1_oid = store.write_tree(work)
    files = store.get_tree_files(tree1_oid)
    assert "subdir/subfile.txt" in files
    assert "file1.txt" in files
    assert "file2.txt" in files

    (work / "file1.txt").write_text("content1_modified")
    (work / "file2.txt").unlink()
    (work / "file3.txt").write_text("content3")

    tree2_oid = store.write_tree(work)
    changes = store.diff_trees(tree1_oid, tree2_oid)
    change_types = {c.path: c.change_type for c in changes}
    assert change_types["file1.txt"] == "modified"
    assert change_types["file2.txt"] == "deleted"
    assert change_types["file3.txt"] == "added"


def test_cli_all_commands(
    capsys: pytest.CaptureFixture[str],
    temp_store: tuple[ObjectStore, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from git_objects.__main__ import main

    _store, tmp = temp_store
    monkeypatch.chdir(tmp)

    assert main([]) == 2

    (tmp / "sample.txt").write_text("sample text")
    ret = main(["write-tree", str(tmp)])
    assert ret == 0
    tree_oid = capsys.readouterr().out.strip()
    assert len(tree_oid) == 40

    ret = main(["commit-tree", tree_oid, "-m", "CLI commit message"])
    assert ret == 0
    commit_oid = capsys.readouterr().out.strip()
    assert len(commit_oid) == 40

    ret = main(["update-ref", "refs/heads/main", commit_oid])
    assert ret == 0

    ret = main(["rev-parse", "HEAD"])
    assert ret == 0
    assert capsys.readouterr().out.strip() == commit_oid

    ret = main(["ls-tree", tree_oid])
    assert ret == 0
    out = capsys.readouterr().out
    assert "sample.txt" in out

    ret = main(["ls-tree", "-r", tree_oid])
    assert ret == 0
    out_r = capsys.readouterr().out
    assert "sample.txt" in out_r

    ret = main(["log", "--topo-order", "HEAD"])
    assert ret == 0
    assert commit_oid in capsys.readouterr().out

    ret = main(["cat-file", "-p", tree_oid])
    assert ret == 0
    assert "sample.txt" in capsys.readouterr().out

    ret = main(["cat-file", "-t", tree_oid])
    assert ret == 0
    assert capsys.readouterr().out.strip() == "tree"

    ret = main(["cat-file", "-s", tree_oid])
    assert ret == 0
    assert int(capsys.readouterr().out.strip()) > 0

    ret = main(["hash-object", "nonexistent.txt"])
    assert ret == 1
    assert "File not found" in capsys.readouterr().err

    ret = main(["rev-parse", "invalid..ref"])
    assert ret == 1
    assert "Error:" in capsys.readouterr().err


def test_on_event_callback(temp_store: tuple[ObjectStore, Path]) -> None:
    events: list[dict[str, object]] = []

    def callback(ev: dict[str, object]) -> None:
        events.append(ev)

    store = ObjectStore(temp_store[1], on_event=callback)
    oid = store.hash_object(b"callback test", "blob", write=True)
    assert any(e["event"] == "write_loose_object" and e["oid"] == oid for e in events)

    obj = store.read_object(oid)
    assert obj.data == b"callback test"
    assert any(e["event"] == "read_loose_object" and e["oid"] == oid for e in events)


def test_delta_instructions_and_errors() -> None:
    from git_objects.delta import apply_delta, create_delta

    base = b"abcdefghijklmnopqrstuvwxyz" * 2
    target = b"abcdefghijklmnopqrstuvwxyz" * 2 + b" extra ending"

    # create_delta with prefix match >= 16
    delta_bytes = create_delta(base, target)
    reconstructed = apply_delta(base, delta_bytes)
    assert reconstructed == target

    # create_delta with small or no match
    d2 = create_delta(b"hello", b"world")
    assert apply_delta(b"hello", d2) == b"world"

    # Corrupt delta errors
    # 1. Base size mismatch
    with pytest.raises(CorruptObjectError, match="Delta base size mismatch"):
        apply_delta(b"short", delta_bytes)

    # 2. Output size limit
    with pytest.raises(ObjectSizeLimitError, match="exceeds limit"):
        apply_delta(base, delta_bytes, max_output_bytes=5)

    # 3. Truncated commands
    bad_delta_copy = bytes([len(base), 10, 0x81])  # 0x81 needs offset byte 0
    with pytest.raises(CorruptObjectError, match="Truncated delta copy offset byte 0"):
        apply_delta(base, bad_delta_copy)

    bad_delta_copy2 = bytes([len(base), 10, 0x82])
    with pytest.raises(CorruptObjectError, match="Truncated delta copy offset byte 1"):
        apply_delta(base, bad_delta_copy2)

    bad_delta_copy3 = bytes([len(base), 10, 0x84])
    with pytest.raises(CorruptObjectError, match="Truncated delta copy offset byte 2"):
        apply_delta(base, bad_delta_copy3)

    bad_delta_copy4 = bytes([len(base), 10, 0x88])
    with pytest.raises(CorruptObjectError, match="Truncated delta copy offset byte 3"):
        apply_delta(base, bad_delta_copy4)

    bad_delta_size0 = bytes([len(base), 10, 0x90])
    with pytest.raises(CorruptObjectError, match="Truncated delta copy size byte 0"):
        apply_delta(base, bad_delta_size0)

    bad_delta_size1 = bytes([len(base), 10, 0xA0])
    with pytest.raises(CorruptObjectError, match="Truncated delta copy size byte 1"):
        apply_delta(base, bad_delta_size1)

    bad_delta_size2 = bytes([len(base), 10, 0xC0])
    with pytest.raises(CorruptObjectError, match="Truncated delta copy size byte 2"):
        apply_delta(base, bad_delta_size2)

    bad_delta_overflow = bytes([len(base), 10, 0x90, 100])
    with pytest.raises(CorruptObjectError, match="Delta copy extends past base"):
        apply_delta(base, bad_delta_overflow)

    bad_literal = bytes([len(base), 10, 0x05, ord("a")])
    with pytest.raises(CorruptObjectError, match="Truncated delta literal insertion"):
        apply_delta(base, bad_literal)

    bad_cmd_zero = bytes([len(base), 10, 0x00])
    with pytest.raises(CorruptObjectError, match="Reserved delta command 0"):
        apply_delta(base, bad_cmd_zero)

    bad_target_size = bytes([len(base), 10, 0x01, ord("a")])
    with pytest.raises(CorruptObjectError, match="Delta target size mismatch"):
        apply_delta(base, bad_target_size)


def test_adapters_similarity_and_lint(temp_store: tuple[ObjectStore, Path]) -> None:
    store, _ = temp_store

    # Test similarity adapter if available
    try:
        from git_objects.adapters.similarity import diff_trees_with_similarity

        doc1 = b"The quick brown fox jumps over the lazy dog and runs away into the forest.\n"
        doc2 = b"The quick brown fox jumps over the lazy dog and runs away into the woods.\n"
        oid1 = store.hash_object(doc1, "blob", write=True)
        oid2 = store.hash_object(doc2, "blob", write=True)

        from git_objects.models import GitTreeEntry
        from git_objects.parsers import serialize_tree

        tree1 = store.hash_object(
            serialize_tree([GitTreeEntry(mode="100644", name="old_doc.txt", oid=oid1)]),
            "tree",
            write=True,
        )
        tree2 = store.hash_object(
            serialize_tree([GitTreeEntry(mode="100644", name="new_doc.txt", oid=oid2)]),
            "tree",
            write=True,
        )

        sim_changes = diff_trees_with_similarity(store, tree1, tree2, threshold=0.5)
        assert len(sim_changes) == 1
        assert sim_changes[0].change_type == "renamed"
        assert sim_changes[0].old_path == "old_doc.txt"
        assert sim_changes[0].path == "new_doc.txt"

        # Edge case: no deleted or no added
        assert diff_trees_with_similarity(store, tree1, tree1) == []
    except ImportError:
        pass

    # Test lint adapter if available
    try:
        from git_objects.adapters.lint import lint_commits

        # Commit with invalid conventional commit message
        c_bad = store.commit_tree(
            tree="4b825dc642cb6eb9a060e54bf8d69288fbee4904",
            parents=[],
            message="bad commit message without type\n",
            author="A <a>",
            committer="C <c>",
        )
        results = list(lint_commits(store, c_bad))
        assert len(results) == 1
        commit, findings = results[0]
        assert commit.oid == c_bad
        assert len(findings) > 0

        # CLI lint-commits
        from git_objects.__main__ import main

        assert main(["lint-commits", c_bad]) == 1
    except ImportError:
        pass


def test_delta_all_byte_combinations() -> None:
    from git_objects.delta import apply_delta, create_delta

    # Test create_delta with matching prefix and trailing differences
    b1 = b"0123456789abcdef" * 3 + b"suffix_1"
    b2 = b"0123456789abcdef" * 3 + b"suffix_2"
    d = create_delta(b1, b2)
    assert apply_delta(b1, d) == b2

    # Test all offset bits and size bits
    # 0x01 | 0x02 | 0x04 | 0x08 | 0x10 | 0x20 | 0x40 = 0x7F -> cmd = 0xFF
    # Let's craft a valid copy command with multiple offset and size bytes
    base = b"x" * 70000
    delta = bytearray()
    # base size: 70000
    sz = 70000
    while sz > 0x7F:
        delta.append((sz & 0x7F) | 0x80)
        sz >>= 7
    delta.append(sz & 0x7F)
    # target size: 500
    sz = 500
    while sz > 0x7F:
        delta.append((sz & 0x7F) | 0x80)
        sz >>= 7
    delta.append(sz & 0x7F)

    # cmd: 0x80 | 0x01 | 0x02 | 0x10 | 0x20 (off byte 0 and 1, size byte 0 and 1)
    # offset: 300 (0x012c -> byte 0 = 0x2c, byte 1 = 0x01)
    # size: 500 (0x01f4 -> byte 0 = 0xf4, byte 1 = 0x01)
    delta.extend([0x80 | 0x01 | 0x02 | 0x10 | 0x20, 0x2C, 0x01, 0xF4, 0x01])
    res = apply_delta(base, bytes(delta))
    assert len(res) == 500


def test_pack_reader_and_store_error_branches(temp_store: tuple[ObjectStore, Path]) -> None:
    store, tmp = temp_store
    from git_objects.errors import (
        DeltaDepthExceededError,
        ObjectNotFoundError,
        ObjectSizeLimitError,
    )
    from git_objects.pack import PackReader

    # Test PackReader depth exceeded
    reader = PackReader(tmp / "nonexistent.pack", None, max_delta_depth=1)  # type: ignore
    with pytest.raises(DeltaDepthExceededError):
        reader.read_object_at_offset(0, depth=2)

    # Test store commit_tree / rev_parse errors
    with pytest.raises(InvalidRefError):
        store.rev_parse("nonexistent^1")

    # Loose object reading error: unreadable
    unreadable = store.objects_dir / "ff" / ("f" * 38)
    unreadable.parent.mkdir(parents=True, exist_ok=True)
    unreadable.touch()
    # Mocking read_bytes to raise OSError
    from unittest.mock import patch

    with (
        patch.object(Path, "read_bytes", side_effect=OSError("disk error")),
        pytest.raises(ObjectNotFoundError, match="Cannot read object"),
    ):
        store.read_object("ff" + "f" * 38)

    # Object size limit for loose decompressed object
    decomp_store = ObjectStore(tmp, max_object_bytes=5)
    large_loose = store.hash_object(b"short", "blob", write=True)
    with pytest.raises(ObjectSizeLimitError):
        decomp_store.read_object(large_loose)

    # Pack index with 64-bit large offsets
    import struct

    fanout_bytes = bytearray()
    # 256 integers of 4 bytes each, with value 1 from index 0xAA to 255
    for i in range(256):
        fanout_bytes.extend(struct.pack(">I", 1 if i >= 0xAA else 0))

    idx_large = bytearray(b"\xfftOc\x00\x00\x00\x02")
    idx_large.extend(fanout_bytes)
    idx_large.extend(b"\xaa" * 20)  # 1 sha
    idx_large.extend(b"\x00" * 4)  # 1 crc
    idx_large.extend(struct.pack(">I", 0 | 0x80000000))  # offset pointing to large offset 0
    idx_large.extend(struct.pack(">Q", 123456789))  # large offset 0
    idx_large.extend(b"\x00" * 40)  # pack sha + idx sha
    idx_file = tmp / "large.idx"
    idx_file.write_bytes(idx_large)
    p_idx = PackIndex(idx_file)
    assert p_idx.offsets["aa" * 20] == 123456789
