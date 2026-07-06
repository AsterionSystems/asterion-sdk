"""Immutable mission database definition and runtime value models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, DecimalException
from enum import IntEnum, StrEnum
from types import MappingProxyType

from .errors import MdbValidationError, TimeArithmeticError


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


class TimeScale(StrEnum):
    UTC = "UTC"
    TAI = "TAI"
    GPS = "GPS"
    TT = "TT"


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
class DynamicDimension:
    """A bounded integer dimension derived from a parameter or caller context."""

    source: ParameterReference | ContextReference
    maximum: int
    multiplier: int = 1
    offset: int = 0


@dataclass(frozen=True, slots=True)
class TimeEpochDefinition:
    name: QualifiedName
    origin: datetime
    time_scale: TimeScale
    description: str | None = None


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
    size_bits: int | DynamicDimension
    validity_criteria: tuple[Comparison, ...] = ()


@dataclass(frozen=True, slots=True)
class StringParameterType:
    name: QualifiedName
    size_bits: int | DynamicDimension
    encoding: StringEncoding = StringEncoding.ASCII
    strip_padding: bytes = b"\x00"
    validity_criteria: tuple[Comparison, ...] = ()


@dataclass(frozen=True, slots=True)
class ArrayParameterType:
    name: QualifiedName
    element_type_ref: str | QualifiedName
    element_count: int | DynamicDimension
    validity_criteria: tuple[Comparison, ...] = ()


@dataclass(frozen=True, slots=True)
class AggregateMember:
    name: str
    type_ref: str | QualifiedName


@dataclass(frozen=True, slots=True)
class AggregateParameterType:
    name: QualifiedName
    members: tuple[AggregateMember, ...]
    validity_criteria: tuple[Comparison, ...] = ()


@dataclass(frozen=True, slots=True)
class AbsoluteTimeParameterType:
    name: QualifiedName
    encoding_type_ref: str | QualifiedName
    epoch_ref: str | QualifiedName
    seconds_per_unit: Decimal = Decimal("1")
    offset_seconds: Decimal = Decimal("0")
    validity_criteria: tuple[Comparison, ...] = ()


@dataclass(frozen=True, slots=True)
class RelativeTimeParameterType:
    name: QualifiedName
    encoding_type_ref: str | QualifiedName
    seconds_per_unit: Decimal = Decimal("1")
    offset_seconds: Decimal = Decimal("0")
    validity_criteria: tuple[Comparison, ...] = ()


type ScalarParameterType = (
    IntegerParameterType
    | FloatParameterType
    | BooleanParameterType
    | EnumeratedParameterType
    | BinaryParameterType
    | StringParameterType
)

type ParameterType = (
    ScalarParameterType
    | ArrayParameterType
    | AggregateParameterType
    | AbsoluteTimeParameterType
    | RelativeTimeParameterType
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
class RepeatEntry:
    name: str
    entries: tuple[ParameterEntry, ...]
    count: int | DynamicDimension


type ContainerEntry = ParameterEntry | RepeatEntry


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
    entries: tuple[ContainerEntry, ...] = ()
    base_container_ref: str | QualifiedName | None = None
    restrictions: tuple[Comparison, ...] = ()


@dataclass(frozen=True, slots=True)
class ParameterValue:
    parameter: ParameterDefinition
    raw_value: RuntimeRawValue
    value: RuntimeValue
    unit: str | None
    is_valid: bool = True
    alarm_severity: AlarmSeverity | None = None


@dataclass(frozen=True, slots=True)
class ElementValue:
    raw_value: RuntimeRawValue
    value: RuntimeValue
    unit: str | None
    is_valid: bool
    alarm_severity: AlarmSeverity | None


@dataclass(frozen=True, slots=True)
class ArrayValue:
    elements: tuple[ElementValue, ...]

    def __len__(self) -> int:
        return len(self.elements)

    def __iter__(self):
        return iter(self.elements)


@dataclass(frozen=True, slots=True)
class AggregateMemberValue:
    member: AggregateMember
    raw_value: RuntimeRawValue
    value: RuntimeValue
    unit: str | None
    is_valid: bool
    alarm_severity: AlarmSeverity | None


@dataclass(frozen=True, slots=True)
class AggregateValue:
    members: tuple[AggregateMemberValue, ...]
    by_name: MappingProxyType[str, AggregateMemberValue] = field(
        repr=False, compare=False
    )

    def __getitem__(self, name: str) -> AggregateMemberValue:
        return self.by_name[name]


@dataclass(frozen=True, slots=True)
class RelativeTimeValue:
    seconds: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.seconds, Decimal) or not self.seconds.is_finite():
            raise TimeArithmeticError("relative time seconds must be a finite Decimal")

    def __add__(self, other: RelativeTimeValue) -> RelativeTimeValue:
        if not isinstance(other, RelativeTimeValue):
            return NotImplemented
        return RelativeTimeValue(self.seconds + other.seconds)

    def __sub__(self, other: RelativeTimeValue) -> RelativeTimeValue:
        if not isinstance(other, RelativeTimeValue):
            return NotImplemented
        return RelativeTimeValue(self.seconds - other.seconds)


@dataclass(frozen=True, slots=True)
class AbsoluteTimeValue:
    epoch: TimeEpochDefinition
    elapsed_seconds: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.epoch, TimeEpochDefinition):
            raise TimeArithmeticError("absolute time epoch is invalid")
        if (
            not isinstance(self.elapsed_seconds, Decimal)
            or not self.elapsed_seconds.is_finite()
        ):
            raise TimeArithmeticError(
                "absolute elapsed seconds must be a finite Decimal"
            )

    def __add__(self, other: RelativeTimeValue) -> AbsoluteTimeValue:
        if not isinstance(other, RelativeTimeValue):
            return NotImplemented
        return AbsoluteTimeValue(self.epoch, self.elapsed_seconds + other.seconds)

    def __sub__(
        self, other: AbsoluteTimeValue | RelativeTimeValue
    ) -> RelativeTimeValue | AbsoluteTimeValue:
        if isinstance(other, RelativeTimeValue):
            return AbsoluteTimeValue(self.epoch, self.elapsed_seconds - other.seconds)
        if not isinstance(other, AbsoluteTimeValue):
            return NotImplemented
        if self.epoch != other.epoch:
            raise TimeArithmeticError(
                "absolute times must use the same epoch and time scale"
            )
        return RelativeTimeValue(self.elapsed_seconds - other.elapsed_seconds)

    def to_datetime(self) -> datetime:
        if self.epoch.time_scale is not TimeScale.UTC:
            raise TimeArithmeticError("datetime conversion is supported only for UTC")
        try:
            microseconds = int(
                (self.elapsed_seconds * Decimal(1_000_000)).quantize(
                    Decimal(1), rounding=ROUND_HALF_EVEN
                )
            )
            return self.epoch.origin + timedelta(microseconds=microseconds)
        except (DecimalException, OverflowError, ValueError) as error:
            raise TimeArithmeticError(
                f"datetime conversion is out of range: {error}"
            ) from error


type RuntimeValue = (
    EngineeringValue
    | ArrayValue
    | AggregateValue
    | AbsoluteTimeValue
    | RelativeTimeValue
)
type RuntimeRawValue = RawValue | tuple["RuntimeRawValue", ...]


@dataclass(frozen=True, slots=True)
class RepeatedEntryValue:
    name: str
    rows: tuple[tuple[ParameterValue, ...], ...]

    def __len__(self) -> int:
        return len(self.rows)


@dataclass(frozen=True, slots=True)
class DecodedContainer:
    container: SequenceContainer
    parameters: tuple[ParameterValue, ...]
    consumed_bits: int
    by_name: MappingProxyType[QualifiedName, ParameterValue] = field(
        repr=False, compare=False
    )
    repeated_entries: tuple[RepeatedEntryValue, ...] = ()
    repeats_by_name: MappingProxyType[str, RepeatedEntryValue] = field(
        default_factory=lambda: MappingProxyType({}), repr=False, compare=False
    )
