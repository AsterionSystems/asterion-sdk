# CCSDS conformance

The implementation targets CCSDS 133.0-B-2, Space Packet Protocol, Issue 2,
including Editorial Change 2.

| Standard area | Implementation |
| --- | --- |
| §4.1.2 packet size | Enforces 6-byte primary headers and 1–65536 data octets. |
| §4.1.3 primary header | Encodes and decodes every defined field in network byte order. |
| §4.1.3.4 sequence control | Supports all four flags and continuous modulo-16384 counts per APID. |
| §4.1.3.5 data length | Encodes and validates the specified length-minus-one value. |
| §4.1.4 secondary header | Provides a mission-defined codec boundary; no format is assumed. |

Implementation policies beyond the standard:

- Raw stream corruption is fatal until reset because Space Packets have no sync marker.
- Reassembly requires explicit byte bounds and discards only the invalid APID state.
- Generic segmentation rejects secondary headers because it cannot recreate their
  mission-specific contents for every segment.
- Telecommand sequence control is treated as a sequence count. The standard also
  permits a mission-defined Packet Name, which callers may set directly on headers.

Transfer frames, channel coding, PUS, CFDP, synchronization, and transport I/O are
outside this distribution.
