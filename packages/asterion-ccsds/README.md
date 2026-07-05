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

APID 2047 is reserved for idle packets. The package exports `IDLE_APID` and
provides `packet.is_idle` and `packet.header.is_idle` for identification without
imposing mission-specific idle-data rules.

## Current limitations

- Only the CCSDS Space Packet primary header and complete packet framing are
  implemented.
- Secondary headers are treated as opaque packet data.
- ECSS PUS, AIT tooling, checksums, streams, and segmented-packet reassembly are
  not implemented.
