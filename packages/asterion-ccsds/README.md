# asterion-ccsds

`asterion-ccsds` provides a small, typed implementation of the six-byte CCSDS
Space Packet primary header, together with basic whole-packet encoding and
decoding. It uses network byte order and has no runtime dependencies.

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

APID 2047 is reserved for idle packets. The package exports `IDLE_APID` and
provides `packet.is_idle` and `packet.header.is_idle` for identification without
imposing mission-specific idle-data rules.

## Current limitations

- Only the CCSDS Space Packet primary header and complete packet framing are
  implemented.
- Secondary headers are treated as opaque packet data.
- ECSS PUS, AIT tooling, checksums, transport I/O, and segmented-packet
  reassembly are not implemented.
