import pytest
from asterion.ccsds import (
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
