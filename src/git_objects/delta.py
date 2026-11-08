"""Git packfile delta decoder and encoder."""

from __future__ import annotations

from git_objects.errors import CorruptObjectError


def apply_delta(base: bytes, delta: bytes, max_output_bytes: int = 67108864) -> bytes:
    """Apply a git pack delta to a base object payload."""
    idx = 0
    delta_len = len(delta)

    def read_varint() -> int:
        nonlocal idx
        result = 0
        shift = 0
        while idx < delta_len:
            b = delta[idx]
            idx += 1
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
            if shift > 64:
                raise CorruptObjectError("Varint overflow in delta")
        return result

    base_size = read_varint()
    if base_size != len(base):
        raise CorruptObjectError(f"Delta base size mismatch: expected {len(base)}, got {base_size}")

    target_size = read_varint()
    if target_size > max_output_bytes:
        from git_objects.errors import ObjectSizeLimitError

        raise ObjectSizeLimitError(
            f"Decompressed delta target exceeds limit: {target_size} > {max_output_bytes}"
        )

    out = bytearray()

    while idx < delta_len:
        cmd = delta[idx]
        idx += 1
        if cmd & 0x80:
            # Copy from base
            cp_off = 0
            cp_size = 0
            if cmd & 0x01:
                if idx >= delta_len:
                    raise CorruptObjectError("Truncated delta copy offset byte 0")
                cp_off |= delta[idx]
                idx += 1
            if cmd & 0x02:
                if idx >= delta_len:
                    raise CorruptObjectError("Truncated delta copy offset byte 1")
                cp_off |= delta[idx] << 8
                idx += 1
            if cmd & 0x04:
                if idx >= delta_len:
                    raise CorruptObjectError("Truncated delta copy offset byte 2")
                cp_off |= delta[idx] << 16
                idx += 1
            if cmd & 0x08:
                if idx >= delta_len:
                    raise CorruptObjectError("Truncated delta copy offset byte 3")
                cp_off |= delta[idx] << 24
                idx += 1

            if cmd & 0x10:
                if idx >= delta_len:
                    raise CorruptObjectError("Truncated delta copy size byte 0")
                cp_size |= delta[idx]
                idx += 1
            if cmd & 0x20:
                if idx >= delta_len:
                    raise CorruptObjectError("Truncated delta copy size byte 1")
                cp_size |= delta[idx] << 8
                idx += 1
            if cmd & 0x40:
                if idx >= delta_len:
                    raise CorruptObjectError("Truncated delta copy size byte 2")
                cp_size |= delta[idx] << 16
                idx += 1

            if cp_size == 0:
                cp_size = 0x10000

            if cp_off + cp_size > len(base):
                raise CorruptObjectError("Delta copy extends past base object length")
            out.extend(base[cp_off : cp_off + cp_size])
        elif cmd > 0:
            # Insert literal data
            if idx + cmd > delta_len:
                raise CorruptObjectError("Truncated delta literal insertion")
            out.extend(delta[idx : idx + cmd])
            idx += cmd
        else:
            raise CorruptObjectError("Reserved delta command 0")

    if len(out) != target_size:
        raise CorruptObjectError(
            f"Delta target size mismatch: expected {target_size}, got {len(out)}"
        )

    return bytes(out)


def create_delta(base: bytes, target: bytes) -> bytes:
    """Create a minimal pack delta transforming base to target."""
    out = bytearray()

    def encode_varint(n: int) -> None:
        while True:
            b = n & 0x7F
            n >>= 7
            if n:
                out.append(b | 0x80)
            else:
                out.append(b)
                break

    encode_varint(len(base))
    encode_varint(len(target))

    # Literal insertion of entire target if no prefix match
    # Or prefix matching
    prefix_len = 0
    min_l = min(len(base), len(target))
    while prefix_len < min_l and base[prefix_len] == target[prefix_len]:
        prefix_len += 1

    if prefix_len >= 16:
        # Emit copy instruction for prefix
        cp_off = 0
        cp_size = prefix_len
        cmd = 0x80
        parts = bytearray()
        if cp_off & 0xFF:
            cmd |= 0x01
            parts.append(cp_off & 0xFF)
        if (cp_off >> 8) & 0xFF:
            cmd |= 0x02
            parts.append((cp_off >> 8) & 0xFF)
        if (cp_off >> 16) & 0xFF:
            cmd |= 0x04
            parts.append((cp_off >> 16) & 0xFF)
        if (cp_off >> 24) & 0xFF:
            cmd |= 0x08
            parts.append((cp_off >> 24) & 0xFF)

        if cp_size != 0x10000:
            if cp_size & 0xFF:
                cmd |= 0x10
                parts.append(cp_size & 0xFF)
            if (cp_size >> 8) & 0xFF:
                cmd |= 0x20
                parts.append((cp_size >> 8) & 0xFF)
            if (cp_size >> 16) & 0xFF:
                cmd |= 0x40
                parts.append((cp_size >> 16) & 0xFF)
        out.append(cmd)
        out.extend(parts)

        remainder = target[prefix_len:]
        # Insert remaining as literal chunks of max 127 bytes
        for i in range(0, len(remainder), 127):
            chunk = remainder[i : i + 127]
            out.append(len(chunk))
            out.extend(chunk)
    else:
        # Insert all target bytes in chunks of up to 127 bytes
        for i in range(0, len(target), 127):
            chunk = target[i : i + 127]
            out.append(len(chunk))
            out.extend(chunk)

    return bytes(out)
