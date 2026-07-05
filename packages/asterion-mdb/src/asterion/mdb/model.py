"""Immutable mission database definition and runtime value models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from types import MappingProxyType

from .errors import MdbValidationError


@dataclass(frozen=True, slots=True, order=True)
class QualifiedName:
    """An absolute hierarchical mission database name."""

    parts: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.parts or any(not part or "/" in part for part in self.parts):
            raise MdbValidationError("qualified names require non-empty path segments")

    @classmethod
    def parse(cls, value: str | QualifiedName) -> QualifiedName:
        if isinstance(value, cls):
            return value
        if not isinstance(value, str) or not value.startswith("/"):
            raise MdbValidationError(
                "qualified names must be absolute and start with '/'"
            )
        return cls(tuple(value[1:].split("/")))

    @property
    def parent(self) -> QualifiedName | None:
        return QualifiedName(self.parts[:-1]) if len(self.parts) > 1 else None

    def child(self, name: str) -> QualifiedName:
        return QualifiedName((*self.parts, name))

    def __str__(self) -> str:
        return "/" + "/".join(self.parts)


class ByteOrder(StrEnum):
    BIG_ENDIAN = "big"
    LITTLE_ENDIAN = "little"


class StringEncoding(StrEnum):
    ASCII = "ascii"
    UTF8 = "utf-8"


@dataclass(frozen=True, slots=True)
class EnumeratedValue:
    raw: int
    label: str | None


type Scalar = int | float | bool | str | bytes
type RawValue = Scalar
type EngineeringValue = Scalar | EnumeratedValue


@dataclass(frozen=True, slots=True)
class PolynomialCalibrator:
    """Polynomial coefficients ordered from constant to highest power."""

    coefficients: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.coefficients:
            raise MdbValidationError("a polynomial calibrator needs coefficients")

    def calibrate(self, raw: int | float) -> float:
        return sum(value * raw**power for power, value in enumerate(self.coefficients))


class AlarmSeverity(IntEnum):
    """XTCE-aligned alarm severity ordered from least to most severe."""

    WATCH = 1
    WARNING = 2
    DISTRESS = 3
    CRITICAL = 4
    SEVERE = 5


@dataclass(frozen=True, slots=True)
class ContextCalibrator:
    """A polynomial calibrator selected when all criteria match."""

    criteria: tuple[Comparison, ...]
    calibrator: PolynomialCalibrator


@dataclass(frozen=True, slots=True)
class NumericAlarmRange:
    """An engineering-value alarm interval with independently inclusive bounds."""

    severity: AlarmSeverity
    minimum: int | float | None = None
    maximum: int | float | None = None
    minimum_inclusive: bool = True
    maximum_inclusive: bool = True


@dataclass(frozen=True, slots=True)
class EnumerationAlarm:
    """An alarm associated with one raw enumerated value."""

    raw_value: int
    severity: AlarmSeverity


@dataclass(frozen=True, slots=True)
class IntegerParameterType:
    name: QualifiedName
    size_bits: int
    signed: bool = False
    byte_order: ByteOrder = ByteOrder.BIG_ENDIAN
    unit: str | None = None
    calibrator: PolynomialCalibrator | None = None
    contextual_calibrators: tuple[ContextCalibrator, ...] = ()
    validity_criteria: tuple[Comparison, ...] = ()
    alarm_ranges: tuple[NumericAlarmRange, ...] = ()


@dataclass(frozen=True, slots=True)
class FloatParameterType:
    name: QualifiedName
    size_bits: int
    byte_order: ByteOrder = ByteOrder.BIG_ENDIAN
    unit: str | None = None
    calibrator: PolynomialCalibrator | None = None
    contextual_calibrators: tuple[ContextCalibrator, ...] = ()
    validity_criteria: tuple[Comparison, ...] = ()
    alarm_ranges: tuple[NumericAlarmRange, ...] = ()


@dataclass(frozen=True, slots=True)
class BooleanParameterType:
    name: QualifiedName
    size_bits: int = 1
    byte_order: ByteOrder = ByteOrder.BIG_ENDIAN
    validity_criteria: tuple[Comparison, ...] = ()


@dataclass(frozen=True, slots=True)
class EnumeratedParameterType:
    name: QualifiedName
    size_bits: int
    choices: tuple[tuple[int, str], ...]
    signed: bool = False
    byte_order: ByteOrder = ByteOrder.BIG_ENDIAN
    validity_criteria: tuple[Comparison, ...] = ()
    alarms: tuple[EnumerationAlarm, ...] = ()


@dataclass(frozen=True, slots=True)
class BinaryParameterType:
    name: QualifiedName
    size_bits: int
    validity_criteria: tuple[Comparison, ...] = ()


@dataclass(frozen=True, slots=True)
class StringParameterType:
    name: QualifiedName
    size_bits: int
    encoding: StringEncoding = StringEncoding.ASCII
    strip_padding: bytes = b"\x00"
    validity_criteria: tuple[Comparison, ...] = ()


type ParameterType = (
    IntegerParameterType
    | FloatParameterType
    | BooleanParameterType
    | EnumeratedParameterType
    | BinaryParameterType
    | StringParameterType
)


@dataclass(frozen=True, slots=True)
class SpaceSystem:
    name: QualifiedName
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ParameterDefinition:
    name: QualifiedName
    type_ref: str | QualifiedName
    description: str | None = None
    aliases: tuple[str, ...] = ()


class EntryPosition(StrEnum):
    SEQUENTIAL = "sequential"
    ABSOLUTE = "absolute"


@dataclass(frozen=True, slots=True)
class ParameterEntry:
    parameter_ref: str | QualifiedName
    position: EntryPosition = EntryPosition.SEQUENTIAL
    bit_offset: int | None = None


@dataclass(frozen=True, slots=True)
class ParameterReference:
    reference: str | QualifiedName


@dataclass(frozen=True, slots=True)
class ContextReference:
    name: str


class ComparisonOperator(StrEnum):
    EQUAL = "=="
    NOT_EQUAL = "!="
    LESS_THAN = "<"
    LESS_THAN_OR_EQUAL = "<="
    GREATER_THAN = ">"
    GREATER_THAN_OR_EQUAL = ">="


@dataclass(frozen=True, slots=True)
class Comparison:
    left: ParameterReference | ContextReference
    operator: ComparisonOperator
    right: Scalar


@dataclass(frozen=True, slots=True)
class SequenceContainer:
    name: QualifiedName
    entries: tuple[ParameterEntry, ...] = ()
    base_container_ref: str | QualifiedName | None = None
    restrictions: tuple[Comparison, ...] = ()


@dataclass(frozen=True, slots=True)
class ParameterValue:
    parameter: ParameterDefinition
    raw_value: RawValue
    value: EngineeringValue
    unit: str | None
    is_valid: bool = True
    alarm_severity: AlarmSeverity | None = None


@dataclass(frozen=True, slots=True)
class DecodedContainer:
    container: SequenceContainer
    parameters: tuple[ParameterValue, ...]
    consumed_bits: int
    by_name: MappingProxyType[QualifiedName, ParameterValue] = field(
        repr=False, compare=False
    )
