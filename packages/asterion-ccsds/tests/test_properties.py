from collections.abc import Sequence
from contextlib import suppress

from asterion.ccsds import (
    MAX_APID,
    MAX_SEQUENCE_COUNT,
    PacketDecodeError,
    PacketType,
    SequenceCounter,
    SequenceFlags,
    SpacePacket,
    SpacePacketDecoder,
    SpacePacketHeader,
    SpacePacketReassembler,
)
from hypothesis import given, settings
from hypothesis import strategies as st

property_settings = settings(max_examples=100, derandomize=True, deadline=None)


@property_settings
@given(
    packet_type=st.sampled_from(PacketType),
    secondary_header_flag=st.booleans(),
    apid=st.integers(min_value=0, max_value=MAX_APID),
    sequence_flags=st.sampled_from(SequenceFlags),
    sequence_count=st.integers(min_value=0, max_value=MAX_SEQUENCE_COUNT),
    packet_data_length=st.integers(min_value=0, max_value=65_535),
)
def test_all_valid_header_fields_round_trip(
    packet_type: PacketType,
    secondary_header_flag: bool,
    apid: int,
    sequence_flags: SequenceFlags,
    sequence_count: int,
    packet_data_length: int,
) -> None:
    header = SpacePacketHeader(
        version=0,
        packet_type=packet_type,
        secondary_header_flag=secondary_header_flag,
        apid=apid,
        sequence_flags=sequence_flags,
        sequence_count=sequence_count,
        packet_data_length=packet_data_length,
    )

    assert SpacePacketHeader.from_bytes(bytes(header)) == header


@property_settings
@given(
    data=st.binary(min_size=1, max_size=4096),
    apid=st.integers(min_value=0, max_value=MAX_APID),
    sequence_count=st.integers(min_value=0, max_value=MAX_SEQUENCE_COUNT),
    packet_type=st.sampled_from(PacketType),
)
def test_legal_packets_round_trip_and_copy_mutable_input(
    data: bytes,
    apid: int,
    sequence_count: int,
    packet_type: PacketType,
) -> None:
    source = bytearray(data)
    packet = SpacePacket.create(
        apid=apid,
        packet_type=packet_type,
        sequence_count=sequence_count,
        data=source,
    )
    source[:] = b"\x00" * len(source)

    assert packet.data == data
    assert SpacePacket.from_bytes(bytes(packet)) == packet


@st.composite
def packet_sequences(draw: st.DrawFn) -> list[SpacePacket]:
    payloads = draw(st.lists(st.binary(min_size=1, max_size=64), max_size=10))
    return [
        SpacePacket.create(
            apid=index % (MAX_APID + 1),
            packet_type=PacketType.TELEMETRY,
            sequence_count=index,
            data=payload,
        )
        for index, payload in enumerate(payloads)
    ]


def _chunk_data(data: bytes, sizes: Sequence[int]) -> list[bytes]:
    chunks: list[bytes] = []
    offset = 0
    for size in sizes:
        if offset >= len(data):
            break
        chunks.append(data[offset : offset + size])
        offset += size
    chunks.append(data[offset:])
    return chunks


@property_settings
@given(packets=packet_sequences(), sizes=st.lists(st.integers(1, 50), max_size=30))
def test_stream_decoding_is_invariant_to_chunk_boundaries(
    packets: list[SpacePacket], sizes: list[int]
) -> None:
    encoded = b"".join(bytes(packet) for packet in packets)
    decoder = SpacePacketDecoder()
    decoded: list[SpacePacket] = []

    for chunk in _chunk_data(encoded, sizes):
        decoded.extend(decoder.feed(chunk))

    decoder.finish()
    assert decoded == packets


@property_settings
@given(
    data=st.binary(min_size=1, max_size=1024),
    max_data_length=st.integers(min_value=1, max_value=128),
    initial_value=st.integers(min_value=0, max_value=MAX_SEQUENCE_COUNT),
)
def test_segmentation_reassembly_round_trip(
    data: bytes, max_data_length: int, initial_value: int
) -> None:
    packets = SequenceCounter(initial_value).create_packets(
        apid=42,
        packet_type=PacketType.TELEMETRY,
        data=data,
        max_data_length=max_data_length,
    )
    reassembler = SpacePacketReassembler(max_assembly_length=len(data))
    result = None

    for packet in packets:
        result = reassembler.push(packet)

    assert result is not None
    assert result.data == data


@property_settings
@given(data=st.binary(max_size=256))
def test_arbitrary_bytes_only_raise_documented_decode_error(data: bytes) -> None:
    with suppress(PacketDecodeError):
        SpacePacket.from_bytes(data)
