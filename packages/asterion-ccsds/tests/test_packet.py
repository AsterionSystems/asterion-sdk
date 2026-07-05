import pytest
from asterion.ccsds import (
    IDLE_APID,
    MAX_APID,
    MAX_PACKET_DATA_LENGTH,
    MAX_PACKET_LENGTH,
    MAX_SEQUENCE_COUNT,
    PRIMARY_HEADER_SIZE,
    SPACE_PACKET_VERSION,
    PacketDecodeError,
    PacketType,
    PacketValidationError,
    SequenceFlags,
    SpacePacket,
    SpacePacketHeader,
)


def make_header(**overrides: object) -> SpacePacketHeader:
    values: dict[str, object] = {
        "version": 0,
        "packet_type": PacketType.TELEMETRY,
        "secondary_header_flag": False,
        "apid": 42,
        "sequence_flags": SequenceFlags.UNSEGMENTED,
        "sequence_count": 7,
        "packet_data_length": 2,
    }
    values.update(overrides)
    return SpacePacketHeader(**values)  # type: ignore[arg-type]


def test_header_encode_decode_round_trip() -> None:
    header = make_header(secondary_header_flag=True)

    assert len(header.to_bytes()) == 6
    assert SpacePacketHeader.from_bytes(header.to_bytes()) == header


def test_full_packet_encode_decode_round_trip() -> None:
    packet = SpacePacket(header=make_header(), data=b"abc")

    assert SpacePacket.from_bytes(packet.to_bytes()) == packet


def test_invalid_apid() -> None:
    with pytest.raises(PacketValidationError, match="apid"):
        make_header(apid=2_048)


def test_invalid_sequence_count() -> None:
    with pytest.raises(PacketValidationError, match="sequence_count"):
        make_header(sequence_count=16_384)


def test_invalid_short_header_decode() -> None:
    with pytest.raises(PacketDecodeError, match="6 bytes"):
        SpacePacketHeader.from_bytes(b"\x00" * 5)


def test_invalid_packet_length_mismatch() -> None:
    encoded = make_header(packet_data_length=4).to_bytes() + b"abc"

    with pytest.raises(PacketDecodeError, match="length mismatch"):
        SpacePacket.from_bytes(encoded)


def test_telemetry_packet_example() -> None:
    packet = SpacePacket(header=make_header(apid=1), data=b"abc")

    assert packet.to_bytes() == bytes.fromhex("0001c0070002616263")
    assert (
        SpacePacket.from_bytes(packet.to_bytes()).header.packet_type
        is PacketType.TELEMETRY
    )


def test_telecommand_packet_example() -> None:
    header = make_header(
        packet_type=PacketType.TELECOMMAND,
        secondary_header_flag=True,
        apid=1,
        sequence_count=8,
    )
    packet = SpacePacket(header=header, data=b"cmd")

    assert packet.to_bytes() == bytes.fromhex("1801c0080002636d64")
    assert (
        SpacePacket.from_bytes(packet.to_bytes()).header.packet_type
        is PacketType.TELECOMMAND
    )


def test_empty_data_rejection() -> None:
    with pytest.raises(PacketValidationError, match="at least one byte"):
        SpacePacket(header=make_header(packet_data_length=0), data=b"")


def test_create_builds_header_and_computes_packet_length() -> None:
    packet = SpacePacket.create(
        apid=42,
        packet_type=PacketType.TELEMETRY,
        sequence_count=15,
        data=b"Hello",
    )

    assert packet.data == b"Hello"
    assert packet.header == SpacePacketHeader(
        version=0,
        packet_type=PacketType.TELEMETRY,
        secondary_header_flag=False,
        apid=42,
        sequence_flags=SequenceFlags.UNSEGMENTED,
        sequence_count=15,
        packet_data_length=4,
    )


def test_create_supports_non_default_header_fields() -> None:
    packet = SpacePacket.create(
        apid=7,
        packet_type=PacketType.TELECOMMAND,
        sequence_count=2,
        data=b"command",
        secondary_header_flag=True,
        sequence_flags=SequenceFlags.FIRST_SEGMENT,
    )

    assert packet.header.secondary_header_flag is True
    assert packet.header.sequence_flags is SequenceFlags.FIRST_SEGMENT


def test_create_rejects_empty_data() -> None:
    with pytest.raises(PacketValidationError, match="packet_data_length"):
        SpacePacket.create(
            apid=42,
            packet_type=PacketType.TELEMETRY,
            sequence_count=15,
            data=b"",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", 1),
        ("packet_type", 2),
        ("secondary_header_flag", 1),
        ("sequence_flags", 4),
        ("packet_data_length", 65_536),
    ],
)
def test_header_rejects_invalid_fields(field: str, value: object) -> None:
    with pytest.raises(PacketValidationError):
        make_header(**{field: value})


def test_header_decode_rejects_nonzero_version() -> None:
    encoded = bytes.fromhex("2000c0000000")

    with pytest.raises(PacketDecodeError, match="invalid primary header"):
        SpacePacketHeader.from_bytes(encoded)


def test_packet_rejects_invalid_model_values() -> None:
    with pytest.raises(PacketValidationError, match="header"):
        SpacePacket(header=object(), data=b"a")  # type: ignore[arg-type]
    with pytest.raises(PacketValidationError, match="data must be bytes"):
        SpacePacket(header=make_header(packet_data_length=0), data="a")  # type: ignore[arg-type]
    with pytest.raises(PacketValidationError, match="does not match"):
        SpacePacket(header=make_header(packet_data_length=1), data=b"a")


def test_packet_rejects_data_larger_than_length_field() -> None:
    with pytest.raises(PacketValidationError, match="must not exceed"):
        SpacePacket(
            header=make_header(packet_data_length=65_535),
            data=b"x" * 65_537,
        )


def test_packet_decode_rejects_short_packet() -> None:
    with pytest.raises(PacketDecodeError, match="at least 6"):
        SpacePacket.from_bytes(b"short")


def test_public_protocol_constants() -> None:
    assert SPACE_PACKET_VERSION == 0
    assert PRIMARY_HEADER_SIZE == 6
    assert MAX_APID == IDLE_APID == 2_047
    assert MAX_SEQUENCE_COUNT == 16_383
    assert MAX_PACKET_DATA_LENGTH == 65_536
    assert MAX_PACKET_LENGTH == 65_542


def test_header_convenience_api() -> None:
    header = make_header(apid=IDLE_APID)

    assert header.data_length == 3
    assert header.total_length == 9
    assert header.is_idle is True
    assert bytes(header) == header.to_bytes()


def test_packet_convenience_api() -> None:
    packet = SpacePacket(header=make_header(), data=b"abc")

    assert packet.data_length == 3
    assert packet.total_length == 9
    assert packet.is_idle is False
    assert bytes(packet) == packet.to_bytes()
    assert len(packet) == packet.total_length == len(bytes(packet))


def test_packet_normalizes_bytearray_and_defensively_copies() -> None:
    source = bytearray(b"abc")
    packet = SpacePacket(header=make_header(), data=source)

    source[0] = ord("z")

    assert packet.data == b"abc"
    assert isinstance(packet.data, bytes)


def test_create_normalizes_memoryview_and_defensively_copies() -> None:
    source = bytearray(b"abc")
    packet = SpacePacket.create(
        apid=42,
        packet_type=PacketType.TELEMETRY,
        sequence_count=1,
        data=memoryview(source),
    )

    source[0] = ord("z")

    assert packet.data == b"abc"
    assert isinstance(packet.data, bytes)


def test_decode_accepts_bytearray_and_memoryview() -> None:
    encoded = SpacePacket(header=make_header(), data=b"abc").to_bytes()

    assert SpacePacketHeader.from_bytes(bytearray(encoded[:6])) == make_header()
    assert SpacePacket.from_bytes(memoryview(encoded)).data == b"abc"


def test_minimum_packet_golden_vector() -> None:
    header = make_header(
        apid=0,
        sequence_flags=SequenceFlags.CONTINUATION,
        sequence_count=0,
        packet_data_length=0,
    )
    packet = SpacePacket(header=header, data=b"\x00")

    assert packet.to_bytes() == bytes.fromhex("00000000000000")


def test_maximum_header_fields_golden_vector() -> None:
    header = SpacePacketHeader(
        version=SPACE_PACKET_VERSION,
        packet_type=PacketType.TELECOMMAND,
        secondary_header_flag=True,
        apid=MAX_APID,
        sequence_flags=SequenceFlags.UNSEGMENTED,
        sequence_count=MAX_SEQUENCE_COUNT,
        packet_data_length=MAX_PACKET_DATA_LENGTH - 1,
    )

    assert header.to_bytes() == bytes.fromhex("1fffffffffff")
    assert header.data_length == MAX_PACKET_DATA_LENGTH
    assert header.total_length == MAX_PACKET_LENGTH


def test_maximum_packet_size() -> None:
    packet = SpacePacket.create(
        apid=MAX_APID,
        packet_type=PacketType.TELEMETRY,
        sequence_count=MAX_SEQUENCE_COUNT,
        data=b"x" * MAX_PACKET_DATA_LENGTH,
    )

    assert len(packet) == MAX_PACKET_LENGTH
    assert packet.is_idle is True


def test_construction_rejects_unsupported_or_released_buffers() -> None:
    with pytest.raises(PacketValidationError, match="bytes, bytearray, or memoryview"):
        SpacePacket(header=make_header(), data=object())  # type: ignore[arg-type]

    released = memoryview(b"abc")
    released.release()
    with pytest.raises(PacketValidationError, match="not a usable byte buffer"):
        SpacePacket(header=make_header(), data=released)


def test_decoding_rejects_unsupported_or_released_buffers() -> None:
    with pytest.raises(PacketDecodeError, match="bytes, bytearray, or memoryview"):
        SpacePacket.from_bytes(object())  # type: ignore[arg-type]
    with pytest.raises(PacketDecodeError, match="bytes, bytearray, or memoryview"):
        SpacePacketHeader.from_bytes(object())  # type: ignore[arg-type]

    released = memoryview(b"abc")
    released.release()
    with pytest.raises(PacketDecodeError, match="not a usable byte buffer"):
        SpacePacket.from_bytes(released)
    with pytest.raises(PacketDecodeError, match="not a usable byte buffer"):
        SpacePacketHeader.from_bytes(released)
