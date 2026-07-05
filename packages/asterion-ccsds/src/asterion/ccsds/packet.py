"""CCSDS Space Packet primary header and packet models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Self

type BytesLike = bytes | bytearray | memoryview

SPACE_PACKET_VERSION = 0
PRIMARY_HEADER_SIZE = 6
MAX_APID = 2_047
IDLE_APID = MAX_APID
MAX_SEQUENCE_COUNT = 16_383
MAX_PACKET_DATA_LENGTH = 65_536
MAX_PACKET_LENGTH = PRIMARY_HEADER_SIZE + MAX_PACKET_DATA_LENGTH


class CcsdsError(Exception):
    """Base exception for errors raised by this package."""


class PacketValidationError(CcsdsError, ValueError):
    """Raised when a packet or header contains an invalid value."""


class PacketDecodeError(CcsdsError, ValueError):
    """Raised when bytes cannot be decoded as a valid Space Packet."""


class PacketType(IntEnum):
    """The direction represented by a Space Packet."""

    TELEMETRY = 0
    TELECOMMAND = 1


class SequenceFlags(IntEnum):
    """The segmentation state of a Space Packet."""

    CONTINUATION = 0
    FIRST_SEGMENT = 1
    LAST_SEGMENT = 2
    UNSEGMENTED = 3


def _validate_integer(name: str, value: object, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PacketValidationError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise PacketValidationError(
            f"{name} must be between {minimum} and {maximum}, got {value}"
        )


def _normalize_packet_data(data: object) -> bytes:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise PacketValidationError("data must be bytes, bytearray, or memoryview")
    try:
        if isinstance(data, bytes):
            return data
        if isinstance(data, bytearray):
            return bytes(data)
        return data.tobytes()
    except (BufferError, OverflowError, TypeError, ValueError) as error:
        raise PacketValidationError(
            f"data is not a usable byte buffer: {error}"
        ) from error


def _decode_buffer(data: object, *, name: str) -> bytes:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise PacketDecodeError(f"{name} must be bytes, bytearray, or memoryview")
    try:
        if isinstance(data, bytes):
            return data
        if isinstance(data, bytearray):
            return bytes(data)
        return data.tobytes()
    except (BufferError, OverflowError, TypeError, ValueError) as error:
        raise PacketDecodeError(
            f"{name} is not a usable byte buffer: {error}"
        ) from error


@dataclass(frozen=True, slots=True)
class SpacePacketHeader:
    """The six-byte CCSDS Space Packet primary header.

    ``packet_data_length`` is the encoded CCSDS value: the number of packet data
    bytes minus one.
    """

    version: int
    packet_type: PacketType
    secondary_header_flag: bool
    apid: int
    sequence_flags: SequenceFlags
    sequence_count: int
    packet_data_length: int

    def __post_init__(self) -> None:
        _validate_integer(
            "version", self.version, SPACE_PACKET_VERSION, SPACE_PACKET_VERSION
        )
        _validate_integer("packet_type", self.packet_type, 0, 1)
        if not isinstance(self.secondary_header_flag, bool):
            raise PacketValidationError("secondary_header_flag must be a bool")
        _validate_integer("apid", self.apid, 0, MAX_APID)
        _validate_integer("sequence_flags", self.sequence_flags, 0, 3)
        _validate_integer("sequence_count", self.sequence_count, 0, MAX_SEQUENCE_COUNT)
        _validate_integer(
            "packet_data_length", self.packet_data_length, 0, MAX_PACKET_DATA_LENGTH - 1
        )

        object.__setattr__(self, "packet_type", PacketType(self.packet_type))
        object.__setattr__(self, "sequence_flags", SequenceFlags(self.sequence_flags))

    @property
    def data_length(self) -> int:
        """Number of packet data bytes declared by this header."""
        return self.packet_data_length + 1

    @property
    def total_length(self) -> int:
        """Complete packet length implied by this header."""
        return PRIMARY_HEADER_SIZE + self.data_length

    @property
    def is_idle(self) -> bool:
        """Whether this header uses the reserved idle-packet APID."""
        return self.apid == IDLE_APID

    def __bytes__(self) -> bytes:
        """Encode the primary header."""
        return self.to_bytes()

    def to_bytes(self) -> bytes:
        """Encode the primary header in big-endian network byte order."""
        first_word = (
            (self.version << 13)
            | (int(self.packet_type) << 12)
            | (int(self.secondary_header_flag) << 11)
            | self.apid
        )
        second_word = (int(self.sequence_flags) << 14) | self.sequence_count
        return (
            first_word.to_bytes(2, "big")
            + second_word.to_bytes(2, "big")
            + self.packet_data_length.to_bytes(2, "big")
        )

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> Self:
        """Decode exactly six bytes as a CCSDS primary header."""
        raw = _decode_buffer(data, name="primary header")
        if len(raw) != PRIMARY_HEADER_SIZE:
            raise PacketDecodeError(
                f"primary header must be {PRIMARY_HEADER_SIZE} bytes, got {len(raw)}"
            )

        first_word = int.from_bytes(raw[0:2], "big")
        second_word = int.from_bytes(raw[2:4], "big")
        try:
            return cls(
                version=(first_word >> 13) & 0b111,
                packet_type=PacketType((first_word >> 12) & 0b1),
                secondary_header_flag=bool((first_word >> 11) & 0b1),
                apid=first_word & 0x7FF,
                sequence_flags=SequenceFlags((second_word >> 14) & 0b11),
                sequence_count=second_word & 0x3FFF,
                packet_data_length=int.from_bytes(raw[4:6], "big"),
            )
        except PacketValidationError as error:
            raise PacketDecodeError(f"invalid primary header: {error}") from error


@dataclass(frozen=True, slots=True, init=False)
class SpacePacket:
    """A CCSDS Space Packet consisting of a primary header and packet data."""

    header: SpacePacketHeader
    data: bytes

    def __init__(self, header: SpacePacketHeader, data: BytesLike) -> None:
        """Initialize a packet, normalizing packet data to immutable bytes."""
        object.__setattr__(self, "header", header)
        object.__setattr__(self, "data", _normalize_packet_data(data))
        self.__post_init__()

    @classmethod
    def create(
        cls,
        *,
        apid: int,
        packet_type: PacketType,
        sequence_count: int,
        data: BytesLike,
        version: int = SPACE_PACKET_VERSION,
        secondary_header_flag: bool = False,
        sequence_flags: SequenceFlags = SequenceFlags.UNSEGMENTED,
    ) -> Self:
        """Create a packet and derive its primary header from packet data."""
        normalized_data = _normalize_packet_data(data)
        header = SpacePacketHeader(
            version=version,
            packet_type=packet_type,
            secondary_header_flag=secondary_header_flag,
            apid=apid,
            sequence_flags=sequence_flags,
            sequence_count=sequence_count,
            packet_data_length=len(normalized_data) - 1,
        )
        return cls(header=header, data=normalized_data)

    def __post_init__(self) -> None:
        if not isinstance(self.header, SpacePacketHeader):
            raise PacketValidationError("header must be a SpacePacketHeader")
        if not self.data:
            raise PacketValidationError("packet data must contain at least one byte")
        if len(self.data) > MAX_PACKET_DATA_LENGTH:
            raise PacketValidationError(
                f"packet data must not exceed {MAX_PACKET_DATA_LENGTH} bytes"
            )
        expected_length_field = len(self.data) - 1
        if self.header.packet_data_length != expected_length_field:
            raise PacketValidationError(
                "packet_data_length does not match data: "
                f"expected {expected_length_field}, got "
                f"{self.header.packet_data_length}"
            )

    @property
    def data_length(self) -> int:
        """Number of bytes in the packet data field."""
        return len(self.data)

    @property
    def total_length(self) -> int:
        """Complete serialized packet length."""
        return PRIMARY_HEADER_SIZE + self.data_length

    @property
    def is_idle(self) -> bool:
        """Whether this packet uses the reserved idle-packet APID."""
        return self.header.is_idle

    def __bytes__(self) -> bytes:
        """Encode the complete Space Packet."""
        return self.to_bytes()

    def __len__(self) -> int:
        """Return the complete serialized packet length."""
        return self.total_length

    def to_bytes(self) -> bytes:
        """Encode the complete Space Packet."""
        return self.header.to_bytes() + self.data

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> Self:
        """Decode one complete Space Packet from bytes."""
        raw = _decode_buffer(data, name="Space Packet")
        if len(raw) < PRIMARY_HEADER_SIZE:
            raise PacketDecodeError(
                f"Space Packet must contain at least {PRIMARY_HEADER_SIZE} header bytes"
            )

        header = SpacePacketHeader.from_bytes(raw[:PRIMARY_HEADER_SIZE])
        packet_data = raw[PRIMARY_HEADER_SIZE:]
        expected_data_length = header.data_length
        if len(packet_data) != expected_data_length:
            raise PacketDecodeError(
                "packet data length mismatch: "
                f"header declares {expected_data_length} bytes, got {len(packet_data)}"
            )
        return cls(header=header, data=packet_data)
