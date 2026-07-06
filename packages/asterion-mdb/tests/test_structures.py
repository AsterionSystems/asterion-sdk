from types import MappingProxyType

import pytest
from asterion.mdb import (
    AggregateMember,
    AggregateParameterType,
    AggregateValue,
    AlarmSeverity,
    ArrayParameterType,
    ArrayValue,
    BinaryParameterType,
    Comparison,
    ComparisonOperator,
    ContextReference,
    DynamicDimension,
    DynamicDimensionError,
    EntryPosition,
    IntegerParameterType,
    MdbValidationError,
    MissionDatabaseBuilder,
    NumericAlarmRange,
    ParameterDefinition,
    ParameterEntry,
    ParameterReference,
    PolynomialCalibrator,
    QualifiedName,
    RepeatEntry,
    SequenceContainer,
    SpaceSystem,
    StringParameterType,
    StructureLimitError,
)


def qname(value: str) -> QualifiedName:
    return QualifiedName.parse(value)


def builder(**kwargs: int) -> MissionDatabaseBuilder:
    result = MissionDatabaseBuilder("mission", **kwargs)
    result.add_space_system(SpaceSystem(qname("/Satellite")))
    return result


def add_parameter(target: MissionDatabaseBuilder, name: str, type_name: str) -> None:
    target.add_parameter(ParameterDefinition(qname(f"/Satellite/{name}"), type_name))


def test_parameter_sized_binary_and_context_sized_string() -> None:
    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    target.add_parameter_type(
        BinaryParameterType(
            qname("/Satellite/blob_t"),
            DynamicDimension(ParameterReference("length"), 32, multiplier=8),
        )
    )
    target.add_parameter_type(
        StringParameterType(
            qname("/Satellite/text_t"),
            DynamicDimension(ContextReference("text.bytes"), 32, multiplier=8),
        )
    )
    for name, type_name in (("length", "u8"), ("blob", "blob_t"), ("text", "text_t")):
        add_parameter(target, name, type_name)
    target.add_container(
        SequenceContainer(
            qname("/Satellite/root"),
            tuple(ParameterEntry(name) for name in ("length", "blob", "text")),
        )
    )

    result = target.compile().decode(
        b"\x02\xaa\xbbHi", root_container="/Satellite/root", context={"text.bytes": 2}
    )

    assert result.parameters[1].value == b"\xaa\xbb"
    assert result.parameters[2].value == "Hi"


def test_scalar_array_preserves_leaf_evaluation() -> None:
    target = builder()
    target.add_parameter_type(
        IntegerParameterType(
            qname("/Satellite/temperature_t"),
            8,
            unit="degC",
            calibrator=PolynomialCalibrator((-10.0, 0.5)),
            alarm_ranges=(NumericAlarmRange(AlarmSeverity.WARNING, minimum=10),),
        )
    )
    target.add_parameter_type(
        ArrayParameterType(qname("/Satellite/temperatures_t"), "temperature_t", 3)
    )
    add_parameter(target, "temperatures", "temperatures_t")
    target.add_container(
        SequenceContainer(qname("/Satellite/root"), (ParameterEntry("temperatures"),))
    )

    value = (
        target.compile()
        .decode(b"\x00\x28\x50", root_container="/Satellite/root")
        .parameters[0]
    )

    assert isinstance(value.value, ArrayValue)
    assert [element.value for element in value.value] == [-10.0, 10.0, 30.0]
    assert value.value.elements[1].unit == "degC"
    assert value.value.elements[2].alarm_severity is AlarmSeverity.WARNING
    assert value.alarm_severity is AlarmSeverity.WARNING


def test_dynamic_array_of_nested_aggregates() -> None:
    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    target.add_parameter_type(
        AggregateParameterType(
            qname("/Satellite/pair_t"),
            (AggregateMember("x", "u8"), AggregateMember("y", "u8")),
        )
    )
    target.add_parameter_type(
        AggregateParameterType(
            qname("/Satellite/wrapped_t"), (AggregateMember("pair", "pair_t"),)
        )
    )
    target.add_parameter_type(
        ArrayParameterType(
            qname("/Satellite/pairs_t"),
            "wrapped_t",
            DynamicDimension(ParameterReference("count"), 4),
        )
    )
    add_parameter(target, "count", "u8")
    add_parameter(target, "pairs", "pairs_t")
    target.add_container(
        SequenceContainer(
            qname("/Satellite/root"),
            (ParameterEntry("count"), ParameterEntry("pairs")),
        )
    )

    value = (
        target.compile()
        .decode(b"\x02\x01\x02\x03\x04", root_container="/Satellite/root")
        .parameters[1]
        .value
    )

    assert isinstance(value, ArrayValue)
    first = value.elements[0].value
    assert isinstance(first, AggregateValue)
    nested = first["pair"].value
    assert isinstance(nested, AggregateValue)
    assert nested["x"].value == 1
    assert nested["y"].value == 2
    assert isinstance(first.by_name, MappingProxyType)


def test_aggregate_members_preserve_calibration_validity_and_alarms() -> None:
    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    target.add_parameter_type(
        IntegerParameterType(
            qname("/Satellite/scaled_t"),
            8,
            calibrator=PolynomialCalibrator((0.0, 2.0)),
            validity_criteria=(
                Comparison(ParameterReference("mode"), ComparisonOperator.EQUAL, 1),
            ),
            alarm_ranges=(NumericAlarmRange(AlarmSeverity.CRITICAL, minimum=10),),
        )
    )
    target.add_parameter_type(
        AggregateParameterType(
            qname("/Satellite/record_t"),
            (AggregateMember("reading", "scaled_t"),),
        )
    )
    add_parameter(target, "mode", "u8")
    add_parameter(target, "record", "record_t")
    target.add_container(
        SequenceContainer(
            qname("/Satellite/root"),
            (ParameterEntry("mode"), ParameterEntry("record")),
        )
    )

    valid = (
        target.compile()
        .decode(b"\x01\x06", root_container="/Satellite/root")
        .parameters[1]
    )
    invalid = (
        target.compile()
        .decode(b"\x00\x06", root_container="/Satellite/root")
        .parameters[1]
    )

    assert isinstance(valid.value, AggregateValue)
    assert valid.value["reading"].value == 12.0
    assert valid.value["reading"].alarm_severity is AlarmSeverity.CRITICAL
    assert valid.alarm_severity is AlarmSeverity.CRITICAL
    assert invalid.is_valid is False
    assert invalid.alarm_severity is None


def test_empty_array_and_empty_dynamic_binary() -> None:
    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    target.add_parameter_type(ArrayParameterType(qname("/Satellite/empty_t"), "u8", 0))
    target.add_parameter_type(
        BinaryParameterType(
            qname("/Satellite/blob_t"),
            DynamicDimension(ContextReference("bits"), 8),
        )
    )
    add_parameter(target, "empty", "empty_t")
    add_parameter(target, "blob", "blob_t")
    target.add_container(
        SequenceContainer(
            qname("/Satellite/root"),
            (ParameterEntry("empty"), ParameterEntry("blob")),
        )
    )

    result = target.compile().decode(
        b"", root_container="/Satellite/root", context={"bits": 0}
    )

    assert len(result.parameters[0].value) == 0  # type: ignore[arg-type]
    assert result.parameters[1].value == b""
    assert result.consumed_bits == 0


def repeat_database(*, count: int | DynamicDimension = 2):
    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    for name in ("a", "b", "tail"):
        add_parameter(target, name, "u8")
    target.add_container(
        SequenceContainer(
            qname("/Satellite/root"),
            (
                RepeatEntry(
                    "samples", (ParameterEntry("a"), ParameterEntry("b")), count
                ),
                ParameterEntry("tail"),
            ),
        )
    )
    return target.compile()


def test_repeat_groups_are_grouped_and_excluded_from_scalar_lookup() -> None:
    result = repeat_database().decode(
        b"\x01\x02\x03\x04\xff", root_container="/Satellite/root"
    )

    repeated = result.repeats_by_name["samples"]
    assert [[value.value for value in row] for row in repeated.rows] == [
        [1, 2],
        [3, 4],
    ]
    assert len(repeated) == 2
    assert result.parameters[0].value == 255
    assert qname("/Satellite/a") not in result.by_name
    assert isinstance(result.repeats_by_name, MappingProxyType)


def test_context_sized_repeat_group() -> None:
    database = repeat_database(
        count=DynamicDimension(ContextReference("rows"), maximum=3)
    )

    result = database.decode(
        b"\x01\x02\xff",
        root_container="/Satellite/root",
        context={"rows": 1},
    )

    assert len(result.repeats_by_name["samples"]) == 1
    assert result.parameters[0].value == 255


def test_repeat_absolute_offsets_are_relative_to_each_iteration() -> None:
    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    add_parameter(target, "a", "u8")
    add_parameter(target, "b", "u8")
    target.add_container(
        SequenceContainer(
            qname("/Satellite/root"),
            (
                RepeatEntry(
                    "rows",
                    (
                        ParameterEntry("a"),
                        ParameterEntry("b", EntryPosition.ABSOLUTE, 16),
                    ),
                    2,
                ),
            ),
        )
    )

    result = target.compile().decode(
        b"\x01\x00\x02\x03\x00\x04", root_container="/Satellite/root"
    )

    assert [
        [value.value for value in row] for row in result.repeated_entries[0].rows
    ] == [
        [1, 2],
        [3, 4],
    ]
    assert result.consumed_bits == 48


@pytest.mark.parametrize(
    ("dimension", "context", "message"),
    [
        (DynamicDimension(ContextReference("count"), 4), {}, "missing"),
        (DynamicDimension(ContextReference("count"), 4), {"count": True}, "integer"),
        (DynamicDimension(ContextReference("count"), 4), {"count": 5}, "exceeds"),
        (
            DynamicDimension(ContextReference("count"), 4, offset=-2),
            {"count": 1},
            "negative",
        ),
    ],
)
def test_dynamic_dimension_runtime_failures(
    dimension: DynamicDimension, context: dict[str, int | bool], message: str
) -> None:
    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    target.add_parameter_type(
        ArrayParameterType(qname("/Satellite/array_t"), "u8", dimension)
    )
    add_parameter(target, "array", "array_t")
    target.add_container(
        SequenceContainer(qname("/Satellite/root"), (ParameterEntry("array"),))
    )

    with pytest.raises(DynamicDimensionError, match=message):
        target.compile().decode(
            b"\x00" * 5, root_container="/Satellite/root", context=context
        )


def test_parameter_dimension_must_be_decoded_first() -> None:
    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    target.add_parameter_type(
        ArrayParameterType(
            qname("/Satellite/array_t"),
            "u8",
            DynamicDimension(ParameterReference("count"), 4),
        )
    )
    add_parameter(target, "array", "array_t")
    add_parameter(target, "count", "u8")
    target.add_container(
        SequenceContainer(
            qname("/Satellite/root"),
            (ParameterEntry("array"), ParameterEntry("count")),
        )
    )

    with pytest.raises(DynamicDimensionError, match="has not been decoded"):
        target.compile().decode(b"\x01", root_container="/Satellite/root")


def test_dynamic_octet_data_requires_byte_alignment() -> None:
    target = builder()
    target.add_parameter_type(
        BinaryParameterType(
            qname("/Satellite/blob_t"),
            DynamicDimension(ContextReference("bits"), 16),
        )
    )
    add_parameter(target, "blob", "blob_t")
    target.add_container(
        SequenceContainer(qname("/Satellite/root"), (ParameterEntry("blob"),))
    )

    with pytest.raises(DynamicDimensionError, match="byte-aligned"):
        target.compile().decode(
            b"\x00", root_container="/Satellite/root", context={"bits": 7}
        )


def test_decoded_value_limit_applies_across_structures_and_repeats() -> None:
    target = builder(max_decoded_values=2)
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    target.add_parameter_type(ArrayParameterType(qname("/Satellite/array_t"), "u8", 3))
    add_parameter(target, "array", "array_t")
    target.add_container(
        SequenceContainer(qname("/Satellite/root"), (ParameterEntry("array"),))
    )

    with pytest.raises(StructureLimitError, match="value count"):
        target.compile().decode(b"\x01\x02\x03", root_container="/Satellite/root")

    target = builder(max_decoded_values=3)
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    add_parameter(target, "a", "u8")
    add_parameter(target, "b", "u8")
    target.add_container(
        SequenceContainer(
            qname("/Satellite/root"),
            (RepeatEntry("rows", (ParameterEntry("a"), ParameterEntry("b")), 2),),
        )
    )
    with pytest.raises(StructureLimitError, match="value count"):
        target.compile().decode(b"\x01\x02\x03\x04", root_container="/Satellite/root")


def test_structured_type_cycles_and_depth_are_rejected() -> None:
    cyclic = builder()
    cyclic.add_parameter_type(
        AggregateParameterType(qname("/Satellite/a_t"), (AggregateMember("b", "b_t"),))
    )
    cyclic.add_parameter_type(
        AggregateParameterType(qname("/Satellite/b_t"), (AggregateMember("a", "a_t"),))
    )
    with pytest.raises(MdbValidationError, match="cycle"):
        cyclic.compile()

    shallow = builder(max_structure_depth=1)
    shallow.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    shallow.add_parameter_type(ArrayParameterType(qname("/Satellite/inner_t"), "u8", 1))
    shallow.add_parameter_type(
        ArrayParameterType(qname("/Satellite/outer_t"), "inner_t", 1)
    )
    with pytest.raises(MdbValidationError, match="nesting"):
        shallow.compile()


@pytest.mark.parametrize(
    "parameter_type",
    [
        ArrayParameterType(qname("/Satellite/a_t"), "missing", 1),
        AggregateParameterType(
            qname("/Satellite/a_t"),
            (AggregateMember("same", "/Satellite/a_t"),),
        ),
        AggregateParameterType(
            qname("/Satellite/a_t"),
            (AggregateMember("x", "missing"),),
        ),
    ],
)
def test_invalid_structured_references(parameter_type: object) -> None:
    target = builder()
    target.add_parameter_type(parameter_type)  # type: ignore[arg-type]
    with pytest.raises(MdbValidationError):
        target.compile()


def test_duplicate_structure_and_repeat_names_are_rejected() -> None:
    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    target.add_parameter_type(
        AggregateParameterType(
            qname("/Satellite/bad_t"),
            (AggregateMember("x", "u8"), AggregateMember("x", "u8")),
        )
    )
    with pytest.raises(MdbValidationError, match="duplicate aggregate"):
        target.compile()

    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    add_parameter(target, "value", "u8")
    repeat = RepeatEntry("rows", (ParameterEntry("value"),), 1)
    target.add_container(SequenceContainer(qname("/Satellite/root"), (repeat, repeat)))
    with pytest.raises(MdbValidationError, match="duplicate repeat"):
        target.compile()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_structure_depth": 0},
        {"max_structure_depth": True},
        {"max_decoded_values": 0},
    ],
)
def test_invalid_database_limits(kwargs: dict[str, int]) -> None:
    with pytest.raises(MdbValidationError, match="positive integer"):
        builder(**kwargs)


@pytest.mark.parametrize(
    "dimension",
    [
        DynamicDimension(ContextReference("count"), 0),
        DynamicDimension(ContextReference("count"), 4, multiplier=True),
    ],
)
def test_invalid_dynamic_dimension_definitions(
    dimension: DynamicDimension,
) -> None:
    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    target.add_parameter_type(
        ArrayParameterType(qname("/Satellite/array_t"), "u8", dimension)
    )

    with pytest.raises(MdbValidationError):
        target.compile()
