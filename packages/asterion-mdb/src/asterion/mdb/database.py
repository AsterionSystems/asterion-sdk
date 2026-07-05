"""Mission database compilation and telemetry decoding."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import isfinite
from types import MappingProxyType

from .decoder import BytesLike, decode_parameter, immutable_values, normalize_bytes
from .errors import (
    AlarmEvaluationError,
    AmbiguousContainerError,
    CalibrationError,
    CalibrationSelectionError,
    MdbDecodeError,
    MdbValidationError,
    NoMatchingContainerError,
    ReferenceResolutionError,
    ValidityEvaluationError,
)
from .model import (
    AlarmSeverity,
    BinaryParameterType,
    BooleanParameterType,
    ByteOrder,
    Comparison,
    ComparisonOperator,
    ContextCalibrator,
    ContextReference,
    DecodedContainer,
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
    Scalar,
    SequenceContainer,
    SpaceSystem,
    StringParameterType,
)


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

    def __init__(self, name: str) -> None:
        if not isinstance(name, str) or not name or "/" in name:
            raise MdbValidationError("database name must be one non-empty path segment")
        self.name = name
        self._systems: dict[QualifiedName, SpaceSystem] = {}
        self._types: dict[QualifiedName, ParameterType] = {}
        self._parameters: dict[QualifiedName, ParameterDefinition] = {}
        self._containers: dict[QualifiedName, SequenceContainer] = {}

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
            ),
        ):
            raise MdbValidationError("parameter type is not supported")
        self._add_unique(
            self._types, parameter_type.name, parameter_type, "parameter type"
        )

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
        types = {
            name: self._validate_type(value) for name, value in self._types.items()
        }
        parameters, aliases = self._resolve_parameters(types)
        types = self._resolve_type_evaluation(types, parameters)
        containers = self._resolve_containers(parameters)
        self._validate_container_cycles(containers)
        derived: dict[QualifiedName, list[QualifiedName]] = {}
        for container in containers.values():
            if container.base_container_ref is not None:
                base = QualifiedName.parse(container.base_container_ref)
                derived.setdefault(base, []).append(container.name)
        return MissionDatabase(
            name=self.name,
            systems=MappingProxyType(dict(self._systems)),
            parameter_types=MappingProxyType(types),
            parameters=MappingProxyType(parameters),
            aliases=MappingProxyType(aliases),
            containers=MappingProxyType(containers),
            derived_containers=MappingProxyType(
                {name: tuple(children) for name, children in derived.items()}
            ),
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
        for definitions in (self._types, self._parameters, self._containers):
            for name in definitions:
                if _owner(name) not in self._systems:
                    raise MdbValidationError(
                        f"definition {name} belongs to unknown space system {_owner(name)}"
                    )

    @staticmethod
    def _validate_type(value: ParameterType) -> ParameterType:
        if isinstance(
            value, (IntegerParameterType, BooleanParameterType, EnumeratedParameterType)
        ):
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
            if value.size_bits not in (32, 64):
                raise MdbValidationError(f"{value.name} float size must be 32 or 64")
        elif isinstance(value, (BinaryParameterType, StringParameterType)):
            if value.size_bits < 8 or value.size_bits % 8:
                raise MdbValidationError(
                    f"{value.name} size must be positive whole bytes"
                )
            if isinstance(value, StringParameterType) and not value.strip_padding:
                raise MdbValidationError(
                    f"{value.name} strip_padding must not be empty"
                )
        if isinstance(value, (IntegerParameterType, FloatParameterType)):
            MissionDatabaseBuilder._validate_numeric_evaluation(value)
        return value

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
            resolved[name] = replace(parameter_type, **changes)
        return resolved

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
            entries: list[ParameterEntry] = []
            seen: set[QualifiedName] = set()
            for entry in container.entries:
                parameter_name = _resolve_reference(
                    entry.parameter_ref, owner=owner, definitions=parameters
                )
                if parameter_name in seen:
                    raise MdbValidationError(
                        f"container {container.name} decodes {parameter_name} more than once"
                    )
                seen.add(parameter_name)
                if entry.position.value == "absolute":
                    if isinstance(entry.bit_offset, bool) or not isinstance(
                        entry.bit_offset, int
                    ):
                        raise MdbValidationError(
                            "absolute entries require an integer bit_offset"
                        )
                    if entry.bit_offset < 0:
                        raise MdbValidationError(
                            "entry bit_offset must not be negative"
                        )
                elif entry.bit_offset is not None:
                    raise MdbValidationError(
                        "sequential entries cannot define bit_offset"
                    )
                entries.append(replace(entry, parameter_ref=parameter_name))

            base = None
            if container.base_container_ref is not None:
                base = _resolve_reference(
                    container.base_container_ref,
                    owner=owner,
                    definitions=self._containers,
                )
            restrictions: list[Comparison] = []
            for comparison in container.restrictions:
                if isinstance(comparison.left, ParameterReference):
                    parameter_name = _resolve_reference(
                        comparison.left.reference,
                        owner=owner,
                        definitions=parameters,
                    )
                    comparison = replace(
                        comparison,
                        left=ParameterReference(parameter_name),
                    )
                elif not comparison.left.name:
                    raise MdbValidationError(
                        "context reference names must not be empty"
                    )
                restrictions.append(comparison)
            resolved[container.name] = replace(
                container,
                entries=tuple(entries),
                base_container_ref=base,
                restrictions=tuple(restrictions),
            )
        return resolved

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
    parameter_types: MappingProxyType[QualifiedName, ParameterType]
    parameters: MappingProxyType[QualifiedName, ParameterDefinition]
    aliases: MappingProxyType[str, QualifiedName]
    containers: MappingProxyType[QualifiedName, SequenceContainer]
    derived_containers: MappingProxyType[QualifiedName, tuple[QualifiedName, ...]]

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
            )
        return DecodedContainer(
            container=selected,
            parameters=tuple(ordered),
            consumed_bits=consumed,
            by_name=immutable_values(values),
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
    ) -> tuple[int, int]:
        for entry in container.entries:
            if entry.bit_offset is not None:
                cursor = entry.bit_offset
            parameter_name = QualifiedName.parse(entry.parameter_ref)
            parameter = self.parameters[parameter_name]
            type_name = QualifiedName.parse(parameter.type_ref)
            parameter_type = self.parameter_types[type_name]
            if (
                getattr(parameter_type, "byte_order", ByteOrder.BIG_ENDIAN)
                is ByteOrder.LITTLE_ENDIAN
                and cursor % 8
            ):
                raise MdbDecodeError(
                    f"little-endian parameter {parameter.name} is not byte-aligned"
                )
            if (
                isinstance(
                    parameter_type,
                    (FloatParameterType, BinaryParameterType, StringParameterType),
                )
                and cursor % 8
            ):
                raise MdbDecodeError(f"parameter {parameter.name} must be byte-aligned")
            decoded, cursor = decode_parameter(
                data,
                offset=cursor,
                parameter=parameter,
                parameter_type=parameter_type,
            )
            decoded = self._evaluate_parameter(decoded, parameter_type, values, context)
            values[parameter.name] = decoded
            ordered.append(decoded)
            consumed = max(consumed, cursor)
        return cursor, consumed

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
