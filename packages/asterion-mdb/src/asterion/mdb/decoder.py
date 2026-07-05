"""Bit extraction and fixed-layout telemetry decoding."""

from __future__ import annotations

import struct
from collections.abc import Mapping
from types import MappingProxyType

from .errors import InsufficientDataError, MdbDecodeError
from .model import (
    BinaryParameterType,
    BooleanParameterType,
    ByteOrder,
    EngineeringValue,
    EnumeratedParameterType,
    EnumeratedValue,
    FloatParameterType,
    IntegerParameterType,
    ParameterDefinition,
    ParameterType,
    ParameterValue,
    QualifiedName,
    RawValue,
)

type BytesLike = bytes | bytearray | memoryview


def normalize_bytes(data: object) -> bytes:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise MdbDecodeError("data must be bytes, bytearray, or memoryview")
    try:
        if isinstance(data, bytes):
            return data
        if isinstance(data, bytearray):
            return bytes(data)
        return data.tobytes()
    except (BufferError, TypeError, ValueError, OverflowError) as error:
        raise MdbDecodeError(f"data is not a usable byte buffer: {error}") from error


def extract_integer(
    data: bytes, *, offset: int, size: int, signed: bool, byte_order: ByteOrder
) -> int:
    end = offset + size
    if end > len(data) * 8:
        raise InsufficientDataError(required_bits=end, available_bits=len(data) * 8)
    if byte_order is ByteOrder.LITTLE_ENDIAN:
        if offset % 8 or size % 8:
            raise MdbDecodeError("little-endian integers must be byte-aligned")
        raw = int.from_bytes(data[offset // 8 : end // 8], "little", signed=False)
    else:
        full = int.from_bytes(data, "big")
        shift = len(data) * 8 - end
        raw = (full >> shift) & ((1 << size) - 1)
    if signed and raw & (1 << (size - 1)):
        raw -= 1 << size
    return raw


def decode_parameter(
    data: bytes,
    *,
    offset: int,
    parameter: ParameterDefinition,
    parameter_type: ParameterType,
) -> tuple[ParameterValue, int]:
    size = parameter_type.size_bits
    end = offset + size
    if end > len(data) * 8:
        raise InsufficientDataError(required_bits=end, available_bits=len(data) * 8)

    raw: RawValue
    value: EngineeringValue
    unit: str | None = None
    if isinstance(parameter_type, IntegerParameterType):
        integer = extract_integer(
            data,
            offset=offset,
            size=size,
            signed=parameter_type.signed,
            byte_order=parameter_type.byte_order,
        )
        raw = integer
        value = integer
        unit = parameter_type.unit
    elif isinstance(parameter_type, FloatParameterType):
        raw_bytes = data[offset // 8 : end // 8]
        format_code = "f" if size == 32 else "d"
        prefix = ">" if parameter_type.byte_order is ByteOrder.BIG_ENDIAN else "<"
        floating = struct.unpack(prefix + format_code, raw_bytes)[0]
        raw = floating
        value = floating
        unit = parameter_type.unit
    elif isinstance(parameter_type, BooleanParameterType):
        integer = extract_integer(
            data,
            offset=offset,
            size=size,
            signed=False,
            byte_order=parameter_type.byte_order,
        )
        raw = integer
        value = bool(integer)
    elif isinstance(parameter_type, EnumeratedParameterType):
        integer = extract_integer(
            data,
            offset=offset,
            size=size,
            signed=parameter_type.signed,
            byte_order=parameter_type.byte_order,
        )
        raw = integer
        value = EnumeratedValue(integer, dict(parameter_type.choices).get(integer))
    elif isinstance(parameter_type, BinaryParameterType):
        raw = data[offset // 8 : end // 8]
        value = raw
    else:
        raw_bytes = data[offset // 8 : end // 8]
        raw = raw_bytes
        unpadded = raw_bytes
        while unpadded.endswith(parameter_type.strip_padding):
            unpadded = unpadded[: -len(parameter_type.strip_padding)]
        try:
            value = unpadded.decode(parameter_type.encoding.value)
        except UnicodeDecodeError as error:
            raise MdbDecodeError(
                f"cannot decode string parameter {parameter.name}: {error}"
            ) from error
    return ParameterValue(parameter, raw, value, unit), end


def immutable_values(
    values: Mapping[QualifiedName, ParameterValue],
) -> MappingProxyType[QualifiedName, ParameterValue]:
    return MappingProxyType(dict(values))
