"""Conformance test suite against real git."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from git_objects.store import ObjectStore


@pytest.mark.conformance
def test_conformance_real_git_verify_pack_and_fsck() -> None:
    """Verify packs written by library pass git verify-pack and git fsck."""
    if not shutil.which("git"):
        pytest.skip("git binary not installed")

    temp_dir = Path(tempfile.mkdtemp())
    try:
        # Init repo with git CLI
        subprocess.run(["git", "init", str(temp_dir)], check=True, capture_output=True)
        store = ObjectStore(temp_dir)

        # Create multiple objects via store
        oids = []
        for i in range(10):
            data = f"blob content {i}\n".encode()
            oid = store.hash_object(data, "blob", write=True)
            oids.append(oid)

        pack_path = store.pack_dir / "pack-conformance.pack"
        idx_path = store.pack_dir / "pack-conformance.idx"
        store.write_pack(oids, pack_path, idx_path)

        # Verify pack with git verify-pack
        res = subprocess.run(
            ["git", "verify-pack", "-v", str(idx_path)],
            cwd=temp_dir,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        for oid in oids:
            assert oid in res.stdout

        # Run git fsck --full
        fsck_res = subprocess.run(
            ["git", "fsck", "--full"],
            cwd=temp_dir,
            capture_output=True,
            text=True,
        )
        assert fsck_res.returncode == 0
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
