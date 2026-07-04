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
    values = {
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
