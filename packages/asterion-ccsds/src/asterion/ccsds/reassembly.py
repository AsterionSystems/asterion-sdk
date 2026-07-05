"""Bounded reassembly of segmented CCSDS Space Packet user data."""

from __future__ import annotations

from dataclasses import dataclass

from .packet import (
    MAX_APID,
    MAX_SEQUENCE_COUNT,
    CcsdsError,
    PacketType,
    PacketValidationError,
    SequenceFlags,
    SpacePacket,
)

_SEQUENCE_MODULUS = MAX_SEQUENCE_COUNT + 1


class ReassemblyError(CcsdsError, ValueError):
    """Raised when packet segments cannot be safely reassembled."""

    apid: int

    def __init__(self, message: str, *, apid: int) -> None:
        self.apid = apid
        super().__init__(f"APID {apid}: {message}")


@dataclass(frozen=True, slots=True)
class ReassembledPacketData:
    """Immutable application data reconstructed from one packet sequence."""

    data: bytes
    apid: int
    packet_type: PacketType
    first_sequence_count: int
    last_sequence_count: int
    segment_count: int


@dataclass(slots=True)
class _Assembly:
    data: bytearray
    packet_type: PacketType
    first_sequence_count: int
    last_sequence_count: int
    segment_count: int


def _validate_positive_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PacketValidationError(f"{name} must be an integer")
    if value < 1:
        raise PacketValidationError(f"{name} must be at least 1, got {value}")
    return value


def _validate_apid(apid: object) -> int:
    if isinstance(apid, bool) or not isinstance(apid, int):
        raise PacketValidationError("apid must be an integer")
    if not 0 <= apid <= MAX_APID:
        raise PacketValidationError(
            f"apid must be between 0 and {MAX_APID}, got {apid}"
        )
    return apid


class SpacePacketReassembler:
    """Reassemble segmented packet data with explicit memory bounds."""

    _assemblies: dict[int, _Assembly]
    _max_assembly_length: int
    _max_active_assemblies: int

    def __init__(
        self,
        *,
        max_assembly_length: int,
        max_active_assemblies: int = 64,
    ) -> None:
        self._max_assembly_length = _validate_positive_integer(
            "max_assembly_length", max_assembly_length
        )
        self._max_active_assemblies = _validate_positive_integer(
            "max_active_assemblies", max_active_assemblies
        )
        self._assemblies = {}

    @property
    def max_assembly_length(self) -> int:
        """Maximum number of bytes permitted in one completed assembly."""
        return self._max_assembly_length

    @property
    def max_active_assemblies(self) -> int:
        """Maximum number of APIDs that may be incomplete concurrently."""
        return self._max_active_assemblies

    @property
    def active_assembly_count(self) -> int:
        """Number of APIDs with an incomplete assembly."""
        return len(self._assemblies)

    def push(self, packet: SpacePacket) -> ReassembledPacketData | None:
        """Consume one packet and return data when its assembly completes."""
        if not isinstance(packet, SpacePacket):
            raise PacketValidationError("packet must be a SpacePacket")

        header = packet.header
        apid = header.apid
        if header.secondary_header_flag:
            self._assemblies.pop(apid, None)
            raise ReassemblyError(
                "secondary-header packets require mission-specific reassembly",
                apid=apid,
            )

        if header.sequence_flags is SequenceFlags.UNSEGMENTED:
            if apid in self._assemblies:
                self._assemblies.pop(apid)
                raise ReassemblyError(
                    "unsegmented packet interrupted an active assembly", apid=apid
                )
            self._check_length(apid=apid, length=packet.data_length, discard=False)
            return ReassembledPacketData(
                data=packet.data,
                apid=apid,
                packet_type=header.packet_type,
                first_sequence_count=header.sequence_count,
                last_sequence_count=header.sequence_count,
                segment_count=1,
            )

        if header.sequence_flags is SequenceFlags.FIRST_SEGMENT:
            if apid in self._assemblies:
                self._assemblies.pop(apid)
                raise ReassemblyError(
                    "first segment interrupted an active assembly", apid=apid
                )
            if len(self._assemblies) >= self._max_active_assemblies:
                raise ReassemblyError("active assembly limit reached", apid=apid)
            self._check_length(apid=apid, length=packet.data_length, discard=False)
            self._assemblies[apid] = _Assembly(
                data=bytearray(packet.data),
                packet_type=header.packet_type,
                first_sequence_count=header.sequence_count,
                last_sequence_count=header.sequence_count,
                segment_count=1,
            )
            return None

        assembly = self._assemblies.get(apid)
        if assembly is None:
            raise ReassemblyError("segment received without a first segment", apid=apid)
        if header.packet_type is not assembly.packet_type:
            self._assemblies.pop(apid)
            raise ReassemblyError("packet type changed during assembly", apid=apid)
        expected_count = (assembly.last_sequence_count + 1) % _SEQUENCE_MODULUS
        if header.sequence_count != expected_count:
            self._assemblies.pop(apid)
            raise ReassemblyError(
                f"expected sequence count {expected_count}, got {header.sequence_count}",
                apid=apid,
            )

        new_length = len(assembly.data) + packet.data_length
        self._check_length(apid=apid, length=new_length, discard=True)
        assembly.data.extend(packet.data)
        assembly.last_sequence_count = header.sequence_count
        assembly.segment_count += 1

        if header.sequence_flags is SequenceFlags.CONTINUATION:
            return None

        self._assemblies.pop(apid)
        return ReassembledPacketData(
            data=bytes(assembly.data),
            apid=apid,
            packet_type=assembly.packet_type,
            first_sequence_count=assembly.first_sequence_count,
            last_sequence_count=assembly.last_sequence_count,
            segment_count=assembly.segment_count,
        )

    def reset_stream(self, *, apid: int) -> None:
        """Discard one APID's incomplete assembly."""
        self._assemblies.pop(_validate_apid(apid), None)

    def reset(self) -> None:
        """Discard every incomplete assembly."""
        self._assemblies.clear()

    def _check_length(self, *, apid: int, length: int, discard: bool) -> None:
        if length <= self._max_assembly_length:
            return
        if discard:
            self._assemblies.pop(apid, None)
        raise ReassemblyError(
            f"assembly length {length} exceeds configured maximum of "
            f"{self._max_assembly_length} bytes",
            apid=apid,
        )
