import pytest
from asterion.ccsds import (
    MAX_PACKET_DATA_LENGTH,
    MAX_PACKET_LENGTH,
    DecoderStateError,
    IncompletePacketError,
    PacketDecodeError,
    PacketType,
    PacketValidationError,
    SpacePacket,
    SpacePacketDecoder,
    decode_packets,
)


def make_packet(
    *, apid: int = 1, sequence_count: int = 0, data: bytes = b"abc"
) -> SpacePacket:
    return SpacePacket.create(
        apid=apid,
        packet_type=PacketType.TELEMETRY,
        sequence_count=sequence_count,
        data=data,
    )


def test_empty_stream() -> None:
    decoder = SpacePacketDecoder()

    assert decoder.feed(b"") == []
    assert decoder.buffered_byte_count == 0
    assert decoder.buffered_data == b""
    assert decoder.is_failed is False
    assert decoder.finish() is None
    assert decode_packets(b"") == []


def test_every_packet_split_position() -> None:
    packet = make_packet(data=b"payload")
    encoded = bytes(packet)

    for split in range(len(encoded) + 1):
        decoder = SpacePacketDecoder()
        first_expected = [] if split < len(encoded) else [packet]
        assert decoder.feed(encoded[:split]) == first_expected
        expected = [] if split == len(encoded) else [packet]
        assert decoder.feed(encoded[split:]) == expected
        decoder.finish()


def test_one_byte_at_a_time() -> None:
    packet = make_packet(data=b"payload")
    decoder = SpacePacketDecoder()
    decoded: list[SpacePacket] = []

    for value in bytes(packet):
        decoded.extend(decoder.feed(bytes([value])))

    assert decoded == [packet]
    decoder.finish()


def test_multiple_packets_in_one_chunk() -> None:
    packets = [
        make_packet(apid=1, sequence_count=3, data=b"one"),
        make_packet(apid=2, sequence_count=4, data=b"two"),
        make_packet(apid=1, sequence_count=5, data=b"three"),
    ]
    encoded = b"".join(bytes(packet) for packet in packets)

    assert decode_packets(encoded) == packets


def test_multiple_packets_across_irregular_chunks() -> None:
    packets = [
        make_packet(sequence_count=index, data=bytes([index + 1]) * 5)
        for index in range(4)
    ]
    encoded = b"".join(bytes(packet) for packet in packets)
    decoder = SpacePacketDecoder()
    decoded: list[SpacePacket] = []

    offsets = (1, 8, 11, 23, len(encoded))
    start = 0
    for end in offsets:
        decoded.extend(decoder.feed(encoded[start:end]))
        start = end

    assert decoded == packets
    decoder.finish()


@pytest.mark.parametrize("trailing_size", [1, 5, 6, 7])
def test_complete_packet_followed_by_partial_packet(trailing_size: int) -> None:
    first = make_packet(sequence_count=1)
    second = make_packet(sequence_count=2, data=b"longer")
    decoder = SpacePacketDecoder()

    assert decoder.feed(bytes(first) + bytes(second)[:trailing_size]) == [first]
    assert decoder.buffered_data == bytes(second)[:trailing_size]


def test_finish_reports_partial_header() -> None:
    decoder = SpacePacketDecoder()
    decoder.feed(b"\x00\x01")

    with pytest.raises(IncompletePacketError) as caught:
        decoder.finish()

    assert caught.value.buffered_byte_count == 2
    assert caught.value.expected_packet_length is None
    assert decoder.is_failed is False


def test_finish_reports_partial_packet_and_allows_more_data() -> None:
    packet = make_packet(data=b"payload")
    decoder = SpacePacketDecoder()
    decoder.feed(bytes(packet)[:-1])

    with pytest.raises(IncompletePacketError) as caught:
        decoder.finish()

    assert caught.value.buffered_byte_count == len(packet) - 1
    assert caught.value.expected_packet_length == len(packet)
    assert decoder.feed(bytes(packet)[-1:]) == [packet]
    decoder.finish()


def test_strict_batch_decode_rejects_trailing_data() -> None:
    packet = make_packet()

    with pytest.raises(IncompletePacketError):
        decode_packets(bytes(packet) + b"\x00")


def test_bytes_like_inputs_are_copied() -> None:
    packet = make_packet()
    source = bytearray(bytes(packet)[:-1])
    decoder = SpacePacketDecoder()
    decoder.feed(memoryview(source))

    snapshot = decoder.buffered_data
    source[0] ^= 0xFF

    assert decoder.buffered_data == snapshot
    assert decode_packets(bytearray(bytes(packet))) == [packet]


def test_invalid_input_does_not_poison_or_change_decoder() -> None:
    decoder = SpacePacketDecoder()
    decoder.feed(b"\x00")
    original = decoder.buffered_data

    with pytest.raises(PacketDecodeError):
        decoder.feed(object())  # type: ignore[arg-type]

    released = memoryview(b"x")
    released.release()
    with pytest.raises(PacketDecodeError):
        decoder.feed(released)

    assert decoder.buffered_data == original
    assert decoder.is_failed is False


def test_maximum_packet_size() -> None:
    packet = make_packet(data=b"x" * MAX_PACKET_DATA_LENGTH)

    assert len(packet) == MAX_PACKET_LENGTH
    assert decode_packets(bytes(packet)) == [packet]


def test_custom_maximum_boundary() -> None:
    packet = make_packet(data=b"1234")
    decoder = SpacePacketDecoder(max_packet_length=len(packet))

    assert decoder.max_packet_length == len(packet)
    assert decoder.feed(bytes(packet)) == [packet]


def test_declared_length_above_custom_maximum_fails() -> None:
    packet = make_packet(data=b"12345")
    decoder = SpacePacketDecoder(max_packet_length=len(packet) - 1)

    with pytest.raises(PacketDecodeError, match="exceeds configured maximum"):
        decoder.feed(bytes(packet))

    assert decoder.is_failed is True
    assert decoder.buffered_data == bytes(packet)


@pytest.mark.parametrize("value", [True, 6, MAX_PACKET_LENGTH + 1, 7.0])
def test_invalid_max_packet_length(value: object) -> None:
    with pytest.raises(PacketValidationError, match="max_packet_length"):
        SpacePacketDecoder(max_packet_length=value)  # type: ignore[arg-type]


def test_invalid_header_fails_until_reset() -> None:
    invalid_header = bytes.fromhex("2000c0000000")
    decoder = SpacePacketDecoder()

    with pytest.raises(PacketDecodeError, match="invalid primary header"):
        decoder.feed(invalid_header)

    assert decoder.is_failed is True
    assert decoder.buffered_data == invalid_header
    with pytest.raises(DecoderStateError, match="reset"):
        decoder.feed(b"")
    with pytest.raises(DecoderStateError, match="reset"):
        decoder.finish()


def test_failure_is_atomic_when_valid_packet_precedes_corruption() -> None:
    packet = make_packet()
    invalid_header = bytes.fromhex("2000c0000000")
    decoder = SpacePacketDecoder()

    with pytest.raises(PacketDecodeError):
        decoder.feed(bytes(packet) + invalid_header)

    assert decoder.buffered_data == bytes(packet) + invalid_header


def test_reset_clears_healthy_incomplete_and_failed_states() -> None:
    decoder = SpacePacketDecoder()
    decoder.reset()
    decoder.feed(b"\x00")
    decoder.reset()
    assert decoder.buffered_data == b""

    with pytest.raises(PacketDecodeError):
        decoder.feed(bytes.fromhex("2000c0000000"))
    decoder.reset()

    packet = make_packet()
    assert decoder.is_failed is False
    assert decoder.feed(bytes(packet)) == [packet]
