"""Extension boundary for mission-specific Space Packet secondary headers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ._buffer import ByteBufferError, copy_bytes
from .packet import (
    MAX_PACKET_DATA_LENGTH,
    SPACE_PACKET_VERSION,
    BytesLike,
    CcsdsError,
    PacketType,
    PacketValidationError,
    SequenceFlags,
    SpacePacket,
)


class SecondaryHeaderError(CcsdsError, ValueError):
    """Raised when a secondary header cannot be encoded or decoded safely."""


class SecondaryHeaderCodec[HeaderT](Protocol):
    """Encode and decode one mission-specific secondary-header type."""

    def encode(self, header: HeaderT) -> BytesLike:
        """Encode a secondary header as one or more octets."""
        ...

    def decode(self, data: memoryview) -> tuple[HeaderT, int]:
        """Decode a header and return it with the number of consumed octets."""
        ...


@dataclass(frozen=True, slots=True)
class DecodedPacketData[HeaderT]:
    """A decoded secondary header and the remaining opaque user data."""

    secondary_header: HeaderT
    user_data: bytes


def encode_packet_data[HeaderT](
    *,
    secondary_header: HeaderT,
    user_data: BytesLike,
    codec: SecondaryHeaderCodec[HeaderT],
) -> bytes:
    """Encode a secondary header followed by opaque user data."""
    try:
        encoded = codec.encode(secondary_header)
    except SecondaryHeaderError:
        raise
    except Exception as error:
        raise SecondaryHeaderError(
            f"failed to encode secondary header: {error}"
        ) from error
    try:
        encoded_header = copy_bytes(encoded)
    except ByteBufferError as error:
        raise SecondaryHeaderError(f"encoded secondary header {error}") from error
    if not encoded_header:
        raise SecondaryHeaderError(
            "encoded secondary header must contain at least one byte"
        )

    try:
        normalized_user_data = copy_bytes(user_data)
    except ByteBufferError as error:
        raise PacketValidationError(f"user_data {error}") from error

    packet_data = encoded_header + normalized_user_data
    if len(packet_data) > MAX_PACKET_DATA_LENGTH:
        raise PacketValidationError(
            f"combined packet data must not exceed {MAX_PACKET_DATA_LENGTH} bytes"
        )
    return packet_data


def decode_packet_data[HeaderT](
    packet: SpacePacket,
    *,
    codec: SecondaryHeaderCodec[HeaderT],
) -> DecodedPacketData[HeaderT]:
    """Decode a packet's secondary header and preserve its remaining user data."""
    if not isinstance(packet, SpacePacket):
        raise PacketValidationError("packet must be a SpacePacket")
    if not packet.header.secondary_header_flag:
        raise SecondaryHeaderError("packet does not declare a secondary header")

    try:
        secondary_header, consumed = codec.decode(memoryview(packet.data).toreadonly())
    except SecondaryHeaderError:
        raise
    except Exception as error:
        raise SecondaryHeaderError(
            f"failed to decode secondary header: {error}"
        ) from error

    if isinstance(consumed, bool) or not isinstance(consumed, int):
        raise SecondaryHeaderError("codec consumed length must be an integer")
    if not 1 <= consumed <= packet.data_length:
        raise SecondaryHeaderError(
            f"codec consumed length must be between 1 and {packet.data_length}, "
            f"got {consumed}"
        )
    return DecodedPacketData(
        secondary_header=secondary_header,
        user_data=packet.data[consumed:],
    )


def create_packet_with_secondary_header[HeaderT](
    *,
    apid: int,
    packet_type: PacketType,
    sequence_count: int,
    secondary_header: HeaderT,
    user_data: BytesLike,
    codec: SecondaryHeaderCodec[HeaderT],
    version: int = SPACE_PACKET_VERSION,
    sequence_flags: SequenceFlags = SequenceFlags.UNSEGMENTED,
) -> SpacePacket:
    """Create a Space Packet containing a codec-defined secondary header."""
    packet_data = encode_packet_data(
        secondary_header=secondary_header,
        user_data=user_data,
        codec=codec,
    )
    return SpacePacket.create(
        apid=apid,
        packet_type=packet_type,
        sequence_count=sequence_count,
        data=packet_data,
        version=version,
        secondary_header_flag=True,
        sequence_flags=sequence_flags,
    )
