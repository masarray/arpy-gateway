"""Strict, portable register decoding; wire words are always big-endian.

Byte order swaps bytes INSIDE each 16-bit register; word order reverses 16-bit
word order. A scale/offset is applied exactly once, after decode.
"""
from __future__ import annotations
import math
import struct
from typing import Sequence
from arpy_gateway.domain.models import ValueType
from .configuration import ParsedSignal

_FORMATS = {ValueType.INT16: "h", ValueType.UINT16: "H",
            ValueType.INT32: "i", ValueType.UINT32: "I",
            ValueType.INT64: "q", ValueType.UINT64: "Q",
            ValueType.FLOAT32: "f", ValueType.FLOAT64: "d"}


def decode_value(point: ParsedSignal, words: Sequence[int | bool]) -> bool | int | float:
    if len(words) != point.width:
        raise ValueError("Decode data length mismatch")
    if point.signal.value_type is ValueType.BOOL:
        return bool(words[0])
    if point.signal.value_type not in _FORMATS:
        raise ValueError("Unsupported Modbus value type")
    for word in words:
        if isinstance(word, bool) or not isinstance(word, int) or not 0 <= word <= 65535:
            raise ValueError("Invalid raw register word")
    wire = [int(word).to_bytes(2, "big") for word in words]
    if point.word_order == "little":
        wire.reverse()
    if point.byte_order == "little":
        wire = [word[::-1] for word in wire]
    try:
        result = struct.unpack(">" + _FORMATS[point.signal.value_type], b"".join(wire))[0]
        value = result * point.scale + point.offset
    except (struct.error, OverflowError) as exc:
        raise ValueError("Numeric decode overflow") from exc
    if not math.isfinite(value):
        raise ValueError("Nonfinite float/scale result")
    # avoid precision loss of 64-bit unsigned integers when no scale needed
    if point.scale == 1 and point.offset == 0:
        return result
    return value
