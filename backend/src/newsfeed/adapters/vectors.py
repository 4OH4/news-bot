"""The stored form of a vector (embeddings, profile centroids) shared by the adapters.

A vector is stored as packed little-endian float32 bytes: 4 bytes per value,
so a 384-value embedding is 1,536 bytes. Firestore indexes every element of an
array separately (384 index entries per article), but not the inside of a
bytes value, and float32 keeps more precision than cosine similarity needs
(ADR-0014). Reading back gives each value rounded to float32, within about
1e-7 relative of what was saved.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence

__all__ = ["pack_vector", "unpack_vector"]


def pack_vector(values: Sequence[float]) -> bytes:
    """Return ``values`` as little-endian float32 bytes.

    Raises ``OverflowError`` for a value beyond float32's range (about 3.4e38),
    which no unit-length vector has.
    """
    return struct.pack(f"<{len(values)}f", *values)


def unpack_vector(data: bytes) -> list[float]:
    """Return the floats packed by ``pack_vector``.

    Raises ``ValueError`` if the length is not a multiple of 4.
    """
    if len(data) % 4:
        raise ValueError(f"packed vector has {len(data)} bytes, not a multiple of 4")
    return list(struct.unpack(f"<{len(data) // 4}f", data))
