# API guide

The public API is available directly from `asterion.ccsds`.

- `SpacePacketHeader` and `SpacePacket` model and encode complete packets.
- `SpacePacketDecoder` incrementally extracts packets from arbitrary byte chunks.
- `SequenceCounter` manages continuous per-APID sequence counts and segmentation.
- `SpacePacketReassembler` performs bounded, strict segment reassembly.
- `SecondaryHeaderCodec` integrates mission-specific secondary-header formats.

Prefer `SpacePacket.create()` for explicit sequence counts and
`SequenceCounter.create_packet()` when the package should manage them. All models
store immutable bytes, and decoding errors derive from `CcsdsError`.

See the [package README](README.md) for complete examples and
[compatibility policy](COMPATIBILITY.md) before relying on pre-1.0 API stability.
