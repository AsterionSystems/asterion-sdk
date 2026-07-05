import pytest
from asterion.ccsds import (
    MAX_PACKET_DATA_LENGTH,
    MAX_SEQUENCE_COUNT,
    PacketType,
    PacketValidationError,
    ReassembledPacketData,
    ReassemblyError,
    SequenceCounter,
    SequenceFlags,
    SpacePacket,
    SpacePacketReassembler,
)


def make_segment(
    *,
    apid: int = 1,
    packet_type: PacketType = PacketType.TELEMETRY,
    sequence_count: int,
    sequence_flags: SequenceFlags,
    data: bytes = b"x",
    secondary_header_flag: bool = False,
) -> SpacePacket:
    return SpacePacket.create(
        apid=apid,
        packet_type=packet_type,
        sequence_count=sequence_count,
        sequence_flags=sequence_flags,
        secondary_header_flag=secondary_header_flag,
        data=data,
    )


def test_create_packets_returns_unsegmented_packet_when_data_fits() -> None:
    counter = SequenceCounter(initial_value=7)

    packets = counter.create_packets(
        apid=1,
        packet_type=PacketType.TELEMETRY,
        data=b"abc",
        max_data_length=3,
    )

    assert len(packets) == 1
    assert packets[0].header.sequence_flags is SequenceFlags.UNSEGMENTED
    assert packets[0].header.sequence_count == 7
    assert packets[0].data == b"abc"
    assert counter.peek(apid=1) == 8


def test_create_packets_assigns_two_segment_flags() -> None:
    counter = SequenceCounter()

    packets = counter.create_packets(
        apid=1,
        packet_type=PacketType.TELEMETRY,
        data=b"abcdef",
        max_data_length=3,
    )

    assert [packet.header.sequence_flags for packet in packets] == [
        SequenceFlags.FIRST_SEGMENT,
        SequenceFlags.LAST_SEGMENT,
    ]
    assert [packet.data for packet in packets] == [b"abc", b"def"]
    assert [packet.header.sequence_count for packet in packets] == [0, 1]


def test_create_packets_assigns_continuation_segments() -> None:
    counter = SequenceCounter()

    packets = counter.create_packets(
        apid=1,
        packet_type=PacketType.TELECOMMAND,
        data=b"abcdefg",
        max_data_length=2,
    )

    assert [packet.header.sequence_flags for packet in packets] == [
        SequenceFlags.FIRST_SEGMENT,
        SequenceFlags.CONTINUATION,
        SequenceFlags.CONTINUATION,
        SequenceFlags.LAST_SEGMENT,
    ]
    assert b"".join(packet.data for packet in packets) == b"abcdefg"
    assert [packet.header.sequence_count for packet in packets] == [0, 1, 2, 3]


def test_create_packets_supports_one_byte_segments_and_wraparound() -> None:
    counter = SequenceCounter(initial_value=MAX_SEQUENCE_COUNT)

    packets = counter.create_packets(
        apid=1,
        packet_type=PacketType.TELEMETRY,
        data=b"abc",
        max_data_length=1,
    )

    assert [packet.header.sequence_count for packet in packets] == [
        MAX_SEQUENCE_COUNT,
        0,
        1,
    ]
    assert counter.peek(apid=1) == 2


def test_create_packets_accepts_maximum_segment_size() -> None:
    data = b"x" * MAX_PACKET_DATA_LENGTH

    packets = SequenceCounter().create_packets(
        apid=1,
        packet_type=PacketType.TELEMETRY,
        data=data,
        max_data_length=MAX_PACKET_DATA_LENGTH,
    )

    assert len(packets) == 1
    assert packets[0].data == data


@pytest.mark.parametrize("value", [0, MAX_PACKET_DATA_LENGTH + 1, True, 1.5])
def test_create_packets_rejects_invalid_maximum(value: object) -> None:
    with pytest.raises(PacketValidationError, match="max_data_length"):
        SequenceCounter().create_packets(
            apid=1,
            packet_type=PacketType.TELEMETRY,
            data=b"abc",
            max_data_length=value,  # type: ignore[arg-type]
        )


def test_failed_segmentation_does_not_advance_counter() -> None:
    counter = SequenceCounter(initial_value=5)

    with pytest.raises(PacketValidationError):
        counter.create_packets(
            apid=1,
            packet_type=PacketType.TELEMETRY,
            data=b"abc",
            max_data_length=1,
            version=1,
        )
    with pytest.raises(PacketValidationError, match="secondary_header_flag"):
        counter.create_packets(
            apid=1,
            packet_type=PacketType.TELEMETRY,
            data=b"abc",
            secondary_header_flag=True,
        )
    with pytest.raises(PacketValidationError, match="at least one byte"):
        counter.create_packets(
            apid=1,
            packet_type=PacketType.TELEMETRY,
            data=b"",
        )

    assert counter.peek(apid=1) == 5


def test_segmentation_normalizes_bytes_like_data() -> None:
    source = bytearray(b"abcdef")
    packets = SequenceCounter().create_packets(
        apid=1,
        packet_type=PacketType.TELEMETRY,
        data=memoryview(source),
        max_data_length=3,
    )

    source[:] = b"xxxxxx"

    assert b"".join(packet.data for packet in packets) == b"abcdef"


@pytest.mark.parametrize("length", [1, 2, 7, 65, 1000])
def test_segmentation_and_reassembly_round_trip(length: int) -> None:
    data = bytes(index % 256 for index in range(length))
    packets = SequenceCounter().create_packets(
        apid=42,
        packet_type=PacketType.TELEMETRY,
        data=data,
        max_data_length=7,
    )
    reassembler = SpacePacketReassembler(max_assembly_length=length)

    results = [result for packet in packets if (result := reassembler.push(packet))]

    assert len(results) == 1
    assert results[0].data == data
    assert results[0].apid == 42
    assert results[0].segment_count == len(packets)


def test_unsegmented_packet_returns_immediate_result() -> None:
    packet = make_segment(
        sequence_count=9,
        sequence_flags=SequenceFlags.UNSEGMENTED,
        data=b"complete",
    )
    reassembler = SpacePacketReassembler(max_assembly_length=100)

    result = reassembler.push(packet)

    assert result == ReassembledPacketData(
        data=b"complete",
        apid=1,
        packet_type=PacketType.TELEMETRY,
        first_sequence_count=9,
        last_sequence_count=9,
        segment_count=1,
    )


def test_interleaved_apid_assemblies() -> None:
    reassembler = SpacePacketReassembler(max_assembly_length=100)

    assert (
        reassembler.push(
            make_segment(
                apid=1, sequence_count=0, sequence_flags=SequenceFlags.FIRST_SEGMENT
            )
        )
        is None
    )
    assert (
        reassembler.push(
            make_segment(
                apid=2,
                sequence_count=5,
                sequence_flags=SequenceFlags.FIRST_SEGMENT,
                data=b"a",
            )
        )
        is None
    )
    first = reassembler.push(
        make_segment(
            apid=1,
            sequence_count=1,
            sequence_flags=SequenceFlags.LAST_SEGMENT,
            data=b"b",
        )
    )
    second = reassembler.push(
        make_segment(
            apid=2,
            sequence_count=6,
            sequence_flags=SequenceFlags.LAST_SEGMENT,
            data=b"b",
        )
    )

    assert first is not None and first.data == b"xb"
    assert second is not None and second.data == b"ab"
    assert reassembler.active_assembly_count == 0


def test_reassembly_supports_sequence_wraparound() -> None:
    reassembler = SpacePacketReassembler(max_assembly_length=10)
    reassembler.push(
        make_segment(
            sequence_count=MAX_SEQUENCE_COUNT,
            sequence_flags=SequenceFlags.FIRST_SEGMENT,
        )
    )

    result = reassembler.push(
        make_segment(sequence_count=0, sequence_flags=SequenceFlags.LAST_SEGMENT)
    )

    assert result is not None
    assert result.first_sequence_count == MAX_SEQUENCE_COUNT
    assert result.last_sequence_count == 0


@pytest.mark.parametrize(
    "flags", [SequenceFlags.CONTINUATION, SequenceFlags.LAST_SEGMENT]
)
def test_segment_without_first_is_rejected(flags: SequenceFlags) -> None:
    with pytest.raises(ReassemblyError, match="without a first"):
        SpacePacketReassembler(max_assembly_length=10).push(
            make_segment(sequence_count=1, sequence_flags=flags)
        )


def test_duplicate_first_discards_only_affected_stream() -> None:
    reassembler = SpacePacketReassembler(max_assembly_length=10)
    reassembler.push(
        make_segment(
            apid=1, sequence_count=0, sequence_flags=SequenceFlags.FIRST_SEGMENT
        )
    )
    reassembler.push(
        make_segment(
            apid=2, sequence_count=0, sequence_flags=SequenceFlags.FIRST_SEGMENT
        )
    )

    with pytest.raises(ReassemblyError, match="first segment interrupted") as caught:
        reassembler.push(
            make_segment(
                apid=1,
                sequence_count=1,
                sequence_flags=SequenceFlags.FIRST_SEGMENT,
            )
        )

    assert caught.value.apid == 1
    assert reassembler.active_assembly_count == 1


def test_wrong_sequence_or_packet_type_discards_assembly() -> None:
    reassembler = SpacePacketReassembler(max_assembly_length=10)
    reassembler.push(
        make_segment(sequence_count=0, sequence_flags=SequenceFlags.FIRST_SEGMENT)
    )
    with pytest.raises(ReassemblyError, match="expected sequence"):
        reassembler.push(
            make_segment(sequence_count=2, sequence_flags=SequenceFlags.CONTINUATION)
        )
    assert reassembler.active_assembly_count == 0

    reassembler.push(
        make_segment(sequence_count=3, sequence_flags=SequenceFlags.FIRST_SEGMENT)
    )
    with pytest.raises(ReassemblyError, match="packet type changed"):
        reassembler.push(
            make_segment(
                packet_type=PacketType.TELECOMMAND,
                sequence_count=4,
                sequence_flags=SequenceFlags.LAST_SEGMENT,
            )
        )
    assert reassembler.active_assembly_count == 0


def test_unsegmented_packet_interrupts_assembly() -> None:
    reassembler = SpacePacketReassembler(max_assembly_length=10)
    reassembler.push(
        make_segment(sequence_count=0, sequence_flags=SequenceFlags.FIRST_SEGMENT)
    )

    with pytest.raises(ReassemblyError, match="unsegmented packet interrupted"):
        reassembler.push(
            make_segment(sequence_count=1, sequence_flags=SequenceFlags.UNSEGMENTED)
        )

    assert reassembler.active_assembly_count == 0


def test_secondary_header_packet_is_rejected_and_discards_assembly() -> None:
    reassembler = SpacePacketReassembler(max_assembly_length=10)
    reassembler.push(
        make_segment(sequence_count=0, sequence_flags=SequenceFlags.FIRST_SEGMENT)
    )

    with pytest.raises(ReassemblyError, match="secondary-header"):
        reassembler.push(
            make_segment(
                sequence_count=1,
                sequence_flags=SequenceFlags.LAST_SEGMENT,
                secondary_header_flag=True,
            )
        )

    assert reassembler.active_assembly_count == 0


def test_assembly_length_limit_discards_partial_assembly() -> None:
    reassembler = SpacePacketReassembler(max_assembly_length=3)
    reassembler.push(
        make_segment(
            sequence_count=0,
            sequence_flags=SequenceFlags.FIRST_SEGMENT,
            data=b"ab",
        )
    )

    with pytest.raises(ReassemblyError, match="exceeds configured maximum"):
        reassembler.push(
            make_segment(
                sequence_count=1,
                sequence_flags=SequenceFlags.LAST_SEGMENT,
                data=b"cd",
            )
        )

    assert reassembler.active_assembly_count == 0


def test_active_assembly_limit_preserves_existing_assembly() -> None:
    reassembler = SpacePacketReassembler(
        max_assembly_length=10, max_active_assemblies=1
    )
    reassembler.push(
        make_segment(
            apid=1, sequence_count=0, sequence_flags=SequenceFlags.FIRST_SEGMENT
        )
    )

    with pytest.raises(ReassemblyError, match="active assembly limit"):
        reassembler.push(
            make_segment(
                apid=2,
                sequence_count=0,
                sequence_flags=SequenceFlags.FIRST_SEGMENT,
            )
        )

    assert reassembler.active_assembly_count == 1


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_reassembler_limits(value: object) -> None:
    with pytest.raises(PacketValidationError):
        SpacePacketReassembler(max_assembly_length=value)  # type: ignore[arg-type]
    with pytest.raises(PacketValidationError):
        SpacePacketReassembler(
            max_assembly_length=10,
            max_active_assemblies=value,  # type: ignore[arg-type]
        )


def test_reassembler_properties_and_resets() -> None:
    reassembler = SpacePacketReassembler(
        max_assembly_length=100, max_active_assemblies=2
    )
    assert reassembler.max_assembly_length == 100
    assert reassembler.max_active_assemblies == 2

    for apid in (1, 2):
        reassembler.push(
            make_segment(
                apid=apid,
                sequence_count=0,
                sequence_flags=SequenceFlags.FIRST_SEGMENT,
            )
        )
    reassembler.reset_stream(apid=1)
    assert reassembler.active_assembly_count == 1
    reassembler.reset()
    assert reassembler.active_assembly_count == 0


def test_reassembler_rejects_invalid_packet_object() -> None:
    with pytest.raises(PacketValidationError, match="SpacePacket"):
        SpacePacketReassembler(max_assembly_length=10).push(object())  # type: ignore[arg-type]
