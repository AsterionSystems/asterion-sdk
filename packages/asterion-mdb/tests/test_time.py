import struct
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType

import pytest
from asterion.mdb import (
    AbsoluteTimeParameterType,
    AbsoluteTimeValue,
    AggregateMember,
    AggregateParameterType,
    AggregateValue,
    AlarmSeverity,
    ArrayParameterType,
    ArrayValue,
    BooleanParameterType,
    ByteOrder,
    Comparison,
    ComparisonOperator,
    ContextCalibrator,
    ContextReference,
    DynamicDimension,
    FloatParameterType,
    InsufficientDataError,
    IntegerParameterType,
    MdbValidationError,
    MissionDatabaseBuilder,
    NumericAlarmRange,
    ParameterDefinition,
    ParameterEntry,
    ParameterReference,
    ParameterType,
    PolynomialCalibrator,
    QualifiedName,
    ReferenceResolutionError,
    RelativeTimeParameterType,
    RelativeTimeValue,
    SequenceContainer,
    SpaceSystem,
    TimeArithmeticError,
    TimeDecodeError,
    TimeEpochDefinition,
    TimeScale,
)


def qname(value: str) -> QualifiedName:
    return QualifiedName.parse(value)


def builder() -> MissionDatabaseBuilder:
    result = MissionDatabaseBuilder("mission")
    result.add_space_system(SpaceSystem(qname("/Satellite")))
    return result


def utc_epoch(name: str = "UTC") -> TimeEpochDefinition:
    return TimeEpochDefinition(
        qname(f"/Satellite/{name}"),
        datetime(2000, 1, 1, tzinfo=UTC),
        TimeScale.UTC,
    )


def add_single_parameter(
    target: MissionDatabaseBuilder,
    parameter_type: ParameterType,
    data_name: str = "time",
) -> None:
    target.add_parameter_type(parameter_type)
    target.add_parameter(
        ParameterDefinition(qname(f"/Satellite/{data_name}"), parameter_type.name)
    )
    target.add_container(
        SequenceContainer(qname("/Satellite/root"), (ParameterEntry(data_name),))
    )


def test_absolute_integer_time_decoding_and_epoch_lookup() -> None:
    target = builder()
    epoch = utc_epoch()
    target.add_time_epoch(epoch)
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u32"), 32))
    add_single_parameter(
        target, AbsoluteTimeParameterType(qname("/Satellite/time_t"), "u32", "UTC")
    )

    database = target.compile()
    decoded = database.decode(
        (123456).to_bytes(4, "big"), root_container="/Satellite/root"
    ).parameters[0]

    assert decoded.raw_value == 123456
    assert decoded.value == AbsoluteTimeValue(epoch, Decimal(123456))
    assert decoded.unit == "s"
    assert decoded.alarm_severity is None
    assert database.time_epoch("/Satellite/UTC") == epoch
    assert isinstance(database.time_epochs, MappingProxyType)


def test_signed_little_endian_relative_time() -> None:
    target = builder()
    target.add_parameter_type(
        IntegerParameterType(
            qname("/Satellite/i16"),
            16,
            signed=True,
            byte_order=ByteOrder.LITTLE_ENDIAN,
        )
    )
    add_single_parameter(
        target,
        RelativeTimeParameterType(
            qname("/Satellite/duration_t"),
            "i16",
            seconds_per_unit=Decimal("0.25"),
            offset_seconds=Decimal("1.5"),
        ),
    )

    value = (
        target.compile()
        .decode(
            (-2).to_bytes(2, "little", signed=True), root_container="/Satellite/root"
        )
        .parameters[0]
    )

    assert value.value == RelativeTimeValue(Decimal("1.00"))


@pytest.mark.parametrize(
    ("size", "order", "format_code"),
    [(32, ByteOrder.BIG_ENDIAN, ">f"), (64, ByteOrder.LITTLE_ENDIAN, "<d")],
)
def test_float_time_decoding_is_deterministic(
    size: int, order: ByteOrder, format_code: str
) -> None:
    target = builder()
    target.add_parameter_type(
        FloatParameterType(qname("/Satellite/float_t"), size, byte_order=order)
    )
    add_single_parameter(
        target,
        RelativeTimeParameterType(qname("/Satellite/duration_t"), "float_t"),
    )

    value = (
        target.compile()
        .decode(struct.pack(format_code, 1.25), root_container="/Satellite/root")
        .parameters[0]
        .value
    )

    assert value == RelativeTimeValue(Decimal("1.25"))


def test_contextual_calibration_precedes_decimal_scale() -> None:
    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    target.add_parameter_type(
        IntegerParameterType(
            qname("/Satellite/encoded_t"),
            8,
            calibrator=PolynomialCalibrator((0.0, 2.0)),
            contextual_calibrators=(
                ContextCalibrator(
                    (
                        Comparison(
                            ContextReference("clock.fast"),
                            ComparisonOperator.EQUAL,
                            True,
                        ),
                    ),
                    PolynomialCalibrator((0.0, 4.0)),
                ),
            ),
        )
    )
    add_single_parameter(
        target,
        RelativeTimeParameterType(
            qname("/Satellite/duration_t"),
            "encoded_t",
            seconds_per_unit=Decimal("0.1"),
            offset_seconds=Decimal("0.5"),
        ),
    )

    database = target.compile()
    default = (
        database.decode(
            b"\x05", root_container="/Satellite/root", context={"clock.fast": False}
        )
        .parameters[0]
        .value
    )
    contextual = (
        database.decode(
            b"\x05", root_container="/Satellite/root", context={"clock.fast": True}
        )
        .parameters[0]
        .value
    )

    assert default == RelativeTimeValue(Decimal("1.5"))
    assert contextual == RelativeTimeValue(Decimal("2.5"))


def test_wrapper_and_encoding_validity_combine_and_alarms_do_not_propagate() -> None:
    target = builder()
    target.add_parameter_type(
        IntegerParameterType(
            qname("/Satellite/encoded_t"),
            8,
            validity_criteria=(
                Comparison(
                    ContextReference("encoding.valid"),
                    ComparisonOperator.EQUAL,
                    True,
                ),
            ),
            alarm_ranges=(NumericAlarmRange(AlarmSeverity.SEVERE, minimum=1),),
        )
    )
    add_single_parameter(
        target,
        RelativeTimeParameterType(
            qname("/Satellite/duration_t"),
            "encoded_t",
            validity_criteria=(
                Comparison(
                    ContextReference("time.valid"),
                    ComparisonOperator.EQUAL,
                    True,
                ),
            ),
        ),
    )
    database = target.compile()

    valid = database.decode(
        b"\x02",
        root_container="/Satellite/root",
        context={"encoding.valid": True, "time.valid": True},
    ).parameters[0]
    invalid = database.decode(
        b"\x02",
        root_container="/Satellite/root",
        context={"encoding.valid": True, "time.valid": False},
    ).parameters[0]

    assert valid.is_valid is True
    assert valid.alarm_severity is None
    assert invalid.is_valid is False
    assert invalid.alarm_severity is None


def test_time_values_in_arrays_and_aggregates() -> None:
    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    target.add_parameter_type(
        RelativeTimeParameterType(qname("/Satellite/duration_t"), "u8")
    )
    target.add_parameter_type(
        ArrayParameterType(qname("/Satellite/times_t"), "duration_t", 2)
    )
    target.add_parameter_type(
        AggregateParameterType(
            qname("/Satellite/record_t"),
            (AggregateMember("times", "times_t"),),
        )
    )
    target.add_parameter(ParameterDefinition(qname("/Satellite/time"), "record_t"))
    target.add_container(
        SequenceContainer(qname("/Satellite/root"), (ParameterEntry("time"),))
    )

    value = (
        target.compile()
        .decode(b"\x01\x02", root_container="/Satellite/root")
        .parameters[0]
        .value
    )

    assert isinstance(value, AggregateValue)
    array = value["times"].value
    assert isinstance(array, ArrayValue)
    assert [element.value for element in array] == [
        RelativeTimeValue(Decimal(1)),
        RelativeTimeValue(Decimal(2)),
    ]


def test_time_value_in_derived_container() -> None:
    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    target.add_parameter_type(
        RelativeTimeParameterType(qname("/Satellite/time_t"), "u8")
    )
    target.add_parameter(ParameterDefinition(qname("/Satellite/mode"), "u8"))
    target.add_parameter(ParameterDefinition(qname("/Satellite/time"), "time_t"))
    target.add_container(
        SequenceContainer(qname("/Satellite/root"), (ParameterEntry("mode"),))
    )
    target.add_container(
        SequenceContainer(
            qname("/Satellite/derived"),
            (ParameterEntry("time"),),
            base_container_ref="root",
            restrictions=(
                Comparison(ParameterReference("mode"), ComparisonOperator.EQUAL, 1),
            ),
        )
    )

    result = target.compile().decode(b"\x01\x05", root_container="/Satellite/root")

    assert result.parameters[1].value == RelativeTimeValue(Decimal(5))


def test_time_arithmetic_and_datetime_conversion() -> None:
    epoch = utc_epoch()
    instant = AbsoluteTimeValue(epoch, Decimal("10.0000015"))
    duration = RelativeTimeValue(Decimal("2.5"))

    assert instant + duration == AbsoluteTimeValue(epoch, Decimal("12.5000015"))
    assert instant - duration == AbsoluteTimeValue(epoch, Decimal("7.5000015"))
    assert (instant + duration) - instant == duration
    assert duration + RelativeTimeValue(Decimal("1")) == RelativeTimeValue(
        Decimal("3.5")
    )
    assert duration - RelativeTimeValue(Decimal("1")) == RelativeTimeValue(
        Decimal("1.5")
    )
    assert instant.to_datetime() == epoch.origin + timedelta(seconds=10, microseconds=2)

    half_even = AbsoluteTimeValue(epoch, Decimal("0.0000005"))
    assert half_even.to_datetime() == epoch.origin


def test_incompatible_time_arithmetic_and_conversion() -> None:
    utc = utc_epoch("UTC")
    other = utc_epoch("Other")
    tai = TimeEpochDefinition(
        qname("/Satellite/TAI"), datetime(2000, 1, 1, tzinfo=UTC), TimeScale.TAI
    )

    with pytest.raises(TimeArithmeticError, match="same epoch"):
        _ = AbsoluteTimeValue(utc, Decimal(1)) - AbsoluteTimeValue(other, Decimal(1))
    with pytest.raises(TimeArithmeticError, match="UTC"):
        AbsoluteTimeValue(tai, Decimal(1)).to_datetime()
    with pytest.raises(TimeArithmeticError, match="out of range"):
        AbsoluteTimeValue(utc, Decimal("1e30")).to_datetime()


@pytest.mark.parametrize("scale", list(TimeScale))
def test_all_time_scales_preserve_identity(scale: TimeScale) -> None:
    epoch = TimeEpochDefinition(
        qname(f"/Satellite/{scale.value}"),
        datetime(2000, 1, 1, tzinfo=UTC),
        scale,
    )
    assert AbsoluteTimeValue(epoch, Decimal(1)).epoch.time_scale is scale


def test_invalid_epochs_and_epoch_references() -> None:
    target = builder()
    target.add_time_epoch(
        TimeEpochDefinition(
            qname("/Satellite/naive"), datetime(2000, 1, 1), TimeScale.UTC
        )
    )
    with pytest.raises(MdbValidationError, match="timezone-aware"):
        target.compile()

    target = builder()
    target.add_time_epoch(
        TimeEpochDefinition(
            qname("/Satellite/offset"),
            datetime(2000, 1, 1, tzinfo=timezone(timedelta(hours=1))),
            TimeScale.UTC,
        )
    )
    with pytest.raises(MdbValidationError, match="zero offset"):
        target.compile()

    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    target.add_parameter_type(
        AbsoluteTimeParameterType(qname("/Satellite/time_t"), "u8", "missing")
    )
    with pytest.raises(ReferenceResolutionError):
        target.compile()

    target = builder()
    target.add_time_epoch(utc_epoch())
    with pytest.raises(MdbValidationError, match="duplicate"):
        target.add_time_epoch(utc_epoch())


@pytest.mark.parametrize(
    "parameter_type",
    [
        RelativeTimeParameterType(
            qname("/Satellite/time_t"), "u8", seconds_per_unit=Decimal(0)
        ),
        RelativeTimeParameterType(
            qname("/Satellite/time_t"),
            "u8",
            offset_seconds=Decimal("NaN"),
        ),
        RelativeTimeParameterType(
            qname("/Satellite/time_t"),
            "u8",
            seconds_per_unit=1,  # type: ignore[arg-type]
        ),
    ],
)
def test_invalid_time_scaling_definitions(
    parameter_type: RelativeTimeParameterType,
) -> None:
    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    target.add_parameter_type(parameter_type)
    with pytest.raises(MdbValidationError):
        target.compile()


def test_time_encoding_must_be_numeric() -> None:
    target = builder()
    target.add_parameter_type(BooleanParameterType(qname("/Satellite/bool_t"), 8))
    target.add_parameter_type(
        RelativeTimeParameterType(qname("/Satellite/time_t"), "bool_t")
    )
    with pytest.raises(MdbValidationError, match="integer or float"):
        target.compile()


def test_time_parameters_cannot_drive_layout_or_comparisons() -> None:
    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    target.add_parameter_type(
        RelativeTimeParameterType(qname("/Satellite/time_t"), "u8")
    )
    target.add_parameter_type(
        ArrayParameterType(
            qname("/Satellite/array_t"),
            "u8",
            DynamicDimension(ParameterReference("time"), maximum=4),
        )
    )
    target.add_parameter(ParameterDefinition(qname("/Satellite/time"), "time_t"))
    with pytest.raises(MdbValidationError, match="dynamic dimensions"):
        target.compile()

    target = builder()
    target.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    target.add_parameter_type(
        RelativeTimeParameterType(qname("/Satellite/time_t"), "u8")
    )
    target.add_parameter(ParameterDefinition(qname("/Satellite/time"), "time_t"))
    target.add_container(
        SequenceContainer(qname("/Satellite/root"), (ParameterEntry("time"),))
    )
    target.add_container(
        SequenceContainer(
            qname("/Satellite/derived"),
            base_container_ref="root",
            restrictions=(
                Comparison(ParameterReference("time"), ComparisonOperator.EQUAL, 1),
            ),
        )
    )
    with pytest.raises(MdbValidationError, match="comparisons"):
        target.compile()


def test_nonfinite_and_truncated_time_inputs() -> None:
    target = builder()
    target.add_parameter_type(FloatParameterType(qname("/Satellite/float_t"), 32))
    add_single_parameter(
        target, RelativeTimeParameterType(qname("/Satellite/time_t"), "float_t")
    )
    database = target.compile()

    with pytest.raises(TimeDecodeError, match="finite"):
        database.decode(
            struct.pack(">f", float("inf")), root_container="/Satellite/root"
        )
    with pytest.raises(InsufficientDataError):
        database.decode(b"\x00", root_container="/Satellite/root")
    with pytest.raises(ReferenceResolutionError):
        database.time_epoch("/Satellite/missing")
