from decimal import Decimal

import pytest
from asterion.mdb import (
    ArrayParameterType,
    ArrayValue,
    InsufficientDataError,
    IntegerParameterType,
    MissionDatabaseBuilder,
    ParameterDefinition,
    ParameterEntry,
    QualifiedName,
    RelativeTimeParameterType,
    RelativeTimeValue,
    RepeatEntry,
    SequenceContainer,
    SpaceSystem,
)
from asterion.mdb.decoder import extract_integer
from asterion.mdb.model import ByteOrder
from hypothesis import given, settings
from hypothesis import strategies as st


@settings(max_examples=200, derandomize=True, deadline=None)
@given(
    value=st.integers(min_value=0, max_value=(1 << 32) - 1),
    size=st.integers(min_value=1, max_value=32),
    prefix=st.integers(min_value=0, max_value=7),
)
def test_big_endian_integer_extraction(value: int, size: int, prefix: int) -> None:
    value &= (1 << size) - 1
    total_bits = prefix + size
    byte_count = (total_bits + 7) // 8
    shift = byte_count * 8 - total_bits
    encoded = (value << shift).to_bytes(byte_count, "big")

    assert (
        extract_integer(
            encoded,
            offset=prefix,
            size=size,
            signed=False,
            byte_order=ByteOrder.BIG_ENDIAN,
        )
        == value
    )


@settings(max_examples=100, derandomize=True, deadline=None)
@given(values=st.lists(st.integers(min_value=0, max_value=255), max_size=32))
def test_fixed_array_round_trip_and_truncation(values: list[int]) -> None:
    qname = QualifiedName.parse
    builder = MissionDatabaseBuilder("property")
    builder.add_space_system(SpaceSystem(qname("/Satellite")))
    builder.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    builder.add_parameter_type(
        ArrayParameterType(qname("/Satellite/array_t"), "u8", len(values))
    )
    builder.add_parameter(ParameterDefinition(qname("/Satellite/array"), "array_t"))
    builder.add_container(
        SequenceContainer(qname("/Satellite/root"), (ParameterEntry("array"),))
    )
    database = builder.compile()

    decoded = database.decode(bytes(values), root_container="/Satellite/root")
    array = decoded.parameters[0].value
    assert isinstance(array, ArrayValue)
    assert [item.value for item in array] == values
    if values:
        with pytest.raises(InsufficientDataError):
            database.decode(bytes(values[:-1]), root_container="/Satellite/root")


@settings(max_examples=100, derandomize=True, deadline=None)
@given(values=st.lists(st.integers(min_value=0, max_value=255), max_size=32))
def test_bounded_repeat_round_trip(values: list[int]) -> None:
    qname = QualifiedName.parse
    builder = MissionDatabaseBuilder("property")
    builder.add_space_system(SpaceSystem(qname("/Satellite")))
    builder.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    builder.add_parameter(ParameterDefinition(qname("/Satellite/value"), "u8"))
    builder.add_container(
        SequenceContainer(
            qname("/Satellite/root"),
            (RepeatEntry("values", (ParameterEntry("value"),), len(values)),),
        )
    )

    decoded = builder.compile().decode(bytes(values), root_container="/Satellite/root")

    assert [row[0].value for row in decoded.repeats_by_name["values"].rows] == values


@settings(max_examples=100, derandomize=True, deadline=None)
@given(
    left=st.integers(min_value=-(1 << 62), max_value=(1 << 62) - 1),
    right=st.integers(min_value=-(1 << 62), max_value=(1 << 62) - 1),
)
def test_relative_time_arithmetic_is_exact(left: int, right: int) -> None:
    first = RelativeTimeValue(Decimal(left))
    second = RelativeTimeValue(Decimal(right))

    assert first + second == RelativeTimeValue(Decimal(left + right))
    assert first - second == RelativeTimeValue(Decimal(left - right))


@settings(max_examples=100, derandomize=True, deadline=None)
@given(value=st.integers(min_value=-(1 << 63), max_value=(1 << 63) - 1))
def test_integer_derived_time_round_trip_is_exact(value: int) -> None:
    qname = QualifiedName.parse
    builder = MissionDatabaseBuilder("property")
    builder.add_space_system(SpaceSystem(qname("/Satellite")))
    builder.add_parameter_type(
        IntegerParameterType(qname("/Satellite/i64"), 64, signed=True)
    )
    builder.add_parameter_type(
        RelativeTimeParameterType(qname("/Satellite/time_t"), "i64")
    )
    builder.add_parameter(ParameterDefinition(qname("/Satellite/time"), "time_t"))
    builder.add_container(
        SequenceContainer(qname("/Satellite/root"), (ParameterEntry("time"),))
    )

    decoded = builder.compile().decode(
        value.to_bytes(8, "big", signed=True), root_container="/Satellite/root"
    )

    assert decoded.parameters[0].value == RelativeTimeValue(Decimal(value))
