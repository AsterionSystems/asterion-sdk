"""Bounded XTCE XML parsing and telemetry-to-MDB mapping."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, Never

from asterion.mdb import (
    AbsoluteTimeParameterType,
    AggregateMember,
    AggregateParameterType,
    AlarmSeverity,
    ArrayParameterType,
    BinaryParameterType,
    BooleanParameterType,
    ByteOrder,
    Comparison,
    ComparisonOperator,
    ContextCalibrator,
    DynamicDimension,
    EntryPosition,
    EnumeratedParameterType,
    EnumerationAlarm,
    FloatParameterType,
    IntegerParameterType,
    MdbError,
    MissionDatabase,
    MissionDatabaseBuilder,
    NumericAlarmRange,
    ParameterDefinition,
    ParameterEntry,
    ParameterReference,
    PolynomialCalibrator,
    QualifiedName,
    RelativeTimeParameterType,
    RepeatEntry,
    SequenceContainer,
    SpaceSystem,
    StringEncoding,
    StringParameterType,
    TimeEpochDefinition,
    TimeScale,
)

from .errors import (
    UnsupportedXtceFeatureError,
    XtceMappingError,
    XtceParseError,
    XtceResourceLimitError,
)

XTCE_1_3_NAMESPACE: Final = "http://www.omg.org/spec/XTCE/20250214"
XTCE_1_2_NAMESPACE: Final = "http://www.omg.org/spec/XTCE/20180204"
SUPPORTED_NAMESPACES: Final = frozenset({XTCE_1_3_NAMESPACE, XTCE_1_2_NAMESPACE})

type BytesLike = bytes | bytearray | memoryview
type XmlData = str | BytesLike


@dataclass(frozen=True, slots=True)
class XtceLoadOptions:
    max_document_bytes: int = 16 * 1024 * 1024
    max_elements: int = 250_000
    max_depth: int = 128
    max_dynamic_elements: int = 65_536
    max_repeat_count: int = 65_536
    max_dynamic_size_bits: int = 67_108_864

    def __post_init__(self) -> None:
        for name, value in (
            ("max_document_bytes", self.max_document_bytes),
            ("max_elements", self.max_elements),
            ("max_depth", self.max_depth),
            ("max_dynamic_elements", self.max_dynamic_elements),
            ("max_repeat_count", self.max_repeat_count),
            ("max_dynamic_size_bits", self.max_dynamic_size_bits),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


def load(
    path: str | os.PathLike[str], *, options: XtceLoadOptions | None = None
) -> MissionDatabase:
    """Load and compile one XTCE document from a filesystem path."""
    source = Path(path)
    source_name = str(source)
    try:
        size = source.stat().st_size
    except OSError as error:
        raise XtceParseError(str(error), source_name=source_name) from error
    limits = options or XtceLoadOptions()
    if size > limits.max_document_bytes:
        raise XtceResourceLimitError(
            f"document size {size} exceeds {limits.max_document_bytes} bytes",
            source_name=source_name,
        )
    try:
        data = source.read_bytes()
    except OSError as error:
        raise XtceParseError(str(error), source_name=source_name) from error
    return loads(data, source_name=source_name, options=limits)


def loads(
    data: XmlData,
    *,
    source_name: str = "<memory>",
    options: XtceLoadOptions | None = None,
) -> MissionDatabase:
    """Load and compile one XTCE document from text or bytes-like XML."""
    limits = options or XtceLoadOptions()
    raw = _normalize_xml(data, source_name=source_name)
    root, namespace = _parse_xml(raw, source_name=source_name, options=limits)
    mapper = _Mapper(namespace=namespace, source_name=source_name, options=limits)
    return mapper.map(root)


def _normalize_xml(data: XmlData, *, source_name: str) -> bytes:
    if isinstance(data, str):
        return data.encode("utf-8")
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise XtceParseError(
            "XML input must be str, bytes, bytearray, or memoryview",
            source_name=source_name,
        )
    try:
        return data if isinstance(data, bytes) else bytes(data)
    except (BufferError, TypeError, ValueError) as error:
        raise XtceParseError(
            f"XML input is not a usable byte buffer: {error}",
            source_name=source_name,
        ) from error


def _parse_xml(
    raw: bytes, *, source_name: str, options: XtceLoadOptions
) -> tuple[ET.Element, str]:
    if len(raw) > options.max_document_bytes:
        raise XtceResourceLimitError(
            f"document size {len(raw)} exceeds {options.max_document_bytes} bytes",
            source_name=source_name,
        )
    lowered = raw.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise XtceParseError(
            "DTD and entity declarations are not allowed", source_name=source_name
        )
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise XtceParseError(str(error), source_name=source_name) from error
    element_count = 0
    stack = [(root, 1)]
    while stack:
        element, depth = stack.pop()
        element_count += 1
        if depth > options.max_depth:
            raise XtceResourceLimitError(
                f"XML depth exceeds {options.max_depth}", source_name=source_name
            )
        if element_count > options.max_elements:
            raise XtceResourceLimitError(
                f"XML element count exceeds {options.max_elements}",
                source_name=source_name,
            )
        stack.extend((child, depth + 1) for child in element)
    namespace, local = _split_tag(root.tag)
    if local != "SpaceSystem":
        raise XtceParseError(
            "root element must be SpaceSystem",
            source_name=source_name,
            element_path="/",
        )
    if namespace not in SUPPORTED_NAMESPACES:
        raise XtceParseError(
            f"unsupported XTCE namespace {namespace!r}",
            source_name=source_name,
            element_path="/SpaceSystem",
        )
    return root, namespace


def _split_tag(tag: str) -> tuple[str, str]:
    if tag.startswith("{") and "}" in tag:
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    return "", tag


class _Mapper:
    def __init__(
        self, *, namespace: str, source_name: str, options: XtceLoadOptions
    ) -> None:
        self.namespace = namespace
        self.source_name = source_name
        self.options = options
        self.builder: MissionDatabaseBuilder | None = None

    def map(self, root: ET.Element) -> MissionDatabase:
        root_name = self._required_attr(root, "name", "/SpaceSystem")
        self.builder = MissionDatabaseBuilder(root_name)
        try:
            self._add_systems(root, parent=None, path="/SpaceSystem")
            self._map_system(root, parent=None, path="/SpaceSystem")
            return self.builder.compile()
        except (
            XtceMappingError,
            UnsupportedXtceFeatureError,
            XtceResourceLimitError,
        ):
            raise
        except MdbError as error:
            raise XtceMappingError(
                str(error), source_name=self.source_name, element_path="/SpaceSystem"
            ) from error
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise XtceMappingError(
                f"invalid XTCE value: {error}",
                source_name=self.source_name,
                element_path="/SpaceSystem",
            ) from error

    @property
    def _builder(self) -> MissionDatabaseBuilder:
        assert self.builder is not None
        return self.builder

    def _add_systems(
        self, element: ET.Element, *, parent: QualifiedName | None, path: str
    ) -> None:
        name = self._required_attr(element, "name", path)
        qualified = QualifiedName((name,)) if parent is None else parent.child(name)
        description = element.get("shortDescription")
        self._builder.add_space_system(SpaceSystem(qualified, description))
        for index, child in enumerate(self._children(element, "SpaceSystem"), 1):
            self._add_systems(
                child,
                parent=qualified,
                path=f"{path}/SpaceSystem[{index}]",
            )

    def _map_system(
        self, element: ET.Element, *, parent: QualifiedName | None, path: str
    ) -> None:
        name = self._required_attr(element, "name", path)
        owner = QualifiedName((name,)) if parent is None else parent.child(name)
        telemetry = self._child(element, "TelemetryMetaData")
        if telemetry is not None:
            self._map_telemetry(
                telemetry, owner=owner, path=f"{path}/TelemetryMetaData"
            )
        for index, child in enumerate(self._children(element, "SpaceSystem"), 1):
            self._map_system(
                child,
                parent=owner,
                path=f"{path}/SpaceSystem[{index}]",
            )

    def _map_telemetry(
        self, element: ET.Element, *, owner: QualifiedName, path: str
    ) -> None:
        type_set = self._child(element, "ParameterTypeSet")
        if type_set is not None:
            for index, item in enumerate(list(type_set), 1):
                self._map_type(
                    item, owner=owner, path=f"{path}/ParameterTypeSet/*[{index}]"
                )
        parameter_set = self._child(element, "ParameterSet")
        if parameter_set is not None:
            for index, item in enumerate(self._children(parameter_set, "Parameter"), 1):
                item_path = f"{path}/ParameterSet/Parameter[{index}]"
                name = self._required_attr(item, "name", item_path)
                type_ref = self._required_attr(item, "parameterTypeRef", item_path)
                aliases = self._aliases(item)
                self._builder.add_parameter(
                    ParameterDefinition(
                        owner.child(name),
                        type_ref,
                        item.get("shortDescription"),
                        aliases,
                    )
                )
        container_set = self._child(element, "ContainerSet")
        if container_set is not None:
            for index, item in enumerate(
                self._children(container_set, "SequenceContainer"), 1
            ):
                self._map_container(
                    item,
                    owner=owner,
                    path=f"{path}/ContainerSet/SequenceContainer[{index}]",
                )

    def _map_type(
        self, element: ET.Element, *, owner: QualifiedName, path: str
    ) -> None:
        _namespace, kind = _split_tag(element.tag)
        name = self._required_attr(element, "name", path)
        qualified = owner.child(name)
        unit = self._unit(element, path)
        if kind == "IntegerParameterType":
            encoding = self._required_child(element, "IntegerDataEncoding", path)
            size, signed, order = self._integer_encoding(encoding, path)
            self._builder.add_parameter_type(
                IntegerParameterType(
                    qualified,
                    size,
                    signed=signed,
                    byte_order=order,
                    unit=unit,
                    calibrator=self._calibrator(encoding, path),
                    contextual_calibrators=self._contextual_calibrators(encoding, path),
                    alarm_ranges=self._numeric_alarms(element, path),
                )
            )
        elif kind == "FloatParameterType":
            encoding = self._required_child(element, "FloatDataEncoding", path)
            size = self._required_int_attr(encoding, "sizeInBits", path)
            self._builder.add_parameter_type(
                FloatParameterType(
                    qualified,
                    size,
                    byte_order=self._byte_order(encoding, path),
                    unit=unit,
                    calibrator=self._calibrator(encoding, path),
                    contextual_calibrators=self._contextual_calibrators(encoding, path),
                    alarm_ranges=self._numeric_alarms(element, path),
                )
            )
        elif kind == "BooleanParameterType":
            encoding = self._required_child(element, "IntegerDataEncoding", path)
            size, _signed, order = self._integer_encoding(encoding, path)
            self._builder.add_parameter_type(
                BooleanParameterType(qualified, size, order)
            )
        elif kind == "EnumeratedParameterType":
            encoding = self._required_child(element, "IntegerDataEncoding", path)
            size, signed, order = self._integer_encoding(encoding, path)
            choices = tuple(
                (
                    self._required_int_attr(item, "value", path),
                    self._required_attr(item, "label", path),
                )
                for item in self._descendants(element, "Enumeration")
            )
            self._builder.add_parameter_type(
                EnumeratedParameterType(
                    qualified,
                    size,
                    choices,
                    signed=signed,
                    byte_order=order,
                    alarms=self._enumeration_alarms(element, choices, path),
                )
            )
        elif kind in {"BinaryParameterType", "StringParameterType"}:
            encoding_name = (
                "BinaryDataEncoding"
                if kind.startswith("Binary")
                else "StringDataEncoding"
            )
            encoding = self._required_child(element, encoding_name, path)
            size = self._encoded_size(encoding, path)
            if kind == "BinaryParameterType":
                self._builder.add_parameter_type(BinaryParameterType(qualified, size))
            else:
                charset = encoding.get("encoding", "US-ASCII").upper()
                string_encoding = (
                    StringEncoding.UTF8
                    if charset in {"UTF-8", "UTF8"}
                    else StringEncoding.ASCII
                )
                if charset not in {"UTF-8", "UTF8", "US-ASCII", "ASCII"}:
                    self._unsupported(f"string encoding {charset!r}", path)
                self._builder.add_parameter_type(
                    StringParameterType(qualified, size, string_encoding)
                )
        elif kind == "ArrayParameterType":
            self._map_array_type(element, owner=owner, name=name, path=path)
        elif kind == "AggregateParameterType":
            member_list = self._required_child(element, "MemberList", path)
            members = tuple(
                AggregateMember(
                    self._required_attr(member, "name", path),
                    self._required_attr(member, "typeRef", path),
                )
                for member in self._children(member_list, "Member")
            )
            self._builder.add_parameter_type(AggregateParameterType(qualified, members))
        elif kind in {"AbsoluteTimeParameterType", "RelativeTimeParameterType"}:
            self._map_time_type(element, owner=owner, name=name, kind=kind, path=path)
        else:
            self._unsupported(kind, path)

    def _map_array_type(
        self, element: ET.Element, *, owner: QualifiedName, name: str, path: str
    ) -> None:
        element_ref = self._required_attr(element, "arrayTypeRef", path)
        dimension_list = self._required_child(element, "DimensionList", path)
        dimensions = self._children(dimension_list, "Dimension")
        if not dimensions:
            raise XtceMappingError(
                "array type requires at least one dimension",
                source_name=self.source_name,
                element_path=path,
            )
        counts = tuple(self._array_dimension(item, path) for item in dimensions)
        current_ref: str | QualifiedName = element_ref
        for index in range(len(counts) - 1, -1, -1):
            type_name = (
                owner.child(name)
                if index == 0
                else owner.child(f"__xtce_{name}_dimension_{index}")
            )
            self._builder.add_parameter_type(
                ArrayParameterType(type_name, current_ref, counts[index])
            )
            current_ref = type_name

    def _array_dimension(
        self, element: ET.Element, path: str
    ) -> int | DynamicDimension:
        starting = self._child(element, "StartingIndex")
        if starting is not None:
            start = self._dimension_value(
                starting, maximum=self.options.max_dynamic_elements, path=path
            )
            if not isinstance(start, int) or start != 0:
                self._unsupported("nonzero or dynamic array starting index", path)
        size = self._child(element, "Size")
        if size is not None:
            return self._dimension_value(
                size, maximum=self.options.max_dynamic_elements, path=path
            )
        ending = self._required_child(element, "EndingIndex", path)
        value = self._dimension_value(
            ending, maximum=self.options.max_dynamic_elements, path=path
        )
        if isinstance(value, int):
            count = value + 1
            if count > self.options.max_dynamic_elements:
                raise XtceResourceLimitError(
                    f"array element count {count} exceeds configured maximum",
                    source_name=self.source_name,
                    element_path=path,
                )
            return count
        return replace(
            value, maximum=self.options.max_dynamic_elements, offset=value.offset + 1
        )

    def _map_time_type(
        self,
        element: ET.Element,
        *,
        owner: QualifiedName,
        name: str,
        kind: str,
        path: str,
    ) -> None:
        encoding = self._child(element, "IntegerDataEncoding")
        if encoding is None:
            encoding = self._child(element, "FloatDataEncoding")
        if encoding is None:
            wrapper = self._child(element, "Encoding")
            if wrapper is not None:
                encoding = next(iter(wrapper), None)
        if encoding is None:
            raise XtceMappingError(
                "time type requires a numeric encoding",
                source_name=self.source_name,
                element_path=path,
            )
        _, encoding_kind = _split_tag(encoding.tag)
        encoding_name = f"__xtce_{name}_encoding"
        encoding_qname = owner.child(encoding_name)
        if encoding_kind == "IntegerDataEncoding":
            size, signed, order = self._integer_encoding(encoding, path)
            self._builder.add_parameter_type(
                IntegerParameterType(encoding_qname, size, signed, order)
            )
        elif encoding_kind == "FloatDataEncoding":
            self._builder.add_parameter_type(
                FloatParameterType(
                    encoding_qname,
                    self._required_int_attr(encoding, "sizeInBits", path),
                    self._byte_order(encoding, path),
                )
            )
        else:
            self._unsupported(f"time encoding {encoding_kind}", path)
        scale = self._decimal_attr(element, "scale", Decimal(1), path)
        offset = self._decimal_attr(element, "offset", Decimal(0), path)
        time_qname = owner.child(name)
        if kind == "RelativeTimeParameterType":
            self._builder.add_parameter_type(
                RelativeTimeParameterType(time_qname, encoding_name, scale, offset)
            )
            return
        reference = self._required_child(element, "ReferenceTime", path)
        epoch_element = self._required_child(reference, "Epoch", path)
        epoch_name = (epoch_element.text or "").strip()
        origin, time_scale = self._known_epoch(epoch_name, path)
        generated_epoch = f"__xtce_{name}_epoch"
        self._builder.add_time_epoch(
            TimeEpochDefinition(owner.child(generated_epoch), origin, time_scale)
        )
        self._builder.add_parameter_type(
            AbsoluteTimeParameterType(
                time_qname, encoding_name, generated_epoch, scale, offset
            )
        )

    def _known_epoch(self, value: str, path: str) -> tuple[datetime, TimeScale]:
        epochs = {
            "UNIX": (datetime(1970, 1, 1, tzinfo=UTC), TimeScale.UTC),
            "GPS": (datetime(1980, 1, 6, tzinfo=UTC), TimeScale.GPS),
            "J2000": (datetime(2000, 1, 1, 12, tzinfo=UTC), TimeScale.TT),
            "TAI": (datetime(1958, 1, 1, tzinfo=UTC), TimeScale.TAI),
        }
        if value not in epochs:
            self._unsupported(f"time epoch {value!r}", path)
        return epochs[value]

    def _map_container(
        self, element: ET.Element, *, owner: QualifiedName, path: str
    ) -> None:
        name = self._required_attr(element, "name", path)
        entries: list[ParameterEntry | RepeatEntry] = []
        entry_list = self._child(element, "EntryList")
        if entry_list is not None:
            for index, entry in enumerate(list(entry_list), 1):
                _, kind = _split_tag(entry.tag)
                entry_path = f"{path}/EntryList/*[{index}]"
                if kind == "ParameterRefEntry":
                    entries.append(self._map_parameter_entry(entry, entry_path))
                elif kind == "RepeatEntry":
                    entries.append(self._map_repeat_entry(entry, index, entry_path))
                else:
                    self._unsupported(kind, entry_path)
        base_ref = None
        restrictions: tuple[Comparison, ...] = ()
        base = self._child(element, "BaseContainer")
        if base is not None:
            base_ref = self._required_attr(base, "containerRef", path)
            criteria = self._child(base, "RestrictionCriteria")
            if criteria is not None:
                restrictions = self._comparisons(criteria, path)
        self._builder.add_container(
            SequenceContainer(owner.child(name), tuple(entries), base_ref, restrictions)
        )

    def _map_parameter_entry(self, entry: ET.Element, path: str) -> ParameterEntry:
        parameter_ref = self._required_attr(entry, "parameterRef", path)
        location = self._child(entry, "LocationInContainerInBits")
        if location is None:
            return ParameterEntry(parameter_ref)
        fixed = self._required_child(location, "FixedValue", path)
        offset = self._element_int(fixed, path)
        reference = location.get("referenceLocation", "previousEntry")
        if reference == "containerStart":
            return ParameterEntry(parameter_ref, EntryPosition.ABSOLUTE, offset)
        if reference == "previousEntry" and offset == 0:
            return ParameterEntry(parameter_ref)
        self._unsupported("relative entry offsets", path)

    def _map_repeat_entry(
        self, entry: ET.Element, index: int, path: str
    ) -> RepeatEntry:
        count_element = self._required_child(entry, "Count", path)
        count = self._dimension_value(
            count_element, maximum=self.options.max_repeat_count, path=path
        )
        entry_list = self._required_child(entry, "EntryList", path)
        repeated: list[ParameterEntry] = []
        for child_index, child in enumerate(list(entry_list), 1):
            _, kind = _split_tag(child.tag)
            child_path = f"{path}/EntryList/*[{child_index}]"
            if kind != "ParameterRefEntry":
                self._unsupported("nested or non-parameter repeat entry", child_path)
            repeated.append(self._map_parameter_entry(child, child_path))
        name = entry.get("name", f"repeat_{index}")
        return RepeatEntry(name, tuple(repeated), count)

    def _comparisons(self, element: ET.Element, path: str) -> tuple[Comparison, ...]:
        result: list[Comparison] = []
        for comparison in self._descendants(element, "Comparison"):
            parameter_ref = self._required_attr(comparison, "parameterRef", path)
            operator_text = comparison.get("comparisonOperator", "==")
            operators = {
                "==": ComparisonOperator.EQUAL,
                "!=": ComparisonOperator.NOT_EQUAL,
                "<": ComparisonOperator.LESS_THAN,
                "<=": ComparisonOperator.LESS_THAN_OR_EQUAL,
                ">": ComparisonOperator.GREATER_THAN,
                ">=": ComparisonOperator.GREATER_THAN_OR_EQUAL,
            }
            if operator_text not in operators:
                self._unsupported(f"comparison operator {operator_text!r}", path)
            raw_value = self._required_attr(comparison, "value", path)
            value: int | float | bool | str
            if raw_value.lower() in {"true", "false"}:
                value = raw_value.lower() == "true"
            else:
                try:
                    value = int(raw_value, 0)
                except ValueError:
                    try:
                        value = float(raw_value)
                    except ValueError:
                        value = raw_value
            result.append(
                Comparison(
                    ParameterReference(parameter_ref), operators[operator_text], value
                )
            )
        return tuple(result)

    def _integer_encoding(
        self, element: ET.Element, path: str
    ) -> tuple[int, bool, ByteOrder]:
        size = self._required_int_attr(element, "sizeInBits", path)
        encoding = element.get("encoding", "unsigned").lower()
        if encoding not in {"unsigned", "twoscomplement", "signmagnitude"}:
            self._unsupported(f"integer encoding {encoding!r}", path)
        if encoding == "signmagnitude":
            self._unsupported("sign-magnitude integers", path)
        return size, encoding == "twoscomplement", self._byte_order(element, path)

    def _byte_order(self, element: ET.Element, path: str) -> ByteOrder:
        value = element.get("byteOrder", "mostSignificantByteFirst")
        if value == "leastSignificantByteFirst":
            return ByteOrder.LITTLE_ENDIAN
        if value == "mostSignificantByteFirst":
            return ByteOrder.BIG_ENDIAN
        self._unsupported(f"byte order {value!r}", path)

    def _encoded_size(self, element: ET.Element, path: str) -> int | DynamicDimension:
        if "sizeInBits" in element.attrib:
            return self._required_int_attr(element, "sizeInBits", path)
        size = self._child(element, "SizeInBits")
        if size is None:
            raise XtceMappingError(
                "fixed size is required",
                source_name=self.source_name,
                element_path=path,
            )
        return self._dimension_value(
            size, maximum=self.options.max_dynamic_size_bits, path=path
        )

    def _dimension_value(
        self, element: ET.Element, *, maximum: int, path: str
    ) -> int | DynamicDimension:
        _, local = _split_tag(element.tag)
        fixed = element if local == "FixedValue" else self._child(element, "FixedValue")
        if fixed is not None:
            value = self._element_int(fixed, path)
            if value < 0:
                raise XtceMappingError(
                    "dimension must not be negative",
                    source_name=self.source_name,
                    element_path=path,
                )
            if value > maximum:
                raise XtceResourceLimitError(
                    f"dimension {value} exceeds configured maximum {maximum}",
                    source_name=self.source_name,
                    element_path=path,
                )
            return value
        dynamic = (
            element if local == "DynamicValue" else self._child(element, "DynamicValue")
        )
        if dynamic is None:
            self._unsupported("non-fixed/non-dynamic dimension", path)
        reference = next(iter(self._descendants(dynamic, "ParameterInstanceRef")), None)
        if reference is None:
            self._unsupported("dimension without ParameterInstanceRef", path)
        parameter_ref = self._required_attr(reference, "parameterRef", path)
        calibrated_text = reference.get("useCalibratedValue", "true").lower()
        if calibrated_text not in {"true", "false"}:
            raise XtceMappingError(
                "useCalibratedValue must be true or false",
                source_name=self.source_name,
                element_path=path,
            )
        multiplier = 1
        offset = 0
        adjustment = next(iter(self._descendants(dynamic, "LinearAdjustment")), None)
        if adjustment is not None:
            multiplier = self._integer_adjustment(adjustment, "slope", 1, path)
            offset = self._integer_adjustment(adjustment, "intercept", 0, path)
        return DynamicDimension(
            ParameterReference(parameter_ref),
            maximum,
            multiplier,
            offset,
            use_raw_value=calibrated_text == "false",
        )

    def _integer_adjustment(
        self, element: ET.Element, name: str, default: int, path: str
    ) -> int:
        value = element.get(name)
        if value is None:
            return default
        try:
            decimal_value = Decimal(value)
        except InvalidOperation as error:
            raise XtceMappingError(
                f"linear adjustment {name} must be numeric",
                source_name=self.source_name,
                element_path=path,
            ) from error
        if decimal_value != decimal_value.to_integral_value():
            self._unsupported("noninteger dynamic linear adjustment", path)
        return int(decimal_value)

    def _calibrator(
        self, encoding: ET.Element, path: str
    ) -> PolynomialCalibrator | None:
        default = self._child(encoding, "DefaultCalibrator")
        if default is None:
            return None
        polynomial = self._child(default, "PolynomialCalibrator")
        if polynomial is None:
            self._unsupported("non-polynomial calibrator", path)
        return self._polynomial(polynomial, path)

    def _polynomial(self, polynomial: ET.Element, path: str) -> PolynomialCalibrator:
        terms: dict[int, float] = {}
        for term in self._children(polynomial, "Term"):
            exponent = self._required_int_attr(term, "exponent", path)
            coefficient = float(self._required_attr(term, "coefficient", path))
            terms[exponent] = coefficient
        if not terms:
            raise XtceMappingError(
                "polynomial calibrator requires terms",
                source_name=self.source_name,
                element_path=path,
            )
        return PolynomialCalibrator(
            tuple(terms.get(power, 0.0) for power in range(max(terms) + 1))
        )

    def _contextual_calibrators(
        self, encoding: ET.Element, path: str
    ) -> tuple[ContextCalibrator, ...]:
        calibrator_list = self._child(encoding, "ContextCalibratorList")
        if calibrator_list is None:
            return ()
        result: list[ContextCalibrator] = []
        for item in self._children(calibrator_list, "ContextCalibrator"):
            match = self._required_child(item, "ContextMatch", path)
            criteria = self._comparisons(match, path)
            calibrator = self._required_child(item, "Calibrator", path)
            polynomial = self._child(calibrator, "PolynomialCalibrator")
            if polynomial is None:
                self._unsupported("non-polynomial contextual calibrator", path)
            result.append(
                ContextCalibrator(criteria, self._polynomial(polynomial, path))
            )
        return tuple(result)

    def _numeric_alarms(
        self, element: ET.Element, path: str
    ) -> tuple[NumericAlarmRange, ...]:
        result: list[NumericAlarmRange] = []
        severity_names = {
            "WatchRange": AlarmSeverity.WATCH,
            "WarningRange": AlarmSeverity.WARNING,
            "DistressRange": AlarmSeverity.DISTRESS,
            "CriticalRange": AlarmSeverity.CRITICAL,
            "SevereRange": AlarmSeverity.SEVERE,
        }
        for item in element.iter():
            _, local = _split_tag(item.tag)
            if local not in severity_names:
                continue
            minimum, minimum_inclusive = self._bound(item, "min")
            maximum, maximum_inclusive = self._bound(item, "max")
            result.append(
                NumericAlarmRange(
                    severity_names[local],
                    minimum,
                    maximum,
                    minimum_inclusive,
                    maximum_inclusive,
                )
            )
        return tuple(result)

    @staticmethod
    def _bound(element: ET.Element, prefix: str) -> tuple[float | None, bool]:
        inclusive = element.get(prefix + "Inclusive")
        if inclusive is not None:
            return float(inclusive), True
        exclusive = element.get(prefix + "Exclusive")
        if exclusive is not None:
            return float(exclusive), False
        return None, True

    def _enumeration_alarms(
        self,
        element: ET.Element,
        choices: tuple[tuple[int, str], ...],
        path: str,
    ) -> tuple[EnumerationAlarm, ...]:
        labels = {label: raw for raw, label in choices}
        result: list[EnumerationAlarm] = []
        severity = {item.name.lower(): item for item in AlarmSeverity}
        for alarm in self._descendants(element, "EnumerationAlarm"):
            label = self._required_attr(alarm, "enumerationLabel", path)
            level = self._required_attr(alarm, "alarmLevel", path).lower()
            if label not in labels or level not in severity:
                raise XtceMappingError(
                    "enumeration alarm references an unknown label or severity",
                    source_name=self.source_name,
                    element_path=path,
                )
            result.append(EnumerationAlarm(labels[label], severity[level]))
        return tuple(result)

    def _unit(self, element: ET.Element, path: str) -> str | None:
        unit_set = self._child(element, "UnitSet")
        if unit_set is None:
            return None
        units = self._children(unit_set, "Unit")
        if not units:
            return None
        if len(units) != 1:
            self._unsupported("compound units", path)
        unit = units[0]
        if any(
            unit.get(name, default) != default
            for name, default in (("power", "1"), ("factor", "1"), ("offset", "0"))
        ):
            self._unsupported("scaled or offset units", path)
        return (unit.text or "").strip() or None

    def _aliases(self, element: ET.Element) -> tuple[str, ...]:
        result: list[str] = []
        alias_set = self._child(element, "AliasSet")
        if alias_set is None:
            return ()
        for alias in self._children(alias_set, "Alias"):
            value = alias.get("alias")
            if value:
                namespace = alias.get("nameSpace")
                result.append(f"{namespace}:{value}" if namespace else value)
        return tuple(result)

    def _decimal_attr(
        self, element: ET.Element, name: str, default: Decimal, path: str
    ) -> Decimal:
        value = element.get(name)
        if value is None:
            return default
        try:
            return Decimal(value)
        except InvalidOperation as error:
            raise XtceMappingError(
                f"attribute {name!r} must be decimal",
                source_name=self.source_name,
                element_path=path,
            ) from error

    def _required_attr(self, element: ET.Element, name: str, path: str) -> str:
        value = element.get(name)
        if value is None or not value:
            raise XtceMappingError(
                f"required attribute {name!r} is missing",
                source_name=self.source_name,
                element_path=path,
            )
        return value

    def _required_int_attr(self, element: ET.Element, name: str, path: str) -> int:
        value = self._required_attr(element, name, path)
        try:
            return int(value, 0)
        except ValueError as error:
            raise XtceMappingError(
                f"attribute {name!r} must be an integer",
                source_name=self.source_name,
                element_path=path,
            ) from error

    def _element_int(self, element: ET.Element, path: str) -> int:
        try:
            return int((element.text or "").strip(), 0)
        except ValueError as error:
            raise XtceMappingError(
                "element value must be an integer",
                source_name=self.source_name,
                element_path=path,
            ) from error

    def _unsupported(self, feature: str, path: str) -> Never:
        raise UnsupportedXtceFeatureError(
            f"unsupported XTCE feature: {feature}",
            source_name=self.source_name,
            element_path=path,
        )

    def _tag(self, local: str) -> str:
        return f"{{{self.namespace}}}{local}"

    def _child(self, element: ET.Element, local: str) -> ET.Element | None:
        return element.find(self._tag(local))

    def _required_child(self, element: ET.Element, local: str, path: str) -> ET.Element:
        child = self._child(element, local)
        if child is None:
            raise XtceMappingError(
                f"required element {local!r} is missing",
                source_name=self.source_name,
                element_path=path,
            )
        return child

    def _children(self, element: ET.Element, local: str) -> list[ET.Element]:
        return list(element.findall(self._tag(local)))

    def _descendants(self, element: ET.Element, local: str) -> list[ET.Element]:
        return list(element.iter(self._tag(local)))
