"""Stateful sequence-count management for CCSDS Space Packets."""

from __future__ import annotations

from .packet import (
    MAX_APID,
    MAX_SEQUENCE_COUNT,
    SPACE_PACKET_VERSION,
    BytesLike,
    PacketType,
    PacketValidationError,
    SequenceFlags,
    SpacePacket,
)

_SEQUENCE_MODULUS = MAX_SEQUENCE_COUNT + 1


def _validate_integer(name: str, value: object, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PacketValidationError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise PacketValidationError(
            f"{name} must be between {minimum} and {maximum}, got {value}"
        )
    return value


def _normalize_apid(apid: object) -> int:
    return _validate_integer("apid", apid, 0, MAX_APID)


def _normalize_packet_type(packet_type: object) -> PacketType:
    value = _validate_integer("packet_type", packet_type, 0, 1)
    return PacketType(value)


class SequenceCounter:
    """Manage independent sequence counts for APID streams.

    Instances are deliberately local, in-memory, and not thread-safe. The next
    value for a previously unseen stream is ``initial_value``.
    """

    _initial_value: int
    _next_values: dict[int, int]

    def __init__(self, initial_value: int = 0) -> None:
        self._initial_value = _validate_integer(
            "initial_value", initial_value, 0, MAX_SEQUENCE_COUNT
        )
        self._next_values = {}

    @property
    def initial_value(self) -> int:
        """First value assigned to a previously unseen or reset stream."""
        return self._initial_value

    def peek(self, *, apid: int) -> int:
        """Return a stream's next value without changing its state."""
        normalized_apid = _normalize_apid(apid)
        return self._next_values.get(normalized_apid, self._initial_value)

    def next(self, *, apid: int) -> int:
        """Return and advance a stream's sequence count."""
        normalized_apid = _normalize_apid(apid)
        value = self._next_values.get(normalized_apid, self._initial_value)
        self._next_values[normalized_apid] = (value + 1) % _SEQUENCE_MODULUS
        return value

    def set_next(self, *, apid: int, value: int) -> None:
        """Set the next sequence count returned for one stream."""
        normalized_apid = _normalize_apid(apid)
        self._next_values[normalized_apid] = _validate_integer(
            "value", value, 0, MAX_SEQUENCE_COUNT
        )

    def reset_stream(self, *, apid: int) -> None:
        """Return one stream to this counter's initial value."""
        normalized_apid = _normalize_apid(apid)
        self._next_values.pop(normalized_apid, None)

    def reset(self) -> None:
        """Return every stream to this counter's initial value."""
        self._next_values.clear()

    def create_packet(
        self,
        *,
        apid: int,
        packet_type: PacketType,
        data: BytesLike,
        version: int = SPACE_PACKET_VERSION,
        secondary_header_flag: bool = False,
        sequence_flags: SequenceFlags = SequenceFlags.UNSEGMENTED,
    ) -> SpacePacket:
        """Create a packet and advance its stream only after successful validation."""
        normalized_apid = _normalize_apid(apid)
        normalized_type = _normalize_packet_type(packet_type)
        sequence_count = self._next_values.get(normalized_apid, self._initial_value)
        packet = SpacePacket.create(
            apid=normalized_apid,
            packet_type=normalized_type,
            sequence_count=sequence_count,
            data=data,
            version=version,
            secondary_header_flag=secondary_header_flag,
            sequence_flags=sequence_flags,
        )
        self._next_values[normalized_apid] = (sequence_count + 1) % _SEQUENCE_MODULUS
        return packet
