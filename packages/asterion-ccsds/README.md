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

```python
from asterion.ccsds import PacketType, SequenceFlags, SpacePacket, SpacePacketHeader

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

For the common case, `SpacePacket.create()` derives the header and packet length:

```python
packet = SpacePacket.create(
    apid=42,
    packet_type=PacketType.TELEMETRY,
    sequence_count=15,
    data=b"Hello",
)
encoded = packet.to_bytes()
```

## Decoding

```python
from asterion.ccsds import SpacePacket

packet = SpacePacket.from_bytes(encoded)
print(packet.header.apid, packet.data)
```

## Current limitations

- Only the CCSDS Space Packet primary header and complete packet framing are
  implemented.
- Secondary headers are treated as opaque packet data.
- ECSS PUS, AIT tooling, checksums, streams, and segmented-packet reassembly are
  not implemented.
