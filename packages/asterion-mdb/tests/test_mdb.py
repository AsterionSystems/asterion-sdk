import math
import struct
from types import MappingProxyType

import pytest
from asterion.mdb import (
    AmbiguousContainerError,
    BinaryParameterType,
    BooleanParameterType,
    ByteOrder,
    Comparison,
    ComparisonOperator,
    ContextReference,
    EntryPosition,
    EnumeratedParameterType,
    EnumeratedValue,
    FloatParameterType,
    InsufficientDataError,
    IntegerParameterType,
    MdbDecodeError,
    MdbValidationError,
    MissionDatabaseBuilder,
    NoMatchingContainerError,
    ParameterDefinition,
    ParameterEntry,
    ParameterReference,
    PolynomialCalibrator,
    QualifiedName,
    ReferenceResolutionError,
    SequenceContainer,
    SpaceSystem,
    StringEncoding,
    StringParameterType,
)


def qname(value: str) -> QualifiedName:
    return QualifiedName.parse(value)


def basic_builder() -> MissionDatabaseBuilder:
    builder = MissionDatabaseBuilder("mission")
    builder.add_space_system(SpaceSystem(qname("/Satellite")))
    return builder


def test_qualified_name_hierarchy() -> None:
    name = qname("/Satellite/Thermal/temperature")

    assert str(name) == "/Satellite/Thermal/temperature"
    assert str(name.parent) == "/Satellite/Thermal"
    assert qname("/Satellite").child("mode") == qname("/Satellite/mode")


@pytest.mark.parametrize("value", ["relative", "", "/bad//name"])
def test_invalid_qualified_names(value: str) -> None:
    with pytest.raises(MdbValidationError):
        qname(value)


def test_builder_rejects_duplicates_and_missing_systems() -> None:
    builder = basic_builder()
    system = SpaceSystem(qname("/Satellite"))
    with pytest.raises(MdbValidationError, match="duplicate"):
        builder.add_space_system(system)

    builder.add_parameter_type(IntegerParameterType(qname("/Unknown/u8"), 8))
    with pytest.raises(MdbValidationError, match="unknown space system"):
        builder.compile()


@pytest.mark.parametrize(
    "method_name",
    ["add_space_system", "add_parameter_type", "add_parameter", "add_container"],
)
def test_builder_rejects_unsupported_definition_objects(method_name: str) -> None:
    builder = basic_builder()

    with pytest.raises(MdbValidationError):
        getattr(builder, method_name)(object())


def test_scoped_reference_resolution_and_aliases() -> None:
    builder = basic_builder()
    builder.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    builder.add_parameter(
        ParameterDefinition(qname("/Satellite/value"), "u8", aliases=("VALUE",))
    )
    builder.add_container(
        SequenceContainer(qname("/Satellite/root"), entries=(ParameterEntry("value"),))
    )

    database = builder.compile()
    result = database.decode(b"\x2a", root_container="/Satellite/root")

    assert result.by_name[qname("/Satellite/value")].value == 42
    assert database.parameter("VALUE").name == qname("/Satellite/value")
    with pytest.raises(ReferenceResolutionError):
        database.parameter("UNKNOWN")


def test_missing_reference_and_duplicate_alias() -> None:
    builder = basic_builder()
    builder.add_parameter(
        ParameterDefinition(qname("/Satellite/value"), "missing", aliases=("X",))
    )
    with pytest.raises(ReferenceResolutionError):
        builder.compile()

    builder = basic_builder()
    builder.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    builder.add_parameter(
        ParameterDefinition(qname("/Satellite/a"), "u8", aliases=("X",))
    )
    builder.add_parameter(
        ParameterDefinition(qname("/Satellite/b"), "u8", aliases=("X",))
    )
    with pytest.raises(MdbValidationError, match="duplicate parameter alias"):
        builder.compile()


def test_big_endian_bit_fields_and_signed_values() -> None:
    builder = basic_builder()
    builder.add_parameter_type(IntegerParameterType(qname("/Satellite/u3"), 3))
    builder.add_parameter_type(
        IntegerParameterType(qname("/Satellite/i5"), 5, signed=True)
    )
    builder.add_parameter(ParameterDefinition(qname("/Satellite/a"), "u3"))
    builder.add_parameter(ParameterDefinition(qname("/Satellite/b"), "i5"))
    builder.add_container(
        SequenceContainer(
            qname("/Satellite/root"),
            entries=(ParameterEntry("a"), ParameterEntry("b")),
        )
    )

    result = builder.compile().decode(b"\xbf", root_container="/Satellite/root")

    assert result.by_name[qname("/Satellite/a")].value == 5
    assert result.by_name[qname("/Satellite/b")].value == -1
    assert result.consumed_bits == 8


def test_little_endian_integer_and_alignment_validation() -> None:
    builder = basic_builder()
    builder.add_parameter_type(
        IntegerParameterType(
            qname("/Satellite/u16le"), 16, byte_order=ByteOrder.LITTLE_ENDIAN
        )
    )
    builder.add_parameter(ParameterDefinition(qname("/Satellite/value"), "u16le"))
    builder.add_container(
        SequenceContainer(qname("/Satellite/root"), entries=(ParameterEntry("value"),))
    )
    assert (
        builder.compile()
        .decode(b"\x34\x12", root_container="/Satellite/root")
        .parameters[0]
        .value
        == 0x1234
    )

    invalid = basic_builder()
    invalid.add_parameter_type(
        IntegerParameterType(
            qname("/Satellite/u12le"), 12, byte_order=ByteOrder.LITTLE_ENDIAN
        )
    )
    with pytest.raises(MdbValidationError, match="whole bytes"):
        invalid.compile()


def test_float_boolean_binary_and_strings() -> None:
    builder = basic_builder()
    types = (
        FloatParameterType(qname("/Satellite/f32"), 32),
        BooleanParameterType(qname("/Satellite/bool"), 8),
        BinaryParameterType(qname("/Satellite/bin"), 16),
        StringParameterType(qname("/Satellite/text"), 32, StringEncoding.ASCII),
    )
    for parameter_type in types:
        builder.add_parameter_type(parameter_type)
    for name, type_name in (
        ("f", "f32"),
        ("ok", "bool"),
        ("raw", "bin"),
        ("text", "text"),
    ):
        builder.add_parameter(
            ParameterDefinition(qname(f"/Satellite/{name}"), type_name)
        )
    builder.add_container(
        SequenceContainer(
            qname("/Satellite/root"),
            entries=tuple(ParameterEntry(name) for name in ("f", "ok", "raw", "text")),
        )
    )
    data = struct.pack(">f", 1.5) + b"\x02\xaa\xbbHi\x00\x00"

    result = builder.compile().decode(data, root_container="/Satellite/root")

    float_value = result.by_name[qname("/Satellite/f")].value
    assert isinstance(float_value, float)
    assert math.isclose(float_value, 1.5)
    assert result.by_name[qname("/Satellite/ok")].value is True
    assert result.by_name[qname("/Satellite/raw")].value == b"\xaa\xbb"
    assert result.by_name[qname("/Satellite/text")].value == "Hi"


def test_enumerations_preserve_unknown_values() -> None:
    builder = basic_builder()
    builder.add_parameter_type(
        EnumeratedParameterType(qname("/Satellite/mode_t"), 8, ((1, "SAFE"),))
    )
    builder.add_parameter(ParameterDefinition(qname("/Satellite/mode"), "mode_t"))
    builder.add_container(
        SequenceContainer(qname("/Satellite/root"), entries=(ParameterEntry("mode"),))
    )
    database = builder.compile()

    assert database.decode(b"\x01", root_container="/Satellite/root").parameters[
        0
    ].value == EnumeratedValue(1, "SAFE")
    assert database.decode(b"\x09", root_container="/Satellite/root").parameters[
        0
    ].value == EnumeratedValue(9, None)


def test_enumeration_restrictions_compare_labels() -> None:
    builder = basic_builder()
    builder.add_parameter_type(
        EnumeratedParameterType(qname("/Satellite/mode_t"), 8, ((1, "SAFE"),))
    )
    builder.add_parameter(ParameterDefinition(qname("/Satellite/mode"), "mode_t"))
    builder.add_container(
        SequenceContainer(qname("/Satellite/root"), entries=(ParameterEntry("mode"),))
    )
    builder.add_container(
        SequenceContainer(
            qname("/Satellite/safe"),
            base_container_ref="root",
            restrictions=(
                Comparison(
                    ParameterReference("mode"), ComparisonOperator.EQUAL, "SAFE"
                ),
            ),
        )
    )

    result = builder.compile().decode(b"\x01", root_container="/Satellite/root")

    assert result.container.name == qname("/Satellite/safe")


def test_polynomial_calibration_and_units() -> None:
    builder = basic_builder()
    builder.add_parameter_type(
        IntegerParameterType(
            qname("/Satellite/temp_t"),
            8,
            unit="degC",
            calibrator=PolynomialCalibrator((-10.0, 0.5)),
        )
    )
    builder.add_parameter(ParameterDefinition(qname("/Satellite/temp"), "temp_t"))
    builder.add_container(
        SequenceContainer(qname("/Satellite/root"), entries=(ParameterEntry("temp"),))
    )

    value = (
        builder.compile()
        .decode(b"\x28", root_container="/Satellite/root")
        .parameters[0]
    )

    assert value.raw_value == 40
    assert value.value == 10.0
    assert value.unit == "degC"


def test_absolute_entries_gaps_and_overlaps() -> None:
    builder = basic_builder()
    builder.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    for name in ("first", "late", "overlap"):
        builder.add_parameter(ParameterDefinition(qname(f"/Satellite/{name}"), "u8"))
    builder.add_container(
        SequenceContainer(
            qname("/Satellite/root"),
            entries=(
                ParameterEntry("first"),
                ParameterEntry("late", EntryPosition.ABSOLUTE, 24),
                ParameterEntry("overlap", EntryPosition.ABSOLUTE, 8),
            ),
        )
    )

    result = builder.compile().decode(
        b"\x01\x02\x03\x04", root_container="/Satellite/root"
    )

    assert [value.value for value in result.parameters] == [1, 4, 2]
    assert result.consumed_bits == 32


def selection_database():
    builder = basic_builder()
    builder.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    for name in ("mode", "base_value", "derived_value"):
        builder.add_parameter(ParameterDefinition(qname(f"/Satellite/{name}"), "u8"))
    builder.add_container(
        SequenceContainer(
            qname("/Satellite/root"),
            entries=(ParameterEntry("mode"), ParameterEntry("base_value")),
        )
    )
    builder.add_container(
        SequenceContainer(
            qname("/Satellite/safe"),
            entries=(ParameterEntry("derived_value"),),
            base_container_ref="root",
            restrictions=(
                Comparison(ParameterReference("mode"), ComparisonOperator.EQUAL, 1),
                Comparison(
                    ContextReference("ccsds.apid"), ComparisonOperator.EQUAL, 42
                ),
            ),
        )
    )
    return builder


def test_inheritance_and_context_selection() -> None:
    result = (
        selection_database()
        .compile()
        .decode(
            b"\x01\x02\x03",
            root_container="/Satellite/root",
            context={"ccsds.apid": 42},
        )
    )

    assert result.container.name == qname("/Satellite/safe")
    assert [value.value for value in result.parameters] == [1, 2, 3]
    assert isinstance(result.by_name, MappingProxyType)


def test_no_match_missing_context_and_ambiguity() -> None:
    database = selection_database().compile()
    with pytest.raises(MdbDecodeError, match="missing context"):
        database.decode(b"\x01\x02\x03", root_container="/Satellite/root")
    with pytest.raises(NoMatchingContainerError):
        database.decode(
            b"\x02\x02\x03",
            root_container="/Satellite/root",
            context={"ccsds.apid": 42},
        )

    builder = selection_database()
    builder.add_container(
        SequenceContainer(
            qname("/Satellite/also_safe"),
            base_container_ref="root",
            restrictions=(
                Comparison(ParameterReference("mode"), ComparisonOperator.EQUAL, 1),
                Comparison(
                    ContextReference("ccsds.apid"), ComparisonOperator.EQUAL, 42
                ),
            ),
        )
    )
    with pytest.raises(AmbiguousContainerError):
        builder.compile().decode(
            b"\x01\x02",
            root_container="/Satellite/root",
            context={"ccsds.apid": 42},
        )


def test_container_cycles_are_rejected() -> None:
    builder = basic_builder()
    builder.add_container(
        SequenceContainer(qname("/Satellite/a"), base_container_ref="b")
    )
    builder.add_container(
        SequenceContainer(qname("/Satellite/b"), base_container_ref="a")
    )
    with pytest.raises(MdbValidationError, match="cycle"):
        builder.compile()


def test_truncated_data_and_invalid_input() -> None:
    builder = basic_builder()
    builder.add_parameter_type(IntegerParameterType(qname("/Satellite/u16"), 16))
    builder.add_parameter(ParameterDefinition(qname("/Satellite/value"), "u16"))
    builder.add_container(
        SequenceContainer(qname("/Satellite/root"), entries=(ParameterEntry("value"),))
    )
    database = builder.compile()

    with pytest.raises(InsufficientDataError) as caught:
        database.decode(b"\x01", root_container="/Satellite/root")
    assert caught.value.required_bits == 16
    assert caught.value.available_bits == 8
    with pytest.raises(MdbDecodeError, match="bytes"):
        database.decode(object(), root_container="/Satellite/root")  # type: ignore[arg-type]


def test_bytearray_input_is_defensively_copied_during_decode() -> None:
    builder = basic_builder()
    builder.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    builder.add_parameter(ParameterDefinition(qname("/Satellite/value"), "u8"))
    builder.add_container(
        SequenceContainer(qname("/Satellite/root"), entries=(ParameterEntry("value"),))
    )
    source = bytearray(b"\x2a")

    result = builder.compile().decode(source, root_container="/Satellite/root")
    source[0] = 0

    assert result.parameters[0].value == 42


@pytest.mark.parametrize("name", ["", "bad/name", 42])
def test_invalid_database_names(name: object) -> None:
    with pytest.raises(MdbValidationError):
        MissionDatabaseBuilder(name)  # type: ignore[arg-type]


def test_definition_validation_boundaries() -> None:
    missing_parent = MissionDatabaseBuilder("mission")
    missing_parent.add_space_system(SpaceSystem(qname("/Satellite/Thermal")))
    with pytest.raises(MdbValidationError, match="missing parent"):
        missing_parent.compile()

    invalid_types = (
        IntegerParameterType(qname("/Satellite/zero"), 0),
        FloatParameterType(qname("/Satellite/f16"), 16),
        BinaryParameterType(qname("/Satellite/bits"), 7),
        StringParameterType(qname("/Satellite/text"), 8, strip_padding=b""),
        EnumeratedParameterType(qname("/Satellite/duplicate"), 8, ((1, "A"), (1, "B"))),
    )
    for parameter_type in invalid_types:
        builder = basic_builder()
        builder.add_parameter_type(parameter_type)
        with pytest.raises(MdbValidationError):
            builder.compile()


@pytest.mark.parametrize(
    "entry",
    [
        ParameterEntry("value", EntryPosition.ABSOLUTE),
        ParameterEntry("value", EntryPosition.ABSOLUTE, -1),
        ParameterEntry("value", EntryPosition.SEQUENTIAL, 1),
    ],
)
def test_invalid_entry_layouts(entry: ParameterEntry) -> None:
    builder = basic_builder()
    builder.add_parameter_type(IntegerParameterType(qname("/Satellite/u8"), 8))
    builder.add_parameter(ParameterDefinition(qname("/Satellite/value"), "u8"))
    builder.add_container(SequenceContainer(qname("/Satellite/root"), (entry,)))

    with pytest.raises(MdbValidationError):
        builder.compile()


def test_decode_error_boundaries() -> None:
    builder = basic_builder()
    builder.add_parameter_type(StringParameterType(qname("/Satellite/text_t"), 8))
    builder.add_parameter(ParameterDefinition(qname("/Satellite/text"), "text_t"))
    builder.add_container(
        SequenceContainer(qname("/Satellite/root"), (ParameterEntry("text"),))
    )
    database = builder.compile()

    with pytest.raises(MdbDecodeError, match="unknown root"):
        database.decode(b"", root_container="/Satellite/missing")
    with pytest.raises(MdbDecodeError, match="cannot decode string"):
        database.decode(b"\xff", root_container="/Satellite/root")
    with pytest.raises(ReferenceResolutionError, match="unknown parameter"):
        database.parameter("/Satellite/missing")

    view = memoryview(b"x")
    view.release()
    with pytest.raises(MdbDecodeError, match="usable byte buffer"):
        database.decode(view, root_container="/Satellite/root")
