import pytest
from asterion.ccsds import (
    IDLE_APID,
    MAX_SEQUENCE_COUNT,
    PacketType,
    PacketValidationError,
    SequenceCounter,
    SequenceFlags,
    SpacePacket,
)


def test_default_and_custom_initial_values() -> None:
    default = SequenceCounter()
    custom = SequenceCounter(initial_value=42)

    assert default.initial_value == 0
    assert default.peek(apid=1) == 0
    assert custom.initial_value == 42
    assert custom.peek(apid=1) == 42


def test_next_returns_then_advances() -> None:
    counter = SequenceCounter()

    assert counter.next(apid=1) == 0
    assert counter.next(apid=1) == 1
    assert counter.peek(apid=1) == 2


def test_sequence_count_wraps_to_zero() -> None:
    counter = SequenceCounter(initial_value=MAX_SEQUENCE_COUNT)

    assert counter.next(apid=1) == MAX_SEQUENCE_COUNT
    assert counter.next(apid=1) == 0


def test_apids_have_independent_counts() -> None:
    counter = SequenceCounter()

    counter.next(apid=1)

    assert counter.peek(apid=1) == 1
    assert counter.peek(apid=2) == 0


def test_packet_types_share_an_apid_count() -> None:
    counter = SequenceCounter()

    telemetry = counter.create_packet(
        apid=1, packet_type=PacketType.TELEMETRY, data=b"tm"
    )
    telecommand = counter.create_packet(
        apid=1, packet_type=PacketType.TELECOMMAND, data=b"tc"
    )

    assert telemetry.header.sequence_count == 0
    assert telecommand.header.sequence_count == 1
    assert counter.peek(apid=1) == 2


def test_peek_does_not_advance_stream() -> None:
    counter = SequenceCounter(initial_value=7)

    assert counter.peek(apid=1) == 7
    assert counter.peek(apid=1) == 7
    assert counter.next(apid=1) == 7


def test_set_and_reset_stream() -> None:
    counter = SequenceCounter(initial_value=3)
    counter.set_next(apid=1, value=100)

    assert counter.next(apid=1) == 100
    counter.reset_stream(apid=1)
    assert counter.peek(apid=1) == 3


def test_reset_all_streams() -> None:
    counter = SequenceCounter(initial_value=3)
    counter.next(apid=1)
    counter.next(apid=2)

    counter.reset()

    assert counter.peek(apid=1) == 3
    assert counter.peek(apid=2) == 3


def test_integer_packet_type_is_normalized_during_creation() -> None:
    counter = SequenceCounter()

    packet = counter.create_packet(
        apid=1,
        packet_type=0,  # type: ignore[arg-type]
        data=b"data",
    )

    assert packet.header.packet_type is PacketType.TELEMETRY
    assert counter.peek(apid=1) == 1


@pytest.mark.parametrize("initial_value", [-1, MAX_SEQUENCE_COUNT + 1, True, 1.5])
def test_invalid_initial_value(initial_value: object) -> None:
    with pytest.raises(PacketValidationError, match="initial_value"):
        SequenceCounter(initial_value=initial_value)  # type: ignore[arg-type]


@pytest.mark.parametrize("apid", [-1, 2_048, True, 1.5])
def test_invalid_stream_key(apid: object) -> None:
    counter = SequenceCounter()

    with pytest.raises(PacketValidationError):
        counter.peek(apid=apid)  # type: ignore[arg-type]


@pytest.mark.parametrize("packet_type", [-1, 2, True, 1.5])
def test_invalid_packet_type(packet_type: object) -> None:
    counter = SequenceCounter()

    with pytest.raises(PacketValidationError, match="packet_type"):
        counter.create_packet(
            apid=1,
            packet_type=packet_type,  # type: ignore[arg-type]
            data=b"data",
        )


@pytest.mark.parametrize("value", [-1, MAX_SEQUENCE_COUNT + 1, True, 1.5])
def test_invalid_set_value(value: object) -> None:
    counter = SequenceCounter()

    with pytest.raises(PacketValidationError, match="value"):
        counter.set_next(
            apid=1,
            value=value,  # type: ignore[arg-type]
        )


def test_create_packet_assigns_and_advances_sequence() -> None:
    counter = SequenceCounter(initial_value=15)

    first = counter.create_packet(
        apid=42,
        packet_type=PacketType.TELEMETRY,
        data=b"first",
    )
    second = counter.create_packet(
        apid=42,
        packet_type=PacketType.TELEMETRY,
        data=b"second",
    )

    assert first.header.sequence_count == 15
    assert second.header.sequence_count == 16
    assert SpacePacket.from_bytes(bytes(first)) == first


def test_create_packet_supports_optional_header_fields() -> None:
    counter = SequenceCounter()
    packet = counter.create_packet(
        apid=42,
        packet_type=PacketType.TELECOMMAND,
        data=b"command",
        secondary_header_flag=True,
        sequence_flags=SequenceFlags.FIRST_SEGMENT,
    )

    assert packet.header.secondary_header_flag is True
    assert packet.header.sequence_flags is SequenceFlags.FIRST_SEGMENT


def test_create_packet_normalizes_bytes_like_data() -> None:
    counter = SequenceCounter()
    source = bytearray(b"payload")
    packet = counter.create_packet(
        apid=42,
        packet_type=PacketType.TELEMETRY,
        data=memoryview(source),
    )

    source[0] = ord("x")

    assert packet.data == b"payload"


def test_failed_packet_creation_does_not_advance() -> None:
    counter = SequenceCounter(initial_value=9)

    with pytest.raises(PacketValidationError):
        counter.create_packet(
            apid=42,
            packet_type=PacketType.TELEMETRY,
            data=b"",
        )

    assert counter.peek(apid=42) == 9


def test_idle_apid_uses_normal_counter_behavior() -> None:
    counter = SequenceCounter()
    packet = counter.create_packet(
        apid=IDLE_APID,
        packet_type=PacketType.TELEMETRY,
        data=b"idle",
    )

    assert packet.is_idle is True
    assert packet.header.sequence_count == 0
    assert counter.peek(apid=IDLE_APID) == 1
