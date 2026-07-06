"""Mission database compilation and telemetry decoding."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException
from math import isfinite
from types import MappingProxyType

from .decoder import BytesLike, decode_parameter, immutable_values, normalize_bytes
from .errors import (
    AlarmEvaluationError,
    AmbiguousContainerError,
    CalibrationError,
    CalibrationSelectionError,
    DynamicDimensionError,
    MdbDecodeError,
    MdbValidationError,
    NoMatchingContainerError,
    ReferenceResolutionError,
    StructureLimitError,
    TimeDecodeError,
    ValidityEvaluationError,
)
from .model import (
    AbsoluteTimeParameterType,
    AbsoluteTimeValue,
    AggregateMember,
    AggregateMemberValue,
    AggregateParameterType,
    AggregateValue,
    AlarmSeverity,
    ArrayParameterType,
    ArrayValue,
    BinaryParameterType,
    BooleanParameterType,
    ByteOrder,
    Comparison,
    ComparisonOperator,
    ContextCalibrator,
    ContextReference,
    DecodedContainer,
    DynamicDimension,
    ElementValue,
    EntryPosition,
    EnumeratedParameterType,
    EnumeratedValue,
    EnumerationAlarm,
    FloatParameterType,
    IntegerParameterType,
    NumericAlarmRange,
    ParameterDefinition,
    ParameterEntry,
    ParameterReference,
    ParameterType,
    ParameterValue,
    PolynomialCalibrator,
    QualifiedName,
    RelativeTimeParameterType,
    RelativeTimeValue,
    RepeatedEntryValue,
    RepeatEntry,
    RuntimeRawValue,
    Scalar,
    SequenceContainer,
    SpaceSystem,
    StringEncoding,
    StringParameterType,
    TimeEpochDefinition,
    TimeScale,
)

DEFAULT_MAX_STRUCTURE_DEPTH = 32
DEFAULT_MAX_DECODED_VALUES = 100_000


def _owner(name: QualifiedName) -> QualifiedName:
    parent = name.parent
    if parent is None:
        raise MdbValidationError(f"definition {name} must belong to a space system")
    return parent


def _resolve_reference[T](
    reference: str | QualifiedName,
    *,
    owner: QualifiedName,
    definitions: Mapping[QualifiedName, T],
) -> QualifiedName:
    if not isinstance(reference, (str, QualifiedName)):
        raise ReferenceResolutionError(
            "references must be strings or QualifiedName values"
        )
    if isinstance(reference, QualifiedName) or reference.startswith("/"):
        candidate = QualifiedName.parse(reference)
        if candidate not in definitions:
            raise ReferenceResolutionError(f"reference {candidate} does not exist")
        return candidate

    scope: QualifiedName | None = owner
    while scope is not None:
        candidate = scope.child(reference)
        if candidate in definitions:
            return candidate
        scope = scope.parent
    raise ReferenceResolutionError(
        f"relative reference {reference!r} cannot be resolved from {owner}"
    )


class MissionDatabaseBuilder:
    """Mutable collector that validates and compiles mission definitions."""

    def __init__(
        self,
        name: str,
        *,
        max_structure_depth: int = DEFAULT_MAX_STRUCTURE_DEPTH,
        max_decoded_values: int = DEFAULT_MAX_DECODED_VALUES,
    ) -> None:
        if not isinstance(name, str) or not name or "/" in name:
            raise MdbValidationError("database name must be one non-empty path segment")
        self.name = name
        self.max_structure_depth = self._positive_limit(
            max_structure_depth, "max_structure_depth"
        )
        self.max_decoded_values = self._positive_limit(
            max_decoded_values, "max_decoded_values"
        )
        self._systems: dict[QualifiedName, SpaceSystem] = {}
        self._epochs: dict[QualifiedName, TimeEpochDefinition] = {}
        self._types: dict[QualifiedName, ParameterType] = {}
        self._parameters: dict[QualifiedName, ParameterDefinition] = {}
        self._containers: dict[QualifiedName, SequenceContainer] = {}

    @staticmethod
    def _positive_limit(value: int, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise MdbValidationError(f"{name} must be a positive integer")
        return value

    def add_space_system(self, system: SpaceSystem) -> None:
        if not isinstance(system, SpaceSystem):
            raise MdbValidationError("space system must be a SpaceSystem")
        self._add_unique(self._systems, system.name, system, "space system")

    def add_parameter_type(self, parameter_type: ParameterType) -> None:
        if not isinstance(
            parameter_type,
            (
                IntegerParameterType,
                FloatParameterType,
                BooleanParameterType,
                EnumeratedParameterType,
                BinaryParameterType,
                StringParameterType,
                ArrayParameterType,
                AggregateParameterType,
                AbsoluteTimeParameterType,
                RelativeTimeParameterType,
            ),
        ):
            raise MdbValidationError("parameter type is not supported")
        self._add_unique(
            self._types, parameter_type.name, parameter_type, "parameter type"
        )

    def add_time_epoch(self, epoch: TimeEpochDefinition) -> None:
        if not isinstance(epoch, TimeEpochDefinition):
            raise MdbValidationError("time epoch must be a TimeEpochDefinition")
        self._add_unique(self._epochs, epoch.name, epoch, "time epoch")

    def add_parameter(self, parameter: ParameterDefinition) -> None:
        if not isinstance(parameter, ParameterDefinition):
            raise MdbValidationError("parameter must be a ParameterDefinition")
        self._add_unique(self._parameters, parameter.name, parameter, "parameter")

    def add_container(self, container: SequenceContainer) -> None:
        if not isinstance(container, SequenceContainer):
            raise MdbValidationError("container must be a SequenceContainer")
        self._add_unique(self._containers, container.name, container, "container")

    def compile(self) -> MissionDatabase:
        self._validate_systems()
        epochs = {
            name: self._validate_epoch(value) for name, value in self._epochs.items()
        }
        types = {
            name: self._validate_type(value) for name, value in self._types.items()
        }
        parameters, aliases = self._resolve_parameters(types)
        types = self._resolve_type_evaluation(types, parameters, epochs)
        self._validate_type_cycles(types)
        containers = self._resolve_containers(parameters)
        self._validate_time_references(types, parameters, containers)
        self._validate_container_cycles(containers)
        derived: dict[QualifiedName, list[QualifiedName]] = {}
        for container in containers.values():
            if container.base_container_ref is not None:
                base = QualifiedName.parse(container.base_container_ref)
                derived.setdefault(base, []).append(container.name)
        return MissionDatabase(
            name=self.name,
            systems=MappingProxyType(dict(self._systems)),
            time_epochs=MappingProxyType(epochs),
            parameter_types=MappingProxyType(types),
            parameters=MappingProxyType(parameters),
            aliases=MappingProxyType(aliases),
            containers=MappingProxyType(containers),
            derived_containers=MappingProxyType(
                {name: tuple(children) for name, children in derived.items()}
            ),
            max_structure_depth=self.max_structure_depth,
            max_decoded_values=self.max_decoded_values,
        )

    @staticmethod
    def _add_unique[T](
        target: dict[QualifiedName, T],
        name: QualifiedName,
        value: T,
        kind: str,
    ) -> None:
        if not isinstance(name, QualifiedName):
            raise MdbValidationError(f"{kind} name must be a QualifiedName")
        if name in target:
            raise MdbValidationError(f"duplicate {kind} definition: {name}")
        target[name] = value

    def _validate_systems(self) -> None:
        for name in self._systems:
            if name.parent is not None and name.parent not in self._systems:
                raise MdbValidationError(
                    f"space system {name} has missing parent {name.parent}"
                )
        for definitions in (
            self._epochs,
            self._types,
            self._parameters,
            self._containers,
        ):
            for name in definitions:
                if _owner(name) not in self._systems:
                    raise MdbValidationError(
                        f"definition {name} belongs to unknown space system {_owner(name)}"
                    )

    @staticmethod
    def _validate_epoch(value: TimeEpochDefinition) -> TimeEpochDefinition:
        if not isinstance(value.origin, datetime) or value.origin.tzinfo is None:
            raise MdbValidationError(
                f"{value.name} epoch origin must be timezone-aware"
            )
        if value.origin.utcoffset() != timedelta(0):
            raise MdbValidationError(f"{value.name} epoch origin must use zero offset")
        if not isinstance(value.time_scale, TimeScale):
            raise MdbValidationError(f"{value.name} time scale is invalid")
        return value

    @staticmethod
    def _validate_type(value: ParameterType) -> ParameterType:
        if isinstance(
            value, (IntegerParameterType, BooleanParameterType, EnumeratedParameterType)
        ):
            if not isinstance(value.byte_order, ByteOrder):
                raise MdbValidationError(f"{value.name} byte order is invalid")
            if value.size_bits < 1:
                raise MdbValidationError(f"{value.name} size_bits must be positive")
            if value.byte_order is ByteOrder.LITTLE_ENDIAN and value.size_bits % 8:
                raise MdbValidationError(
                    f"{value.name} little-endian size must be whole bytes"
                )
            if isinstance(value, EnumeratedParameterType):
                keys = [key for key, _ in value.choices]
                if len(keys) != len(set(keys)):
                    raise MdbValidationError(
                        f"{value.name} has duplicate enumeration values"
                    )
                MissionDatabaseBuilder._validate_enumeration_alarms(value)
        elif isinstance(value, FloatParameterType):
            if not isinstance(value.byte_order, ByteOrder):
                raise MdbValidationError(f"{value.name} byte order is invalid")
            if value.size_bits not in (32, 64):
                raise MdbValidationError(f"{value.name} float size must be 32 or 64")
        elif isinstance(value, (BinaryParameterType, StringParameterType)):
            if isinstance(value.size_bits, int):
                if isinstance(value.size_bits, bool) or (
                    value.size_bits < 8 or value.size_bits % 8
                ):
                    raise MdbValidationError(
                        f"{value.name} size must be positive whole bytes"
                    )
            else:
                MissionDatabaseBuilder._validate_dimension(
                    value.size_bits, f"{value.name} size"
                )
            if isinstance(value, StringParameterType) and not value.strip_padding:
                raise MdbValidationError(
                    f"{value.name} strip_padding must not be empty"
                )
            if isinstance(value, StringParameterType) and (
                not isinstance(value.strip_padding, bytes)
                or not isinstance(value.encoding, StringEncoding)
            ):
                raise MdbValidationError(f"{value.name} string encoding is invalid")
        elif isinstance(value, ArrayParameterType):
            MissionDatabaseBuilder._validate_count(
                value.element_count, f"{value.name} element count"
            )
        elif isinstance(value, AggregateParameterType):
            if any(not isinstance(member, AggregateMember) for member in value.members):
                raise MdbValidationError(f"{value.name} aggregate members are invalid")
            names = [member.name for member in value.members]
            if any(not isinstance(name, str) or not name for name in names):
                raise MdbValidationError(
                    f"{value.name} aggregate member names must not be empty"
                )
            if len(names) != len(set(names)):
                raise MdbValidationError(
                    f"{value.name} has duplicate aggregate member names"
                )
        elif isinstance(value, (AbsoluteTimeParameterType, RelativeTimeParameterType)):
            MissionDatabaseBuilder._validate_time_type(value)
        if isinstance(value, (IntegerParameterType, FloatParameterType)):
            MissionDatabaseBuilder._validate_numeric_evaluation(value)
        return value

    @staticmethod
    def _validate_time_type(
        value: AbsoluteTimeParameterType | RelativeTimeParameterType,
    ) -> None:
        for field_name, field_value in (
            ("seconds_per_unit", value.seconds_per_unit),
            ("offset_seconds", value.offset_seconds),
        ):
            if not isinstance(field_value, Decimal) or not field_value.is_finite():
                raise MdbValidationError(
                    f"{value.name} {field_name} must be a finite Decimal"
                )
        if value.seconds_per_unit <= 0:
            raise MdbValidationError(f"{value.name} seconds_per_unit must be positive")

    @staticmethod
    def _validate_count(value: int | DynamicDimension, label: str) -> None:
        if isinstance(value, DynamicDimension):
            MissionDatabaseBuilder._validate_dimension(value, label)
        elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise MdbValidationError(f"{label} must be a nonnegative integer")

    @staticmethod
    def _validate_dimension(value: DynamicDimension, label: str) -> None:
        if not isinstance(value, DynamicDimension):
            raise MdbValidationError(f"{label} must be fixed or dynamic")
        if not isinstance(value.source, (ParameterReference, ContextReference)):
            raise MdbValidationError(f"{label} source is invalid")
        if isinstance(value.source, ContextReference) and (
            not isinstance(value.source.name, str) or not value.source.name
        ):
            raise MdbValidationError(f"{label} context source is invalid")
        for field_name, field_value in (
            ("maximum", value.maximum),
            ("multiplier", value.multiplier),
            ("offset", value.offset),
        ):
            if isinstance(field_value, bool) or not isinstance(field_value, int):
                raise MdbValidationError(f"{label} {field_name} must be an integer")
        if value.maximum < 1:
            raise MdbValidationError(f"{label} maximum must be positive")
        if not isinstance(value.use_raw_value, bool):
            raise MdbValidationError(f"{label} use_raw_value must be boolean")

    @staticmethod
    def _validate_numeric_evaluation(
        value: IntegerParameterType | FloatParameterType,
    ) -> None:
        if value.calibrator is not None:
            MissionDatabaseBuilder._validate_calibrator(value.name, value.calibrator)
        for contextual in value.contextual_calibrators:
            if not isinstance(contextual, ContextCalibrator) or not contextual.criteria:
                raise MdbValidationError(
                    f"{value.name} contextual calibrators require criteria"
                )
            MissionDatabaseBuilder._validate_calibrator(
                value.name, contextual.calibrator
            )
        for alarm in value.alarm_ranges:
            if not isinstance(alarm, NumericAlarmRange):
                raise MdbValidationError(f"{value.name} has an invalid alarm range")
            if not isinstance(alarm.severity, AlarmSeverity):
                raise MdbValidationError(f"{value.name} has an invalid alarm severity")
            if alarm.minimum is None and alarm.maximum is None:
                raise MdbValidationError(f"{value.name} alarm range needs a bound")
            bounds = (alarm.minimum, alarm.maximum)
            if any(
                isinstance(bound, bool)
                or not isinstance(bound, (int, float))
                or (isinstance(bound, float) and not isfinite(bound))
                for bound in bounds
                if bound is not None
            ):
                raise MdbValidationError(f"{value.name} alarm bounds must be numeric")
            if not isinstance(alarm.minimum_inclusive, bool) or not isinstance(
                alarm.maximum_inclusive, bool
            ):
                raise MdbValidationError(
                    f"{value.name} alarm inclusivity flags must be boolean"
                )
            if (
                alarm.minimum is not None
                and alarm.maximum is not None
                and (
                    alarm.minimum > alarm.maximum
                    or (
                        alarm.minimum == alarm.maximum
                        and not (alarm.minimum_inclusive and alarm.maximum_inclusive)
                    )
                )
            ):
                raise MdbValidationError(f"{value.name} has an empty alarm range")

    @staticmethod
    def _validate_calibrator(
        name: QualifiedName, calibrator: PolynomialCalibrator
    ) -> None:
        if not isinstance(calibrator, PolynomialCalibrator):
            raise MdbValidationError(f"{name} calibrator must be polynomial")
        if any(
            isinstance(coefficient, bool)
            or not isinstance(coefficient, (int, float))
            or (isinstance(coefficient, float) and not isfinite(coefficient))
            for coefficient in calibrator.coefficients
        ):
            raise MdbValidationError(
                f"{name} calibrator coefficients must be finite numbers"
            )

    @staticmethod
    def _validate_enumeration_alarms(value: EnumeratedParameterType) -> None:
        raw_values: list[int] = []
        for alarm in value.alarms:
            if not isinstance(alarm, EnumerationAlarm):
                raise MdbValidationError(
                    f"{value.name} has an invalid enumeration alarm"
                )
            if isinstance(alarm.raw_value, bool) or not isinstance(
                alarm.raw_value, int
            ):
                raise MdbValidationError(f"{value.name} alarm values must be integers")
            if not isinstance(alarm.severity, AlarmSeverity):
                raise MdbValidationError(f"{value.name} has an invalid alarm severity")
            raw_values.append(alarm.raw_value)
        if len(raw_values) != len(set(raw_values)):
            raise MdbValidationError(f"{value.name} has duplicate enumeration alarms")

    def _resolve_parameters(
        self, types: Mapping[QualifiedName, ParameterType]
    ) -> tuple[dict[QualifiedName, ParameterDefinition], dict[str, QualifiedName]]:
        aliases: dict[str, QualifiedName] = {}
        resolved: dict[QualifiedName, ParameterDefinition] = {}
        for parameter in self._parameters.values():
            type_name = _resolve_reference(
                parameter.type_ref, owner=_owner(parameter.name), definitions=types
            )
            for alias in parameter.aliases:
                if alias in aliases:
                    raise MdbValidationError(f"duplicate parameter alias: {alias}")
                aliases[alias] = parameter.name
            resolved[parameter.name] = replace(parameter, type_ref=type_name)
        return resolved, aliases

    def _resolve_type_evaluation(
        self,
        types: Mapping[QualifiedName, ParameterType],
        parameters: Mapping[QualifiedName, ParameterDefinition],
        epochs: Mapping[QualifiedName, TimeEpochDefinition],
    ) -> dict[QualifiedName, ParameterType]:
        resolved: dict[QualifiedName, ParameterType] = {}
        for name, parameter_type in types.items():
            owner = _owner(name)
            validity = self._resolve_comparisons(
                parameter_type.validity_criteria,
                owner=owner,
                parameters=parameters,
            )
            changes: dict[str, object] = {"validity_criteria": validity}
            if isinstance(parameter_type, (IntegerParameterType, FloatParameterType)):
                contextual = tuple(
                    replace(
                        item,
                        criteria=self._resolve_comparisons(
                            item.criteria,
                            owner=owner,
                            parameters=parameters,
                        ),
                    )
                    for item in parameter_type.contextual_calibrators
                )
                changes["contextual_calibrators"] = contextual
            if isinstance(parameter_type, (BinaryParameterType, StringParameterType)):
                if isinstance(parameter_type.size_bits, DynamicDimension):
                    changes["size_bits"] = self._resolve_dimension(
                        parameter_type.size_bits,
                        owner=owner,
                        parameters=parameters,
                    )
            elif isinstance(parameter_type, ArrayParameterType):
                changes["element_type_ref"] = _resolve_reference(
                    parameter_type.element_type_ref,
                    owner=owner,
                    definitions=types,
                )
                if isinstance(parameter_type.element_count, DynamicDimension):
                    changes["element_count"] = self._resolve_dimension(
                        parameter_type.element_count,
                        owner=owner,
                        parameters=parameters,
                    )
            elif isinstance(parameter_type, AggregateParameterType):
                changes["members"] = tuple(
                    replace(
                        member,
                        type_ref=_resolve_reference(
                            member.type_ref,
                            owner=owner,
                            definitions=types,
                        ),
                    )
                    for member in parameter_type.members
                )
            elif isinstance(
                parameter_type,
                (AbsoluteTimeParameterType, RelativeTimeParameterType),
            ):
                encoding_name = _resolve_reference(
                    parameter_type.encoding_type_ref,
                    owner=owner,
                    definitions=types,
                )
                if not isinstance(
                    types[encoding_name], (IntegerParameterType, FloatParameterType)
                ):
                    raise MdbValidationError(
                        f"{parameter_type.name} time encoding must be integer or float"
                    )
                changes["encoding_type_ref"] = encoding_name
                if isinstance(parameter_type, AbsoluteTimeParameterType):
                    changes["epoch_ref"] = _resolve_reference(
                        parameter_type.epoch_ref,
                        owner=owner,
                        definitions=epochs,
                    )
            resolved[name] = replace(parameter_type, **changes)
        return resolved

    @staticmethod
    def _resolve_dimension(
        dimension: DynamicDimension,
        *,
        owner: QualifiedName,
        parameters: Mapping[QualifiedName, ParameterDefinition],
    ) -> DynamicDimension:
        if isinstance(dimension.source, ParameterReference):
            source = ParameterReference(
                _resolve_reference(
                    dimension.source.reference,
                    owner=owner,
                    definitions=parameters,
                )
            )
            return replace(dimension, source=source)
        if not dimension.source.name:
            raise MdbValidationError("dimension context name must not be empty")
        return dimension

    def _validate_type_cycles(
        self, types: Mapping[QualifiedName, ParameterType]
    ) -> None:
        def children(parameter_type: ParameterType) -> tuple[QualifiedName, ...]:
            if isinstance(parameter_type, ArrayParameterType):
                return (QualifiedName.parse(parameter_type.element_type_ref),)
            if isinstance(parameter_type, AggregateParameterType):
                return tuple(
                    QualifiedName.parse(member.type_ref)
                    for member in parameter_type.members
                )
            return ()

        def visit(
            name: QualifiedName,
            path: tuple[QualifiedName, ...],
            structured_depth: int,
        ) -> None:
            if name in path:
                raise MdbValidationError(f"structured type cycle includes {name}")
            nested = children(types[name])
            if nested and structured_depth >= self.max_structure_depth:
                raise MdbValidationError(
                    f"structured type nesting exceeds {self.max_structure_depth}"
                )
            for child in nested:
                visit(child, (*path, name), structured_depth + 1)

        for name in types:
            visit(name, (), 0)

    @staticmethod
    def _validate_time_references(
        types: Mapping[QualifiedName, ParameterType],
        parameters: Mapping[QualifiedName, ParameterDefinition],
        containers: Mapping[QualifiedName, SequenceContainer],
    ) -> None:
        time_parameters = {
            parameter.name
            for parameter in parameters.values()
            if isinstance(
                types[QualifiedName.parse(parameter.type_ref)],
                (AbsoluteTimeParameterType, RelativeTimeParameterType),
            )
        }

        def check_comparisons(comparisons: tuple[Comparison, ...]) -> None:
            for comparison in comparisons:
                if isinstance(comparison.left, ParameterReference) and (
                    QualifiedName.parse(comparison.left.reference) in time_parameters
                ):
                    raise MdbValidationError(
                        "time parameters cannot participate in comparisons"
                    )

        def check_dimension(dimension: int | DynamicDimension) -> None:
            if (
                isinstance(dimension, DynamicDimension)
                and isinstance(dimension.source, ParameterReference)
                and QualifiedName.parse(dimension.source.reference) in time_parameters
            ):
                raise MdbValidationError(
                    "time parameters cannot drive dynamic dimensions"
                )

        for parameter_type in types.values():
            check_comparisons(parameter_type.validity_criteria)
            if isinstance(parameter_type, (IntegerParameterType, FloatParameterType)):
                for contextual in parameter_type.contextual_calibrators:
                    check_comparisons(contextual.criteria)
            if isinstance(parameter_type, (BinaryParameterType, StringParameterType)):
                check_dimension(parameter_type.size_bits)
            elif isinstance(parameter_type, ArrayParameterType):
                check_dimension(parameter_type.element_count)
        for container in containers.values():
            check_comparisons(container.restrictions)
            for entry in container.entries:
                if isinstance(entry, RepeatEntry):
                    check_dimension(entry.count)

    @staticmethod
    def _resolve_comparisons(
        comparisons: tuple[Comparison, ...],
        *,
        owner: QualifiedName,
        parameters: Mapping[QualifiedName, ParameterDefinition],
    ) -> tuple[Comparison, ...]:
        resolved: list[Comparison] = []
        for comparison in comparisons:
            if not isinstance(comparison, Comparison):
                raise MdbValidationError("criteria must contain Comparison values")
            if not isinstance(comparison.operator, ComparisonOperator):
                raise MdbValidationError("comparison operator is invalid")
            if not isinstance(comparison.right, (int, float, bool, str, bytes)):
                raise MdbValidationError("comparison literal is invalid")
            if isinstance(comparison.left, ParameterReference):
                parameter_name = _resolve_reference(
                    comparison.left.reference,
                    owner=owner,
                    definitions=parameters,
                )
                comparison = replace(
                    comparison, left=ParameterReference(parameter_name)
                )
            elif isinstance(comparison.left, ContextReference):
                if not comparison.left.name:
                    raise MdbValidationError(
                        "context reference names must not be empty"
                    )
            else:
                raise MdbValidationError("comparison left side is invalid")
            resolved.append(comparison)
        return tuple(resolved)

    def _resolve_containers(
        self, parameters: Mapping[QualifiedName, ParameterDefinition]
    ) -> dict[QualifiedName, SequenceContainer]:
        resolved: dict[QualifiedName, SequenceContainer] = {}
        for container in self._containers.values():
            owner = _owner(container.name)
            entries: list[ParameterEntry | RepeatEntry] = []
            seen: set[QualifiedName] = set()
            repeat_names: set[str] = set()
            for entry in container.entries:
                if isinstance(entry, RepeatEntry):
                    if (
                        not isinstance(entry.name, str)
                        or not entry.name
                        or entry.name in repeat_names
                    ):
                        raise MdbValidationError(
                            f"container {container.name} has invalid duplicate repeat name"
                        )
                    repeat_names.add(entry.name)
                    self._validate_count(entry.count, f"repeat {entry.name} count")
                    count = entry.count
                    if isinstance(count, DynamicDimension):
                        count = self._resolve_dimension(
                            count, owner=owner, parameters=parameters
                        )
                    repeat_entries: list[ParameterEntry] = []
                    repeat_seen: set[QualifiedName] = set()
                    for repeated in entry.entries:
                        resolved_entry, parameter_name = self._resolve_parameter_entry(
                            repeated, owner=owner, parameters=parameters
                        )
                        if parameter_name in repeat_seen:
                            raise MdbValidationError(
                                f"repeat {entry.name} decodes {parameter_name} more than once"
                            )
                        repeat_seen.add(parameter_name)
                        repeat_entries.append(resolved_entry)
                    entries.append(
                        replace(entry, entries=tuple(repeat_entries), count=count)
                    )
                    continue
                if not isinstance(entry, ParameterEntry):
                    raise MdbValidationError("container entry is not supported")
                resolved_entry, parameter_name = self._resolve_parameter_entry(
                    entry, owner=owner, parameters=parameters
                )
                if parameter_name in seen:
                    raise MdbValidationError(
                        f"container {container.name} decodes {parameter_name} more than once"
                    )
                seen.add(parameter_name)
                entries.append(resolved_entry)

            base = None
            if container.base_container_ref is not None:
                base = _resolve_reference(
                    container.base_container_ref,
                    owner=owner,
                    definitions=self._containers,
                )
            restrictions = self._resolve_comparisons(
                container.restrictions, owner=owner, parameters=parameters
            )
            resolved[container.name] = replace(
                container,
                entries=tuple(entries),
                base_container_ref=base,
                restrictions=restrictions,
            )
        return resolved

    @staticmethod
    def _resolve_parameter_entry(
        entry: ParameterEntry,
        *,
        owner: QualifiedName,
        parameters: Mapping[QualifiedName, ParameterDefinition],
    ) -> tuple[ParameterEntry, QualifiedName]:
        if not isinstance(entry, ParameterEntry):
            raise MdbValidationError("repeat groups may contain only parameter entries")
        parameter_name = _resolve_reference(
            entry.parameter_ref, owner=owner, definitions=parameters
        )
        if not isinstance(entry.position, EntryPosition):
            raise MdbValidationError("entry position is invalid")
        if entry.position is EntryPosition.ABSOLUTE:
            if isinstance(entry.bit_offset, bool) or not isinstance(
                entry.bit_offset, int
            ):
                raise MdbValidationError(
                    "absolute entries require an integer bit_offset"
                )
            if entry.bit_offset < 0:
                raise MdbValidationError("entry bit_offset must not be negative")
        elif entry.bit_offset is not None:
            raise MdbValidationError("sequential entries cannot define bit_offset")
        return replace(entry, parameter_ref=parameter_name), parameter_name

    @staticmethod
    def _validate_container_cycles(
        containers: Mapping[QualifiedName, SequenceContainer],
    ) -> None:
        for origin in containers:
            seen: set[QualifiedName] = set()
            current: QualifiedName | None = origin
            while current is not None:
                if current in seen:
                    raise MdbValidationError(
                        f"container inheritance cycle includes {current}"
                    )
                seen.add(current)
                base = containers[current].base_container_ref
                current = QualifiedName.parse(base) if base is not None else None


@dataclass(frozen=True, slots=True)
class MissionDatabase:
    """Immutable, compiled mission database used for runtime decoding."""

    name: str
    systems: MappingProxyType[QualifiedName, SpaceSystem]
    time_epochs: MappingProxyType[QualifiedName, TimeEpochDefinition]
    parameter_types: MappingProxyType[QualifiedName, ParameterType]
    parameters: MappingProxyType[QualifiedName, ParameterDefinition]
    aliases: MappingProxyType[str, QualifiedName]
    containers: MappingProxyType[QualifiedName, SequenceContainer]
    derived_containers: MappingProxyType[QualifiedName, tuple[QualifiedName, ...]]
    max_structure_depth: int
    max_decoded_values: int

    def time_epoch(self, reference: str | QualifiedName) -> TimeEpochDefinition:
        """Look up an epoch by absolute qualified name."""
        name = QualifiedName.parse(reference)
        if name not in self.time_epochs:
            raise ReferenceResolutionError(f"unknown time epoch: {name}")
        return self.time_epochs[name]

    def parameter(self, reference: str | QualifiedName) -> ParameterDefinition:
        """Look up a parameter by absolute name or alias."""
        if isinstance(reference, str) and not reference.startswith("/"):
            if reference not in self.aliases:
                raise ReferenceResolutionError(f"unknown parameter alias: {reference}")
            name = self.aliases[reference]
        else:
            name = QualifiedName.parse(reference)
        if name not in self.parameters:
            raise ReferenceResolutionError(f"unknown parameter: {name}")
        return self.parameters[name]

    def decode(
        self,
        data: BytesLike,
        *,
        root_container: str | QualifiedName,
        context: Mapping[str, Scalar] | None = None,
    ) -> DecodedContainer:
        raw = normalize_bytes(data)
        root_name = QualifiedName.parse(root_container)
        if root_name not in self.containers:
            raise MdbDecodeError(f"unknown root container {root_name}")
        values: dict[QualifiedName, ParameterValue] = {}
        ordered: list[ParameterValue] = []
        repeated: list[RepeatedEntryValue] = []
        repeats_by_name: dict[str, RepeatedEntryValue] = {}
        budget = [0]
        context_values = context or {}
        cursor = 0
        consumed = 0

        ancestry: list[SequenceContainer] = []
        current: QualifiedName | None = root_name
        while current is not None:
            container = self.containers[current]
            ancestry.append(container)
            base = container.base_container_ref
            current = QualifiedName.parse(base) if base is not None else None
        for container in reversed(ancestry):
            cursor, consumed = self._decode_entries(
                container,
                raw,
                cursor,
                consumed,
                values,
                ordered,
                context_values,
                repeated,
                repeats_by_name,
                budget,
            )

        selected = self.containers[root_name]
        while children := self.derived_containers.get(selected.name):
            matches = [
                self.containers[name]
                for name in children
                if self._matches(self.containers[name], values, context_values)
            ]
            if not matches:
                raise NoMatchingContainerError(
                    f"no derived container matches below {selected.name}"
                )
            if len(matches) > 1:
                names = ", ".join(str(item.name) for item in matches)
                raise AmbiguousContainerError(
                    f"multiple derived containers match below {selected.name}: {names}"
                )
            selected = matches[0]
            cursor, consumed = self._decode_entries(
                selected,
                raw,
                cursor,
                consumed,
                values,
                ordered,
                context_values,
                repeated,
                repeats_by_name,
                budget,
            )
        return DecodedContainer(
            container=selected,
            parameters=tuple(ordered),
            consumed_bits=consumed,
            by_name=immutable_values(values),
            repeated_entries=tuple(repeated),
            repeats_by_name=MappingProxyType(dict(repeats_by_name)),
        )

    def _decode_entries(
        self,
        container: SequenceContainer,
        data: bytes,
        cursor: int,
        consumed: int,
        values: dict[QualifiedName, ParameterValue],
        ordered: list[ParameterValue],
        context: Mapping[str, Scalar],
        repeated: list[RepeatedEntryValue],
        repeats_by_name: dict[str, RepeatedEntryValue],
        budget: list[int],
    ) -> tuple[int, int]:
        for entry in container.entries:
            if isinstance(entry, RepeatEntry):
                count = self._resolve_dimension_value(
                    entry.count, values=values, context=context
                )
                rows: list[tuple[ParameterValue, ...]] = []
                for _ in range(count):
                    iteration_start = cursor
                    row: list[ParameterValue] = []
                    for repeated_entry in entry.entries:
                        if repeated_entry.bit_offset is not None:
                            cursor = iteration_start + repeated_entry.bit_offset
                        parameter_name = QualifiedName.parse(
                            repeated_entry.parameter_ref
                        )
                        parameter = self.parameters[parameter_name]
                        parameter_type = self.parameter_types[
                            QualifiedName.parse(parameter.type_ref)
                        ]
                        decoded, cursor = self._decode_type(
                            data,
                            cursor,
                            parameter,
                            parameter_type,
                            values,
                            context,
                            depth=0,
                            budget=budget,
                        )
                        row.append(decoded)
                        consumed = max(consumed, cursor)
                    rows.append(tuple(row))
                result = RepeatedEntryValue(entry.name, tuple(rows))
                if entry.name in repeats_by_name:
                    raise MdbDecodeError(f"duplicate repeat result name {entry.name!r}")
                repeated.append(result)
                repeats_by_name[entry.name] = result
                continue
            if entry.bit_offset is not None:
                cursor = entry.bit_offset
            parameter_name = QualifiedName.parse(entry.parameter_ref)
            parameter = self.parameters[parameter_name]
            type_name = QualifiedName.parse(parameter.type_ref)
            parameter_type = self.parameter_types[type_name]
            decoded, cursor = self._decode_type(
                data,
                cursor,
                parameter,
                parameter_type,
                values,
                context,
                depth=0,
                budget=budget,
            )
            values[parameter.name] = decoded
            ordered.append(decoded)
            consumed = max(consumed, cursor)
        return cursor, consumed

    def _decode_type(
        self,
        data: bytes,
        cursor: int,
        parameter: ParameterDefinition,
        parameter_type: ParameterType,
        values: Mapping[QualifiedName, ParameterValue],
        context: Mapping[str, Scalar],
        *,
        depth: int,
        budget: list[int],
    ) -> tuple[ParameterValue, int]:
        if depth > self.max_structure_depth:
            raise StructureLimitError(
                f"structured value exceeds depth {self.max_structure_depth}"
            )
        if isinstance(
            parameter_type, (AbsoluteTimeParameterType, RelativeTimeParameterType)
        ):
            encoding_type = self.parameter_types[
                QualifiedName.parse(parameter_type.encoding_type_ref)
            ]
            if not isinstance(
                encoding_type, (IntegerParameterType, FloatParameterType)
            ):
                raise TimeDecodeError("compiled time encoding is not numeric")
            encoded, cursor = self._decode_type(
                data,
                cursor,
                parameter,
                encoding_type,
                values,
                context,
                depth=depth,
                budget=budget,
            )
            return (
                self._wrap_time_value(
                    encoded, parameter_type, values=values, context=context
                ),
                cursor,
            )
        if isinstance(parameter_type, ArrayParameterType):
            count = self._resolve_dimension_value(
                parameter_type.element_count, values=values, context=context
            )
            element_type = self.parameter_types[
                QualifiedName.parse(parameter_type.element_type_ref)
            ]
            elements: list[ElementValue] = []
            array_raw_values: list[RuntimeRawValue] = []
            for _ in range(count):
                decoded, cursor = self._decode_type(
                    data,
                    cursor,
                    parameter,
                    element_type,
                    values,
                    context,
                    depth=depth + 1,
                    budget=budget,
                )
                elements.append(
                    ElementValue(
                        decoded.raw_value,
                        decoded.value,
                        decoded.unit,
                        decoded.is_valid,
                        decoded.alarm_severity,
                    )
                )
                array_raw_values.append(decoded.raw_value)
            array = ArrayValue(tuple(elements))
            severity = max(
                (item.alarm_severity for item in elements if item.alarm_severity),
                default=None,
            )
            decoded = ParameterValue(
                parameter,
                tuple(array_raw_values),
                array,
                None,
                all(item.is_valid for item in elements),
                severity,
            )
            return self._evaluate_structured(
                decoded, parameter_type, values, context
            ), cursor
        if isinstance(parameter_type, AggregateParameterType):
            members: list[AggregateMemberValue] = []
            aggregate_raw_values: list[RuntimeRawValue] = []
            for member in parameter_type.members:
                member_type = self.parameter_types[QualifiedName.parse(member.type_ref)]
                decoded, cursor = self._decode_type(
                    data,
                    cursor,
                    parameter,
                    member_type,
                    values,
                    context,
                    depth=depth + 1,
                    budget=budget,
                )
                members.append(
                    AggregateMemberValue(
                        member,
                        decoded.raw_value,
                        decoded.value,
                        decoded.unit,
                        decoded.is_valid,
                        decoded.alarm_severity,
                    )
                )
                aggregate_raw_values.append(decoded.raw_value)
            aggregate = AggregateValue(
                tuple(members),
                MappingProxyType({item.member.name: item for item in members}),
            )
            severity = max(
                (item.alarm_severity for item in members if item.alarm_severity),
                default=None,
            )
            decoded = ParameterValue(
                parameter,
                tuple(aggregate_raw_values),
                aggregate,
                None,
                all(item.is_valid for item in members),
                severity,
            )
            return self._evaluate_structured(
                decoded, parameter_type, values, context
            ), cursor

        budget[0] += 1
        if budget[0] > self.max_decoded_values:
            raise StructureLimitError(
                f"decoded value count exceeds {self.max_decoded_values}"
            )
        concrete_type = parameter_type
        if isinstance(parameter_type, (BinaryParameterType, StringParameterType)):
            size = self._resolve_dimension_value(
                parameter_type.size_bits, values=values, context=context
            )
            if size % 8:
                raise DynamicDimensionError(
                    f"parameter {parameter.name} size must be byte-aligned"
                )
            concrete_type = replace(parameter_type, size_bits=size)
        if (
            getattr(concrete_type, "byte_order", ByteOrder.BIG_ENDIAN)
            is ByteOrder.LITTLE_ENDIAN
            and cursor % 8
        ):
            raise MdbDecodeError(
                f"little-endian parameter {parameter.name} is not byte-aligned"
            )
        if (
            isinstance(
                concrete_type,
                (FloatParameterType, BinaryParameterType, StringParameterType),
            )
            and cursor % 8
        ):
            raise MdbDecodeError(f"parameter {parameter.name} must be byte-aligned")
        decoded, cursor = decode_parameter(
            data,
            offset=cursor,
            parameter=parameter,
            parameter_type=concrete_type,
        )
        return self._evaluate_parameter(decoded, concrete_type, values, context), cursor

    def _wrap_time_value(
        self,
        encoded: ParameterValue,
        parameter_type: AbsoluteTimeParameterType | RelativeTimeParameterType,
        *,
        values: Mapping[QualifiedName, ParameterValue],
        context: Mapping[str, Scalar],
    ) -> ParameterValue:
        numeric = encoded.value
        if isinstance(numeric, bool) or not isinstance(numeric, (int, float)):
            raise TimeDecodeError("time encoding must produce a numeric value")
        if isinstance(numeric, float) and not isfinite(numeric):
            raise TimeDecodeError("time encoding must produce a finite value")
        try:
            decimal_value = (
                Decimal(numeric) if isinstance(numeric, int) else Decimal(str(numeric))
            )
            seconds = (
                decimal_value * parameter_type.seconds_per_unit
                + parameter_type.offset_seconds
            )
        except (DecimalException, ValueError) as error:
            raise TimeDecodeError(f"cannot convert encoded time: {error}") from error
        if not seconds.is_finite():
            raise TimeDecodeError("decoded time must be finite")
        try:
            wrapper_valid = self._matches_comparisons(
                parameter_type.validity_criteria, values, context
            )
        except MdbDecodeError as error:
            raise ValidityEvaluationError(
                f"cannot evaluate validity for {encoded.parameter.name}: {error}"
            ) from error
        if isinstance(parameter_type, AbsoluteTimeParameterType):
            epoch = self.time_epochs[QualifiedName.parse(parameter_type.epoch_ref)]
            value = AbsoluteTimeValue(epoch, seconds)
        else:
            value = RelativeTimeValue(seconds)
        return ParameterValue(
            encoded.parameter,
            encoded.raw_value,
            value,
            "s",
            encoded.is_valid and wrapper_valid,
            None,
        )

    @classmethod
    def _evaluate_structured(
        cls,
        decoded: ParameterValue,
        parameter_type: ArrayParameterType | AggregateParameterType,
        values: Mapping[QualifiedName, ParameterValue],
        context: Mapping[str, Scalar],
    ) -> ParameterValue:
        try:
            own_valid = cls._matches_comparisons(
                parameter_type.validity_criteria, values, context
            )
        except MdbDecodeError as error:
            raise ValidityEvaluationError(
                f"cannot evaluate validity for {decoded.parameter.name}: {error}"
            ) from error
        is_valid = decoded.is_valid and own_valid
        return replace(
            decoded,
            is_valid=is_valid,
            alarm_severity=decoded.alarm_severity if is_valid else None,
        )

    @staticmethod
    def _resolve_dimension_value(
        dimension: int | DynamicDimension,
        *,
        values: Mapping[QualifiedName, ParameterValue],
        context: Mapping[str, Scalar],
    ) -> int:
        if isinstance(dimension, int):
            return dimension
        if isinstance(dimension.source, ParameterReference):
            name = QualifiedName.parse(dimension.source.reference)
            if name not in values:
                raise DynamicDimensionError(
                    f"dimension source parameter {name} has not been decoded"
                )
            source: object = (
                values[name].raw_value
                if dimension.use_raw_value
                else values[name].value
            )
        else:
            if dimension.source.name not in context:
                raise DynamicDimensionError(
                    f"missing dimension context {dimension.source.name!r}"
                )
            source = context[dimension.source.name]
        if isinstance(source, float) and isfinite(source) and source.is_integer():
            source = int(source)
        if isinstance(source, bool) or not isinstance(source, int):
            raise DynamicDimensionError("dimension source must be an integer")
        result = source * dimension.multiplier + dimension.offset
        if result < 0:
            raise DynamicDimensionError("resolved dimension must not be negative")
        if result > dimension.maximum:
            raise DynamicDimensionError(
                f"resolved dimension {result} exceeds maximum {dimension.maximum}"
            )
        return result

    @classmethod
    def _evaluate_parameter(
        cls,
        decoded: ParameterValue,
        parameter_type: ParameterType,
        values: Mapping[QualifiedName, ParameterValue],
        context: Mapping[str, Scalar],
    ) -> ParameterValue:
        engineering = decoded.value
        if isinstance(parameter_type, (IntegerParameterType, FloatParameterType)):
            try:
                matches = [
                    item
                    for item in parameter_type.contextual_calibrators
                    if cls._matches_comparisons(item.criteria, values, context)
                ]
            except MdbDecodeError as error:
                raise CalibrationSelectionError(
                    f"cannot select a calibrator for {decoded.parameter.name}: {error}"
                ) from error
            if len(matches) > 1:
                raise CalibrationSelectionError(
                    f"multiple contextual calibrators match {decoded.parameter.name}"
                )
            calibrator = matches[0].calibrator if matches else parameter_type.calibrator
            if calibrator is not None:
                try:
                    raw = decoded.raw_value
                    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                        raise TypeError("raw value is not numeric")
                    engineering = calibrator.calibrate(raw)
                except (ArithmeticError, TypeError, ValueError) as error:
                    raise CalibrationError(
                        f"cannot calibrate parameter {decoded.parameter.name}: {error}"
                    ) from error

        try:
            is_valid = cls._matches_comparisons(
                parameter_type.validity_criteria, values, context
            )
        except MdbDecodeError as error:
            raise ValidityEvaluationError(
                f"cannot evaluate validity for {decoded.parameter.name}: {error}"
            ) from error

        severity = None
        if is_valid:
            try:
                severity = cls._alarm_severity(parameter_type, engineering, decoded)
            except (TypeError, ValueError) as error:
                raise AlarmEvaluationError(
                    f"cannot evaluate alarms for {decoded.parameter.name}: {error}"
                ) from error
        return replace(
            decoded,
            value=engineering,
            is_valid=is_valid,
            alarm_severity=severity,
        )

    @staticmethod
    def _alarm_severity(
        parameter_type: ParameterType,
        engineering: object,
        decoded: ParameterValue,
    ) -> AlarmSeverity | None:
        if isinstance(parameter_type, (IntegerParameterType, FloatParameterType)):
            if isinstance(engineering, bool) or not isinstance(
                engineering, (int, float)
            ):
                raise TypeError("engineering value is not numeric")
            matches = (
                alarm.severity
                for alarm in parameter_type.alarm_ranges
                if _in_alarm_range(engineering, alarm)
            )
            return max(matches, default=None)
        if isinstance(parameter_type, EnumeratedParameterType):
            raw = decoded.raw_value
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise TypeError("enumerated raw value is not an integer")
            return dict(
                (alarm.raw_value, alarm.severity) for alarm in parameter_type.alarms
            ).get(raw)
        return None

    @staticmethod
    def _matches(
        container: SequenceContainer,
        values: Mapping[QualifiedName, ParameterValue],
        context: Mapping[str, Scalar],
    ) -> bool:
        return MissionDatabase._matches_comparisons(
            container.restrictions, values, context
        )

    @staticmethod
    def _matches_comparisons(
        comparisons: tuple[Comparison, ...],
        values: Mapping[QualifiedName, ParameterValue],
        context: Mapping[str, Scalar],
    ) -> bool:
        for comparison in comparisons:
            if isinstance(comparison.left, ParameterReference):
                name = QualifiedName.parse(comparison.left.reference)
                if name not in values:
                    raise MdbDecodeError(
                        f"restriction references undecoded parameter {name}"
                    )
                left: object = values[name].value
            else:
                if comparison.left.name not in context:
                    raise MdbDecodeError(
                        f"missing context value {comparison.left.name!r}"
                    )
                left = context[comparison.left.name]
            right = comparison.right
            if isinstance(left, EnumeratedValue):
                left = left.label if isinstance(right, str) else left.raw
            try:
                if not _compare(left, comparison.operator, right):
                    return False
            except TypeError as error:
                raise MdbDecodeError(
                    f"cannot compare {left!r} and {right!r}"
                ) from error
        return True


def _in_alarm_range(value: int | float, alarm: NumericAlarmRange) -> bool:
    if alarm.minimum is not None and (
        value < alarm.minimum
        or (value == alarm.minimum and not alarm.minimum_inclusive)
    ):
        return False
    return alarm.maximum is None or not (
        value > alarm.maximum
        or (value == alarm.maximum and not alarm.maximum_inclusive)
    )


def _compare(left: object, operator: ComparisonOperator, right: object) -> bool:
    if operator is ComparisonOperator.EQUAL:
        return left == right
    if operator is ComparisonOperator.NOT_EQUAL:
        return left != right
    if operator is ComparisonOperator.LESS_THAN:
        return left < right  # type: ignore[operator]
    if operator is ComparisonOperator.LESS_THAN_OR_EQUAL:
        return left <= right  # type: ignore[operator]
    if operator is ComparisonOperator.GREATER_THAN:
        return left > right  # type: ignore[operator]
    return left >= right  # type: ignore[operator]
