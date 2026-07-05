"""Incremental decoding of CCSDS Space Packet byte streams."""

from __future__ import annotations

from ._buffer import ByteBufferError, copy_bytes
from .packet import (
    MAX_PACKET_LENGTH,
    PRIMARY_HEADER_SIZE,
    BytesLike,
    CcsdsError,
    PacketDecodeError,
    PacketValidationError,
    SpacePacket,
    SpacePacketHeader,
)

_MIN_PACKET_LENGTH = PRIMARY_HEADER_SIZE + 1


class IncompletePacketError(PacketDecodeError):
    """Raised when a stream ends with an incomplete Space Packet."""

    buffered_byte_count: int
    expected_packet_length: int | None

    def __init__(
        self,
        *,
        buffered_byte_count: int,
        expected_packet_length: int | None,
    ) -> None:
        self.buffered_byte_count = buffered_byte_count
        self.expected_packet_length = expected_packet_length
        if expected_packet_length is None:
            detail = "a complete primary header is not available"
        else:
            detail = f"the header declares {expected_packet_length} bytes"
        super().__init__(
            f"incomplete Space Packet: {buffered_byte_count} bytes buffered; {detail}"
        )


class DecoderStateError(CcsdsError, RuntimeError):
    """Raised when a failed stream decoder must be reset before reuse."""


class SpacePacketDecoder:
    """Incrementally decode complete Space Packets from arbitrary byte chunks.

    A malformed packet is fatal because a raw Space Packet stream has no marker
    that permits reliable byte-level resynchronization. Call :meth:`reset` before
    reusing a failed decoder.
    """

    _buffer: bytearray
    _failed: bool
    _max_packet_length: int

    def __init__(self, *, max_packet_length: int = MAX_PACKET_LENGTH) -> None:
        if isinstance(max_packet_length, bool) or not isinstance(
            max_packet_length, int
        ):
            raise PacketValidationError("max_packet_length must be an integer")
        if not _MIN_PACKET_LENGTH <= max_packet_length <= MAX_PACKET_LENGTH:
            raise PacketValidationError(
                "max_packet_length must be between "
                f"{_MIN_PACKET_LENGTH} and {MAX_PACKET_LENGTH}, "
                f"got {max_packet_length}"
            )
        self._max_packet_length = max_packet_length
        self._buffer = bytearray()
        self._failed = False

    @property
    def max_packet_length(self) -> int:
        """Largest complete packet accepted by this decoder."""
        return self._max_packet_length

    @property
    def buffered_byte_count(self) -> int:
        """Number of bytes retained for an incomplete or failed packet."""
        return len(self._buffer)

    @property
    def buffered_data(self) -> bytes:
        """Immutable snapshot of the currently buffered stream data."""
        return bytes(self._buffer)

    @property
    def is_failed(self) -> bool:
        """Whether a fatal decode error requires :meth:`reset`."""
        return self._failed

    def feed(self, data: BytesLike) -> list[SpacePacket]:
        """Consume a byte chunk and return all newly completed packets."""
        self._require_healthy()
        try:
            raw = copy_bytes(data)
        except ByteBufferError as error:
            raise PacketDecodeError(f"stream data {error}") from error
        if not raw:
            return []

        self._buffer.extend(raw)
        packets: list[SpacePacket] = []
        consumed = 0
        try:
            while len(self._buffer) - consumed >= PRIMARY_HEADER_SIZE:
                header_start = consumed
                header_end = header_start + PRIMARY_HEADER_SIZE
                header = SpacePacketHeader.from_bytes(
                    self._buffer[header_start:header_end]
                )
                packet_length = header.total_length
                if packet_length > self._max_packet_length:
                    raise PacketDecodeError(
                        f"Space Packet length {packet_length} exceeds configured maximum "
                        f"of {self._max_packet_length} bytes"
                    )
                if len(self._buffer) - consumed < packet_length:
                    break

                packet_end = header_start + packet_length
                packets.append(
                    SpacePacket.from_bytes(self._buffer[header_start:packet_end])
                )
                consumed = packet_end
        except PacketDecodeError:
            self._failed = True
            raise

        if consumed:
            del self._buffer[:consumed]
        return packets

    def finish(self) -> None:
        """Assert that no incomplete packet remains buffered."""
        self._require_healthy()
        if not self._buffer:
            return

        expected_packet_length: int | None = None
        if len(self._buffer) >= PRIMARY_HEADER_SIZE:
            expected_packet_length = SpacePacketHeader.from_bytes(
                self._buffer[:PRIMARY_HEADER_SIZE]
            ).total_length
        raise IncompletePacketError(
            buffered_byte_count=len(self._buffer),
            expected_packet_length=expected_packet_length,
        )

    def reset(self) -> None:
        """Clear all buffered data and restore the healthy decoder state."""
        self._buffer.clear()
        self._failed = False

    def _require_healthy(self) -> None:
        if self._failed:
            raise DecoderStateError(
                "SpacePacketDecoder is in a failed state; call reset() before reuse"
            )


def decode_packets(
    data: BytesLike,
    *,
    max_packet_length: int = MAX_PACKET_LENGTH,
) -> list[SpacePacket]:
    """Decode a buffer containing only complete Space Packets."""
    decoder = SpacePacketDecoder(max_packet_length=max_packet_length)
    packets = decoder.feed(data)
    decoder.finish()
    return packets
