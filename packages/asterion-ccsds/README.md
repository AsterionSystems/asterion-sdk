# asterion-ccsds

`asterion-ccsds` provides a small, typed implementation of the six-byte CCSDS
Space Packet primary header, together with basic whole-packet encoding and
decoding. It uses network byte order and has no runtime dependencies.

Additional reference material:

- [API guide](https://github.com/AsterionSystems/asterion-sdk/blob/main/packages/asterion-ccsds/API.md)
- [CCSDS conformance](https://github.com/AsterionSystems/asterion-sdk/blob/main/packages/asterion-ccsds/CONFORMANCE.md)
- [Compatibility policy](https://github.com/AsterionSystems/asterion-sdk/blob/main/packages/asterion-ccsds/COMPATIBILITY.md)
- [Changelog](https://github.com/AsterionSystems/asterion-sdk/blob/main/packages/asterion-ccsds/CHANGELOG.md)

## Installation

Package publication is not configured yet. Once published, installation will be:

```console
pip install asterion-ccsds
```

## Encoding

For most applications, use `SpacePacket.create()`. It computes the encoded packet
length and supplies the standard header defaults:

```python
from asterion.ccsds import PacketType, SpacePacket

packet = SpacePacket.create(
    apid=42,
    packet_type=PacketType.TELEMETRY,
    sequence_count=15,
    data=b"Hello",
)
encoded = packet.to_bytes()
```

`bytes`, `bytearray`, and `memoryview` data are accepted and copied into immutable
`bytes`. Python-native conversion and length operations are also available:

```python
encoded = bytes(packet)
assert len(packet) == packet.total_length == len(encoded)
assert packet.data_length == len(packet.data)
```

Construct the primary header directly when the encoded length field or other
low-level details must be controlled:

```python
from asterion.ccsds import SequenceFlags, SpacePacketHeader

data = b"payload"
header = SpacePacketHeader(
    version=0,
    packet_type=PacketType.TELEMETRY,
    secondary_header_flag=False,
    apid=42,
    sequence_flags=SequenceFlags.UNSEGMENTED,
    sequence_count=1,
    packet_data_length=len(data) - 1,
)
encoded = SpacePacket(header=header, data=data).to_bytes()
```

The header exposes both the encoded `packet_data_length` field and the decoded,
human-friendly lengths:

```python
assert header.data_length == len(data)
assert header.total_length == 6 + len(data)
```

## Decoding

```python
from asterion.ccsds import SpacePacket

packet = SpacePacket.from_bytes(encoded)
print(packet.header.apid, packet.data)
```

## Decoding packet streams

Use `SpacePacketDecoder` when packets arrive in arbitrary chunks. Complete
packets are returned immediately, while a trailing partial packet is retained:

```python
from asterion.ccsds import SpacePacketDecoder

decoder = SpacePacketDecoder()
for chunk in source:
    for packet in decoder.feed(chunk):
        process(packet)

decoder.finish()  # Raises IncompletePacketError if trailing bytes remain.
```

Several complete packets can be decoded from one buffer with the strict helper:

```python
from asterion.ccsds import decode_packets

packets = decode_packets(buffer)
```

The decoder accepts packets up to the CCSDS maximum by default. A smaller
mission-specific limit can be supplied with `max_packet_length`. Invalid headers
and size violations put the decoder into a failed state; its diagnostic
`buffered_data` is preserved until `reset()` is called.

The decoder deliberately does not scan for a new packet after malformed data.
Raw Space Packets have no synchronization marker, so reliable resynchronization
requires an external framing layer.

## Managing sequence counts

`SpacePacket.create()` is deliberately stateless when the application already
owns its sequence counts. For automatic per-stream sequencing, use
`SequenceCounter`:

```python
from asterion.ccsds import PacketType, SequenceCounter

counter = SequenceCounter()
packet = counter.create_packet(
    apid=42,
    packet_type=PacketType.TELEMETRY,
    data=b"payload",
)
```

Streams are identified by APID, as specified by CCSDS 133.0-B-2. Telemetry and
telecommand packets sharing an APID therefore consume the same sequence. The
first packet uses `initial_value` (zero by default), and values wrap from 16383
to zero. Use `peek()`, `set_next()`, `reset_stream()`, or `reset()` when explicit
state control is needed.

Counter instances contain only in-memory state. They are not global, persistent,
or thread-safe; applications sharing an instance across threads must synchronize
access.

## Segmentation and reassembly

Large application data can be divided into Space Packets while preserving the
per-APID sequence count:

```python
packets = counter.create_packets(
    apid=42,
    packet_type=PacketType.TELEMETRY,
    data=large_data,
    max_data_length=1024,
)
```

Data that fits produces one unsegmented packet. Larger data receives the CCSDS
first, continuation, and last sequence flags. Generic segmentation rejects
secondary headers because their content and per-segment construction are
mission-specific.

Reassembly requires an explicit memory bound:

```python
from asterion.ccsds import SpacePacketReassembler

reassembler = SpacePacketReassembler(
    max_assembly_length=16 * 1024 * 1024,
    max_active_assemblies=64,
)
for packet in packets:
    if (result := reassembler.push(packet)) is not None:
        process(result.data)
```

Assemblies are tracked per APID and require continuous modulo-16384 sequence
counts. An invalid segment discards only its APID's partial assembly and raises
`ReassemblyError`; other APIDs remain usable. This behavior is a bounded library
convenience over the sequence flags in CCSDS 133.0-B-2 §4.1.3.4.2, not a
mission-specific secondary-header or PUS reassembly policy.

## Secondary-header codecs

CCSDS defines where a secondary header appears but leaves its format to the
mission. The package provides a typed codec boundary without a global registry or
knowledge of PUS:

```python
packet = create_packet_with_secondary_header(
    apid=42,
    packet_type=PacketType.TELEMETRY,
    sequence_count=9,
    secondary_header=my_header,
    user_data=b"payload",
    codec=my_codec,
)

decoded = decode_packet_data(packet, codec=my_codec)
process(decoded.secondary_header, decoded.user_data)
```

A `SecondaryHeaderCodec[T]` encodes `T` to one or more bytes and decodes it from
a read-only `memoryview`, returning `(header, consumed_byte_count)`. This lets a
future `asterion-pus` package supply its own codec while depending on
`asterion-ccsds`; the CCSDS package never imports PUS or stores global codec
state.

APID 2047 is reserved for idle packets. The package exports `IDLE_APID` and
provides `packet.is_idle` and `packet.header.is_idle` for identification without
imposing mission-specific idle-data rules.

## Current limitations

- Only the CCSDS Space Packet primary header and complete packet framing are
  implemented.
- Secondary headers are treated as opaque packet data.
- ECSS PUS, AIT tooling, checksums, transport I/O, and segmented-packet
  reassembly are not implemented.
