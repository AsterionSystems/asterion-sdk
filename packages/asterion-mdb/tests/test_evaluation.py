import asterion.mdb as mdb
import pytest
from asterion.mdb import (
    AlarmSeverity,
    CalibrationSelectionError,
    Comparison,
    ComparisonOperator,
    ContextCalibrator,
    ContextReference,
    EnumeratedParameterType,
    EnumerationAlarm,
    IntegerParameterType,
    MdbValidationError,
    MissionDatabaseBuilder,
    NumericAlarmRange,
    ParameterDefinition,
    ParameterEntry,
    ParameterReference,
    PolynomialCalibrator,
    QualifiedName,
    SequenceContainer,
    SpaceSystem,
    ValidityEvaluationError,
)


def qname(value: str) -> QualifiedName:
    return QualifiedName.parse(value)


def test_public_exports_are_sorted_unique_and_accessible() -> None:
    assert len(mdb.__all__) == len(set(mdb.__all__))
    assert all(hasattr(mdb, name) for name in mdb.__all__)


def numeric_database(
    *,
    calibrator: PolynomialCalibrator | None = None,
    contextual: tuple[ContextCalibrator, ...] = (),
    validity: tuple[Comparison, ...] = (),
    alarms: tuple[NumericAlarmRange, ...] = (),
):
    builder = MissionDatabaseBuilder("mission")
    builder.add_space_system(SpaceSystem(qname("/Satellite")))
    builder.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    builder.add_parameter_type(
        IntegerParameterType(
            qname("/Satellite/temperature_t"),
            8,
            unit="degC",
            calibrator=calibrator,
            contextual_calibrators=contextual,
            validity_criteria=validity,
            alarm_ranges=alarms,
        )
    )
    builder.add_parameter(ParameterDefinition(qname("/Satellite/mode"), "u8"))
    builder.add_parameter(
        ParameterDefinition(qname("/Satellite/temperature"), "temperature_t")
    )
    builder.add_container(
        SequenceContainer(
            qname("/Satellite/root"),
            (ParameterEntry("mode"), ParameterEntry("temperature")),
        )
    )
    return builder.compile()


def test_identity_default_and_contextual_calibration() -> None:
    contextual = ContextCalibrator(
        (Comparison(ParameterReference("mode"), ComparisonOperator.EQUAL, 1),),
        PolynomialCalibrator((100.0, 2.0)),
    )
    database = numeric_database(
        calibrator=PolynomialCalibrator((-10.0, 0.5)), contextual=(contextual,)
    )

    assert (
        database.decode(b"\x00\x28", root_container="/Satellite/root")
        .parameters[1]
        .value
        == 10.0
    )
    assert (
        database.decode(b"\x01\x02", root_container="/Satellite/root")
        .parameters[1]
        .value
        == 104.0
    )
    assert (
        numeric_database()
        .decode(b"\x00\x07", root_container="/Satellite/root")
        .parameters[1]
        .value
        == 7
    )


def test_context_calibration_can_use_caller_context() -> None:
    contextual = ContextCalibrator(
        (Comparison(ContextReference("thermal.bank"), ComparisonOperator.EQUAL, "B"),),
        PolynomialCalibrator((0.0, 10.0)),
    )
    database = numeric_database(contextual=(contextual,))

    value = database.decode(
        b"\x00\x03",
        root_container="/Satellite/root",
        context={"thermal.bank": "B"},
    ).parameters[1]

    assert value.raw_value == 3
    assert value.value == 30.0


def test_ambiguous_and_missing_contextual_calibration() -> None:
    criterion = (Comparison(ParameterReference("mode"), ComparisonOperator.EQUAL, 1),)
    database = numeric_database(
        contextual=(
            ContextCalibrator(criterion, PolynomialCalibrator((1.0,))),
            ContextCalibrator(criterion, PolynomialCalibrator((2.0,))),
        )
    )
    with pytest.raises(CalibrationSelectionError, match="multiple"):
        database.decode(b"\x01\x02", root_container="/Satellite/root")

    missing = numeric_database(
        contextual=(
            ContextCalibrator(
                (Comparison(ContextReference("missing"), ComparisonOperator.EQUAL, 1),),
                PolynomialCalibrator((1.0,)),
            ),
        )
    )
    with pytest.raises(CalibrationSelectionError, match="missing context"):
        missing.decode(b"\x00\x02", root_container="/Satellite/root")


def test_validity_preserves_values_and_suppresses_alarms() -> None:
    validity = (Comparison(ParameterReference("mode"), ComparisonOperator.EQUAL, 1),)
    database = numeric_database(
        calibrator=PolynomialCalibrator((0.0, 2.0)),
        validity=validity,
        alarms=(NumericAlarmRange(AlarmSeverity.SEVERE, minimum=10),),
    )

    invalid = database.decode(b"\x00\x0a", root_container="/Satellite/root").parameters[
        1
    ]
    valid = database.decode(b"\x01\x0a", root_container="/Satellite/root").parameters[1]

    assert (invalid.raw_value, invalid.value, invalid.is_valid) == (10, 20.0, False)
    assert invalid.alarm_severity is None
    assert valid.is_valid is True
    assert valid.alarm_severity is AlarmSeverity.SEVERE


def test_missing_validity_input_has_typed_error() -> None:
    database = numeric_database(
        validity=(
            Comparison(ContextReference("valid"), ComparisonOperator.EQUAL, True),
        )
    )

    with pytest.raises(ValidityEvaluationError, match="missing context"):
        database.decode(b"\x00\x01", root_container="/Satellite/root")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1, AlarmSeverity.WATCH),
        (2, AlarmSeverity.WARNING),
        (3, AlarmSeverity.DISTRESS),
        (4, AlarmSeverity.CRITICAL),
        (5, AlarmSeverity.SEVERE),
        (6, None),
    ],
)
def test_all_alarm_severities(raw: int, expected: AlarmSeverity | None) -> None:
    alarms = tuple(
        NumericAlarmRange(severity, minimum=index, maximum=index)
        for index, severity in enumerate(AlarmSeverity, 1)
    )
    value = (
        numeric_database(alarms=alarms)
        .decode(bytes((0, raw)), root_container="/Satellite/root")
        .parameters[1]
    )

    assert value.alarm_severity is expected


def test_alarm_boundaries_and_overlaps_choose_strongest() -> None:
    database = numeric_database(
        alarms=(
            NumericAlarmRange(AlarmSeverity.WARNING, minimum=10, maximum=20),
            NumericAlarmRange(
                AlarmSeverity.CRITICAL,
                minimum=15,
                maximum=20,
                minimum_inclusive=False,
                maximum_inclusive=False,
            ),
        )
    )

    severities = [
        database.decode(bytes((0, raw)), root_container="/Satellite/root")
        .parameters[1]
        .alarm_severity
        for raw in (10, 15, 16, 20, 21)
    ]
    assert severities == [
        AlarmSeverity.WARNING,
        AlarmSeverity.WARNING,
        AlarmSeverity.CRITICAL,
        AlarmSeverity.WARNING,
        None,
    ]


def test_enumeration_alarms_use_raw_values() -> None:
    builder = MissionDatabaseBuilder("mission")
    builder.add_space_system(SpaceSystem(qname("/Satellite")))
    builder.add_parameter_type(
        EnumeratedParameterType(
            qname("/Satellite/mode_t"),
            8,
            ((1, "SAFE"),),
            alarms=(EnumerationAlarm(9, AlarmSeverity.DISTRESS),),
        )
    )
    builder.add_parameter(ParameterDefinition(qname("/Satellite/mode"), "mode_t"))
    builder.add_container(
        SequenceContainer(qname("/Satellite/root"), (ParameterEntry("mode"),))
    )
    database = builder.compile()

    assert (
        database.decode(b"\x01", root_container="/Satellite/root")
        .parameters[0]
        .alarm_severity
        is None
    )
    assert (
        database.decode(b"\x09", root_container="/Satellite/root")
        .parameters[0]
        .alarm_severity
        is AlarmSeverity.DISTRESS
    )


@pytest.mark.parametrize(
    "alarms",
    [
        (NumericAlarmRange(AlarmSeverity.WARNING),),
        (NumericAlarmRange(AlarmSeverity.WARNING, minimum=2, maximum=1),),
        (
            NumericAlarmRange(
                AlarmSeverity.WARNING,
                minimum=1,
                maximum=1,
                minimum_inclusive=False,
            ),
        ),
    ],
)
def test_invalid_numeric_alarm_definitions(
    alarms: tuple[NumericAlarmRange, ...],
) -> None:
    with pytest.raises(MdbValidationError):
        numeric_database(alarms=alarms)


def test_compile_rejects_ambiguous_definition_shapes() -> None:
    with pytest.raises(MdbValidationError, match="require criteria"):
        numeric_database(
            contextual=(ContextCalibrator((), PolynomialCalibrator((1.0,))),)
        )

    builder = MissionDatabaseBuilder("mission")
    builder.add_space_system(SpaceSystem(qname("/Satellite")))
    builder.add_parameter_type(
        EnumeratedParameterType(
            qname("/Satellite/mode_t"),
            8,
            (),
            alarms=(
                EnumerationAlarm(1, AlarmSeverity.WARNING),
                EnumerationAlarm(1, AlarmSeverity.SEVERE),
            ),
        )
    )
    with pytest.raises(MdbValidationError, match="duplicate enumeration alarms"):
        builder.compile()


def test_calibrated_values_accumulate_through_container_inheritance() -> None:
    builder = MissionDatabaseBuilder("mission")
    builder.add_space_system(SpaceSystem(qname("/Satellite")))
    builder.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    builder.add_parameter_type(
        IntegerParameterType(
            qname("/Satellite/scaled_t"),
            8,
            calibrator=PolynomialCalibrator((0.0, 2.0)),
        )
    )
    builder.add_parameter(ParameterDefinition(qname("/Satellite/mode"), "u8"))
    builder.add_parameter(ParameterDefinition(qname("/Satellite/scaled"), "scaled_t"))
    builder.add_container(
        SequenceContainer(qname("/Satellite/root"), (ParameterEntry("mode"),))
    )
    builder.add_container(
        SequenceContainer(
            qname("/Satellite/derived"),
            (ParameterEntry("scaled"),),
            base_container_ref="root",
            restrictions=(
                Comparison(ParameterReference("mode"), ComparisonOperator.EQUAL, 1),
            ),
        )
    )

    result = builder.compile().decode(b"\x01\x05", root_container="/Satellite/root")

    assert result.container.name == qname("/Satellite/derived")
    assert result.parameters[1].value == 10.0


@pytest.mark.parametrize(
    "parameter_type",
    [
        IntegerParameterType(
            qname("/Satellite/bad_t"),
            8,
            calibrator=PolynomialCalibrator((float("nan"),)),
        ),
        IntegerParameterType(
            qname("/Satellite/bad_t"),
            8,
            alarm_ranges=(
                NumericAlarmRange(AlarmSeverity.WARNING, minimum=float("inf")),
            ),
        ),
    ],
)
def test_non_finite_evaluation_definitions_are_rejected(
    parameter_type: IntegerParameterType,
) -> None:
    builder = MissionDatabaseBuilder("mission")
    builder.add_space_system(SpaceSystem(qname("/Satellite")))
    builder.add_parameter_type(parameter_type)

    with pytest.raises(MdbValidationError):
        builder.compile()
