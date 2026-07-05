from dataclasses import dataclass

import pytest
from asterion.ccsds import (
    MAX_PACKET_DATA_LENGTH,
    DecodedPacketData,
    PacketType,
    PacketValidationError,
    SecondaryHeaderError,
    SpacePacket,
    create_packet_with_secondary_header,
    decode_packet_data,
    encode_packet_data,
)


@dataclass(frozen=True)
class ExampleHeader:
    kind: int


class ExampleCodec:
    def encode(self, header: ExampleHeader) -> bytes:
        return bytes([header.kind])

    def decode(self, data: memoryview) -> tuple[ExampleHeader, int]:
        if not data:
            raise ValueError("missing example header")
        return ExampleHeader(kind=data[0]), 1


class EmptyCodec:
    def encode(self, header: ExampleHeader) -> bytes:
        return b""

    def decode(self, data: memoryview) -> tuple[ExampleHeader, int]:
        return ExampleHeader(0), 0


class FailingCodec:
    def encode(self, header: ExampleHeader) -> bytes:
        raise RuntimeError("encode failure")

    def decode(self, data: memoryview) -> tuple[ExampleHeader, int]:
        raise RuntimeError("decode failure")


@pytest.mark.parametrize(
    "user_data", [b"payload", bytearray(b"payload"), memoryview(b"payload")]
)
def test_encode_packet_data_accepts_bytes_like_user_data(user_data: object) -> None:
    encoded = encode_packet_data(
        secondary_header=ExampleHeader(7),
        user_data=user_data,  # type: ignore[arg-type]
        codec=ExampleCodec(),
    )

    assert encoded == b"\x07payload"


def test_create_and_decode_secondary_header_round_trip() -> None:
    packet = create_packet_with_secondary_header(
        apid=42,
        packet_type=PacketType.TELEMETRY,
        sequence_count=9,
        secondary_header=ExampleHeader(7),
        user_data=b"payload",
        codec=ExampleCodec(),
    )

    assert packet.header.secondary_header_flag is True
    assert SpacePacket.from_bytes(bytes(packet)) == packet
    assert decode_packet_data(packet, codec=ExampleCodec()) == DecodedPacketData(
        secondary_header=ExampleHeader(7),
        user_data=b"payload",
    )


def test_secondary_header_may_be_the_entire_packet_data() -> None:
    packet = create_packet_with_secondary_header(
        apid=42,
        packet_type=PacketType.TELEMETRY,
        sequence_count=0,
        secondary_header=ExampleHeader(3),
        user_data=b"",
        codec=ExampleCodec(),
    )

    decoded = decode_packet_data(packet, codec=ExampleCodec())

    assert packet.data == b"\x03"
    assert decoded.user_data == b""


def test_decode_passes_read_only_view_to_codec() -> None:
    class ReadOnlyCodec(ExampleCodec):
        def decode(self, data: memoryview) -> tuple[ExampleHeader, int]:
            assert data.readonly is True
            return super().decode(data)

    packet = create_packet_with_secondary_header(
        apid=1,
        packet_type=PacketType.TELEMETRY,
        sequence_count=0,
        secondary_header=ExampleHeader(1),
        user_data=b"data",
        codec=ReadOnlyCodec(),
    )

    assert decode_packet_data(packet, codec=ReadOnlyCodec()).user_data == b"data"


def test_encode_rejects_empty_or_failing_codec_output() -> None:
    with pytest.raises(SecondaryHeaderError, match="at least one byte"):
        encode_packet_data(
            secondary_header=ExampleHeader(1), user_data=b"", codec=EmptyCodec()
        )
    with pytest.raises(SecondaryHeaderError, match="encode failure"):
        encode_packet_data(
            secondary_header=ExampleHeader(1), user_data=b"", codec=FailingCodec()
        )


def test_encode_rejects_invalid_user_data_and_oversized_result() -> None:
    with pytest.raises(PacketValidationError, match="user_data"):
        encode_packet_data(
            secondary_header=ExampleHeader(1),
            user_data=object(),  # type: ignore[arg-type]
            codec=ExampleCodec(),
        )
    with pytest.raises(PacketValidationError, match="combined packet data"):
        encode_packet_data(
            secondary_header=ExampleHeader(1),
            user_data=b"x" * MAX_PACKET_DATA_LENGTH,
            codec=ExampleCodec(),
        )


def test_decode_requires_packet_and_secondary_header_flag() -> None:
    with pytest.raises(PacketValidationError, match="SpacePacket"):
        decode_packet_data(object(), codec=ExampleCodec())  # type: ignore[arg-type]

    packet = SpacePacket.create(
        apid=1,
        packet_type=PacketType.TELEMETRY,
        sequence_count=0,
        data=b"data",
    )
    with pytest.raises(SecondaryHeaderError, match="does not declare"):
        decode_packet_data(packet, codec=ExampleCodec())


def test_decode_wraps_codec_failure() -> None:
    packet = create_packet_with_secondary_header(
        apid=1,
        packet_type=PacketType.TELEMETRY,
        sequence_count=0,
        secondary_header=ExampleHeader(1),
        user_data=b"data",
        codec=ExampleCodec(),
    )

    with pytest.raises(SecondaryHeaderError, match="decode failure"):
        decode_packet_data(packet, codec=FailingCodec())


@pytest.mark.parametrize("consumed", [0, 5, True, 1.5])
def test_decode_validates_codec_consumed_length(consumed: object) -> None:
    class ConsumedCodec(ExampleCodec):
        def decode(self, data: memoryview) -> tuple[ExampleHeader, int]:
            return ExampleHeader(1), consumed  # type: ignore[return-value]

    packet = create_packet_with_secondary_header(
        apid=1,
        packet_type=PacketType.TELEMETRY,
        sequence_count=0,
        secondary_header=ExampleHeader(1),
        user_data=b"x",
        codec=ExampleCodec(),
    )

    with pytest.raises(SecondaryHeaderError, match="consumed length"):
        decode_packet_data(packet, codec=ConsumedCodec())
