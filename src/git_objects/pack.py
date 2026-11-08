"""Git packfile v2 reader and writer with .idx v2 indexing."""

from __future__ import annotations

import hashlib
import struct
import zlib
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from git_objects.delta import apply_delta
from git_objects.errors import (
    CorruptObjectError,
    DeltaDepthExceededError,
    ObjectNotFoundError,
    ObjectSizeLimitError,
    PackParseError,
)
from git_objects.models import GitObject

# Pack object types
OBJ_BAD = -1
OBJ_NONE = 0
OBJ_COMMIT = 1
OBJ_TREE = 2
OBJ_BLOB = 3
OBJ_TAG = 4
OBJ_OFS_DELTA = 6
OBJ_REF_DELTA = 7

TYPE_NAMES = {
    OBJ_COMMIT: "commit",
    OBJ_TREE: "tree",
    OBJ_BLOB: "blob",
    OBJ_TAG: "tag",
}
NAME_TO_TYPE = {v: k for k, v in TYPE_NAMES.items()}


class LRUCache:
    """Thread-safe-free simple in-memory LRU cache capped at max_entries."""

    def __init__(self, max_entries: int = 1000) -> None:
        self._max_entries = max_entries
        self._data: OrderedDict[str, GitObject] = OrderedDict()

    def get(self, key: str) -> GitObject | None:
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def set(self, key: str, value: GitObject) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        if len(self._data) > self._max_entries:
            self._data.popitem(last=False)


class PackIndex:
    """Parser and lookup engine for Git pack .idx v2."""

    def __init__(self, idx_path: Path, data: bytes | None = None) -> None:
        self.idx_path = idx_path
        if data is None:
            try:
                self.data = idx_path.read_bytes()
            except OSError as err:
                raise PackParseError(f"Cannot read idx file: {err}") from err
        else:
            self.data = data

        self.offsets: dict[str, int] = {}
        self._parse()

    def _parse(self) -> None:
        data = self.data
        if len(data) < 1024 + 8 + 40:
            raise PackParseError("Index file too small")

        # Magic header and version
        magic, version = struct.unpack(">4sI", data[:8])
        if magic != b"\xfftOc" or version != 2:
            raise PackParseError(
                f"Unsupported pack index version or invalid magic: {magic!r} v{version}"
            )

        fanout = struct.unpack(">256I", data[8:1032])
        total_objects = fanout[255]

        # Table offsets
        sha_table_start = 1032
        sha_table_end = sha_table_start + 20 * total_objects
        crc_table_end = sha_table_end + 4 * total_objects
        off_table_end = crc_table_end + 4 * total_objects

        if len(data) < off_table_end + 40:
            raise PackParseError("Index file truncated")

        sha_data = data[sha_table_start:sha_table_end]
        off_data = data[crc_table_end:off_table_end]

        large_offsets: list[int] = []
        large_offset_count = (len(data) - 40 - off_table_end) // 8
        if large_offset_count > 0:
            for i in range(large_offset_count):
                start = off_table_end + i * 8
                (lo,) = struct.unpack(">Q", data[start : start + 8])
                large_offsets.append(lo)

        for i in range(total_objects):
            sha_hex = sha_data[i * 20 : (i + 1) * 20].hex()
            (off_val,) = struct.unpack(">I", off_data[i * 4 : (i + 1) * 4])
            if off_val & 0x80000000:
                idx_in_large = off_val & 0x7FFFFFFF
                if idx_in_large >= len(large_offsets):
                    raise PackParseError("Large offset index out of range")
                offset = large_offsets[idx_in_large]
            else:
                offset = off_val
            self.offsets[sha_hex] = offset


class PackReader:
    """Parser and object decoder for Git packfile v2."""

    def __init__(
        self,
        pack_path: Path,
        index: PackIndex,
        *,
        max_object_bytes: int = 67108864,
        max_delta_depth: int = 50,
        lru_cache: LRUCache | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.pack_path = pack_path
        self.index = index
        self.max_object_bytes = max_object_bytes
        self.max_delta_depth = max_delta_depth
        self.lru_cache = lru_cache if lru_cache is not None else LRUCache()
        self.on_event = on_event
        self._fp = None

    def contains(self, oid_hex: str) -> bool:
        return oid_hex in self.index.offsets

    def read_object(
        self, oid_hex: str, store_lookup: Callable[[str], GitObject] | None = None
    ) -> GitObject:
        if oid_hex not in self.index.offsets:
            raise ObjectNotFoundError(f"Object {oid_hex} not in pack {self.pack_path}")

        cached = self.lru_cache.get(oid_hex)
        if cached is not None:
            return cached

        offset = self.index.offsets[oid_hex]
        obj = self.read_object_at_offset(offset, store_lookup=store_lookup)
        if obj.oid != oid_hex:
            # Computed oid must match requested oid
            raise CorruptObjectError(
                f"Pack object sha1 mismatch: expected {oid_hex}, got {obj.oid}"
            )
        self.lru_cache.set(oid_hex, obj)
        return obj

    def read_object_at_offset(
        self,
        offset: int,
        depth: int = 0,
        store_lookup: Callable[[str], GitObject] | None = None,
    ) -> GitObject:
        if depth > self.max_delta_depth:
            raise DeltaDepthExceededError(
                f"Delta chain exceeded maximum depth {self.max_delta_depth}"
            )

        with open(self.pack_path, "rb") as f:
            f.seek(offset)
            # Read type and uncompressed size
            c = f.read(1)
            if not c:
                raise PackParseError("Unexpected EOF reading object header")
            b = c[0]
            obj_type = (b >> 4) & 7
            size = b & 0x0F
            shift = 4
            while b & 0x80:
                c = f.read(1)
                if not c:
                    raise PackParseError("Unexpected EOF reading varint size")
                b = c[0]
                size |= (b & 0x7F) << shift
                shift += 7
                if shift > 64:
                    raise PackParseError("Size header overflow")

            if size > self.max_object_bytes:
                raise ObjectSizeLimitError(
                    f"Object declared size {size} exceeds limit {self.max_object_bytes}"
                )

            base_offset: int | None = None
            base_ref: str | None = None

            if obj_type == OBJ_OFS_DELTA:
                # Negative offset
                c = f.read(1)
                if not c:
                    raise PackParseError("EOF reading OFS_DELTA negative offset")
                b = c[0]
                base_delta_offset = b & 0x7F
                while b & 0x80:
                    c = f.read(1)
                    if not c:
                        raise PackParseError("EOF in OFS_DELTA offset")
                    b = c[0]
                    base_delta_offset = ((base_delta_offset + 1) << 7) | (b & 0x7F)
                base_offset = offset - base_delta_offset
                if base_offset < 12 or base_offset >= offset:
                    raise PackParseError(f"Invalid base offset {base_offset} at {offset}")
            elif obj_type == OBJ_REF_DELTA:
                raw_ref = f.read(20)
                if len(raw_ref) < 20:
                    raise PackParseError("EOF reading REF_DELTA sha")
                base_ref = raw_ref.hex()

            # Decompress zlib payload
            decompressor = zlib.decompressobj()
            decompressed = bytearray()
            while not decompressor.eof:
                chunk = f.read(65536)
                if not chunk:
                    raise PackParseError("Truncated zlib stream in pack object")
                try:
                    part = decompressor.decompress(chunk)
                except zlib.error as err:
                    raise PackParseError(f"zlib decompress error: {err}") from err
                decompressed.extend(part)
                if len(decompressed) > self.max_object_bytes:
                    raise ObjectSizeLimitError("Decompressed stream exceeds max_object_bytes")

        raw_payload = bytes(decompressed)
        if len(raw_payload) != size:
            raise CorruptObjectError(
                f"Object size mismatch: expected {size}, got {len(raw_payload)}"
            )

        if obj_type in TYPE_NAMES:
            type_name = TYPE_NAMES[obj_type]
            data = raw_payload
        elif obj_type == OBJ_OFS_DELTA:
            if base_offset is None:
                raise PackParseError("Missing base offset for OFS_DELTA")
            base_obj = self.read_object_at_offset(base_offset, depth + 1, store_lookup=store_lookup)
            type_name = base_obj.type
            data = apply_delta(base_obj.data, raw_payload, max_output_bytes=self.max_object_bytes)
        elif obj_type == OBJ_REF_DELTA:
            if base_ref is None:
                raise PackParseError("Missing base ref for REF_DELTA")
            base_obj = None
            if base_ref in self.index.offsets:
                base_obj = self.read_object(base_ref, store_lookup=store_lookup)
            elif store_lookup is not None:
                base_obj = store_lookup(base_ref)
            if base_obj is None:
                raise ObjectNotFoundError(f"Base object {base_ref} for REF_DELTA not found")
            type_name = base_obj.type
            data = apply_delta(base_obj.data, raw_payload, max_output_bytes=self.max_object_bytes)
        else:
            raise PackParseError(f"Unknown pack object type {obj_type}")

        # Compute SHA-1
        full_header = f"{type_name} {len(data)}\x00".encode("ascii")
        oid = hashlib.sha1(full_header + data, usedforsecurity=False).hexdigest()

        if self.on_event is not None:
            # Safe event payload with no raw payload bytes
            self.on_event(
                {
                    "event": "read_pack_object",
                    "oid": oid,
                    "obj_type": type_name,
                    "size": len(data),
                    "pack_offset": offset,
                }
            )

        return GitObject(oid=oid, type=type_name, data=data)


def write_packfile(
    objects: list[GitObject],
    out_pack_path: Path,
    out_idx_path: Path,
) -> tuple[str, int]:
    """Write a packfile v2 and index v2 for a given list of GitObjects."""
    # Sort objects by sha1 for canonical .idx v2 ordering
    sorted_objects = sorted(objects, key=lambda o: o.oid)
    total = len(sorted_objects)

    pack_sha1 = hashlib.sha1(usedforsecurity=False)
    pack_data = bytearray()

    # 12-byte Pack Header: 'PACK', version 2, count
    header = struct.pack(">4sII", b"PACK", 2, total)
    pack_data.extend(header)
    pack_sha1.update(header)

    offsets: list[int] = []
    crcs: list[int] = []

    for obj in sorted_objects:
        offset = len(pack_data)
        offsets.append(offset)

        type_code = NAME_TO_TYPE.get(obj.type)
        if type_code is None:
            raise CorruptObjectError(f"Invalid object type: {obj.type}")

        # Varint header: (type << 4) | (size & 0x0F)
        size = len(obj.data)
        b = (type_code << 4) | (size & 0x0F)
        size >>= 4
        obj_hdr = bytearray()
        while size > 0:
            obj_hdr.append(b | 0x80)
            b = size & 0x7F
            size >>= 7
        obj_hdr.append(b)

        compressed = zlib.compress(obj.data)
        chunk = bytes(obj_hdr) + compressed
        pack_data.extend(chunk)
        pack_sha1.update(chunk)

        # Compute CRC32 on compressed data chunk
        crc = zlib.crc32(chunk) & 0xFFFFFFFF
        crcs.append(crc)

    pack_digest = pack_sha1.digest()
    pack_data.extend(pack_digest)
    out_pack_path.write_bytes(pack_data)

    # Generate .idx v2
    idx_data = bytearray()
    # Header: \xfftOc, version 2
    idx_data.extend(b"\xfftOc\x00\x00\x00\x02")

    # 256 fanout table
    fanout = [0] * 256
    for obj in sorted_objects:
        first_byte = int(obj.oid[:2], 16)
        fanout[first_byte] += 1
    for i in range(1, 256):
        fanout[i] += fanout[i - 1]
    for count in fanout:
        idx_data.extend(struct.pack(">I", count))

    # SHA table
    for obj in sorted_objects:
        idx_data.extend(bytes.fromhex(obj.oid))

    # CRC32 table
    for crc in crcs:
        idx_data.extend(struct.pack(">I", crc))

    # Offset table (32-bit with MSB flag for 64-bit if offset >= 2GB)
    large_offsets: list[int] = []
    for off in offsets:
        if off >= 0x80000000:
            idx_in_large = len(large_offsets)
            large_offsets.append(off)
            idx_data.extend(struct.pack(">I", idx_in_large | 0x80000000))
        else:
            idx_data.extend(struct.pack(">I", off))

    # Large offsets table
    for lo in large_offsets:
        idx_data.extend(struct.pack(">Q", lo))

    # Packfile SHA1 trailer
    idx_data.extend(pack_digest)

    # Index SHA1 trailer
    idx_sha1 = hashlib.sha1(idx_data, usedforsecurity=False).digest()
    idx_data.extend(idx_sha1)

    out_idx_path.write_bytes(idx_data)
    return pack_digest.hex(), total
