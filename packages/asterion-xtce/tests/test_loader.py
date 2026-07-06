from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from asterion.mdb import (
    AbsoluteTimeValue,
    AlarmSeverity,
    EnumeratedValue,
    QualifiedName,
    RelativeTimeValue,
)
from asterion.xtce import (
    XTCE_1_2_NAMESPACE,
    XTCE_1_3_NAMESPACE,
    UnsupportedXtceFeatureError,
    XtceLoadOptions,
    XtceMappingError,
    XtceParseError,
    XtceResourceLimitError,
    load,
    loads,
)


def telemetry_document(namespace: str = XTCE_1_3_NAMESPACE) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<SpaceSystem xmlns="{namespace}" name="Satellite">
  <TelemetryMetaData>
    <ParameterTypeSet>
      <EnumeratedParameterType name="mode_t">
        <UnitSet/>
        <EnumerationList>
          <Enumeration value="1" label="SCIENCE"/>
        </EnumerationList>
        <IntegerDataEncoding sizeInBits="8" encoding="unsigned"/>
      </EnumeratedParameterType>
      <IntegerParameterType name="temperature_t">
        <UnitSet><Unit>degC</Unit></UnitSet>
        <IntegerDataEncoding sizeInBits="8" encoding="unsigned">
          <DefaultCalibrator>
            <PolynomialCalibrator>
              <Term exponent="0" coefficient="0"/>
              <Term exponent="1" coefficient="2"/>
            </PolynomialCalibrator>
          </DefaultCalibrator>
        </IntegerDataEncoding>
        <DefaultAlarm>
          <StaticAlarmRanges><WarningRange minInclusive="10"/></StaticAlarmRanges>
        </DefaultAlarm>
      </IntegerParameterType>
      <StringParameterType name="text_t">
        <UnitSet/>
        <StringDataEncoding encoding="UTF-8">
          <SizeInBits><FixedValue>16</FixedValue></SizeInBits>
        </StringDataEncoding>
      </StringParameterType>
    </ParameterTypeSet>
    <ParameterSet>
      <Parameter name="mode" parameterTypeRef="mode_t">
        <AliasSet><Alias nameSpace="ops" alias="MODE"/></AliasSet>
      </Parameter>
      <Parameter name="temperature" parameterTypeRef="temperature_t"/>
      <Parameter name="text" parameterTypeRef="text_t"/>
    </ParameterSet>
    <ContainerSet>
      <SequenceContainer name="root">
        <EntryList>
          <ParameterRefEntry parameterRef="mode"/>
          <ParameterRefEntry parameterRef="temperature"/>
        </EntryList>
      </SequenceContainer>
      <SequenceContainer name="science">
        <BaseContainer containerRef="root">
          <RestrictionCriteria>
            <ComparisonList>
              <Comparison parameterRef="mode" comparisonOperator="==" value="SCIENCE"/>
            </ComparisonList>
          </RestrictionCriteria>
        </BaseContainer>
        <EntryList><ParameterRefEntry parameterRef="text"/></EntryList>
      </SequenceContainer>
    </ContainerSet>
  </TelemetryMetaData>
</SpaceSystem>"""


@pytest.mark.parametrize("namespace", [XTCE_1_3_NAMESPACE, XTCE_1_2_NAMESPACE])
def test_loads_telemetry_vertical_slice(namespace: str) -> None:
    database = loads(telemetry_document(namespace), source_name="mission.xml")
    result = database.decode(b"\x01\x05Hi", root_container="/Satellite/root")

    assert result.container.name == QualifiedName.parse("/Satellite/science")
    assert result.parameters[0].value == EnumeratedValue(1, "SCIENCE")
    assert result.parameters[1].value == 10.0
    assert result.parameters[1].unit == "degC"
    assert result.parameters[1].alarm_severity is AlarmSeverity.WARNING
    assert result.parameters[2].value == "Hi"
    assert database.parameter("ops:MODE").name == QualifiedName.parse("/Satellite/mode")


def test_nested_space_system_and_absolute_entry() -> None:
    xml = f"""<SpaceSystem xmlns="{XTCE_1_3_NAMESPACE}" name="Mission">
      <SpaceSystem name="Payload">
        <TelemetryMetaData>
          <ParameterTypeSet>
            <IntegerParameterType name="u8"><UnitSet/><IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
          </ParameterTypeSet>
          <ParameterSet><Parameter name="value" parameterTypeRef="u8"/></ParameterSet>
          <ContainerSet><SequenceContainer name="packet"><EntryList>
            <ParameterRefEntry parameterRef="value"><LocationInContainerInBits referenceLocation="containerStart"><FixedValue>8</FixedValue></LocationInContainerInBits></ParameterRefEntry>
          </EntryList></SequenceContainer></ContainerSet>
        </TelemetryMetaData>
      </SpaceSystem>
    </SpaceSystem>"""

    result = loads(xml).decode(b"\x00\x2a", root_container="/Mission/Payload/packet")

    assert result.parameters[0].value == 42
    assert result.consumed_bits == 16


def test_numeric_time_types() -> None:
    xml = f"""<SpaceSystem xmlns="{XTCE_1_3_NAMESPACE}" name="Satellite">
      <TelemetryMetaData><ParameterTypeSet>
        <RelativeTimeParameterType name="duration_t" scale="0.5" offset="1">
          <IntegerDataEncoding sizeInBits="16" encoding="twosComplement" byteOrder="leastSignificantByteFirst"/>
        </RelativeTimeParameterType>
        <AbsoluteTimeParameterType name="absolute_t">
          <ReferenceTime><Epoch>UNIX</Epoch></ReferenceTime>
          <FloatDataEncoding sizeInBits="32"/>
        </AbsoluteTimeParameterType>
      </ParameterTypeSet><ParameterSet>
        <Parameter name="duration" parameterTypeRef="duration_t"/>
        <Parameter name="absolute" parameterTypeRef="absolute_t"/>
      </ParameterSet><ContainerSet><SequenceContainer name="packet"><EntryList>
        <ParameterRefEntry parameterRef="duration"/><ParameterRefEntry parameterRef="absolute"/>
      </EntryList></SequenceContainer></ContainerSet></TelemetryMetaData>
    </SpaceSystem>"""
    import struct

    data = (-2).to_bytes(2, "little", signed=True) + struct.pack(">f", 2.5)
    values = loads(xml).decode(data, root_container="/Satellite/packet").parameters

    assert values[0].value == RelativeTimeValue(Decimal("0.0"))
    assert isinstance(values[1].value, AbsoluteTimeValue)
    assert values[1].value.elapsed_seconds == Decimal("2.5")
    assert values[1].value.epoch.origin == datetime(1970, 1, 1, tzinfo=UTC)


def test_load_path_and_bytes_like_inputs(tmp_path: Path) -> None:
    path = tmp_path / "mission.xml"
    path.write_text(telemetry_document(), encoding="utf-8")

    assert load(path).name == "Satellite"
    assert loads(bytearray(telemetry_document().encode())).name == "Satellite"
    assert loads(memoryview(telemetry_document().encode())).name == "Satellite"


@pytest.mark.parametrize(
    "xml",
    [
        "<not-xml",
        "<Other/>",
        '<SpaceSystem xmlns="urn:unknown" name="x"/>',
        f'<SpaceSystem xmlns="{XTCE_1_3_NAMESPACE}"/>',
    ],
)
def test_parse_and_envelope_failures(xml: str) -> None:
    with pytest.raises((XtceParseError, XtceMappingError)) as caught:
        loads(xml, source_name="broken.xml")
    assert caught.value.source_name == "broken.xml"


@pytest.mark.parametrize(
    "declaration",
    ["<!DOCTYPE SpaceSystem>", '<!ENTITY x "boom">'],
)
def test_dtd_and_entities_are_rejected(declaration: str) -> None:
    xml = f'{declaration}<SpaceSystem xmlns="{XTCE_1_3_NAMESPACE}" name="x"/>'
    with pytest.raises(XtceParseError, match="not allowed"):
        loads(xml)


def test_resource_limits() -> None:
    xml = f'<SpaceSystem xmlns="{XTCE_1_3_NAMESPACE}" name="x"><SpaceSystem name="y"/></SpaceSystem>'
    with pytest.raises(XtceResourceLimitError, match="size"):
        loads(xml, options=XtceLoadOptions(max_document_bytes=10))
    with pytest.raises(XtceResourceLimitError, match="element count"):
        loads(xml, options=XtceLoadOptions(max_elements=1))
    with pytest.raises(XtceResourceLimitError, match="depth"):
        loads(xml, options=XtceLoadOptions(max_depth=1))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_document_bytes": 0},
        {"max_elements": True},
        {"max_depth": 0},
    ],
)
def test_invalid_options(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        XtceLoadOptions(**kwargs)


def test_unsupported_recognized_construct_has_element_path() -> None:
    xml = f"""<SpaceSystem xmlns="{XTCE_1_3_NAMESPACE}" name="Satellite">
      <TelemetryMetaData><ParameterTypeSet>
        <ArrayParameterType name="array" arrayTypeRef="u8"/>
      </ParameterTypeSet></TelemetryMetaData>
    </SpaceSystem>"""

    with pytest.raises(UnsupportedXtceFeatureError) as caught:
        loads(xml, source_name="unsupported.xml")

    assert caught.value.source_name == "unsupported.xml"
    assert caught.value.element_path is not None
    assert "ArrayParameterType" in str(caught.value)


def test_unusable_inputs_and_files(tmp_path: Path) -> None:
    with pytest.raises(XtceParseError, match="must be"):
        loads(object())  # type: ignore[arg-type]
    view = memoryview(b"x")
    view.release()
    with pytest.raises(XtceParseError, match="usable"):
        loads(view)
    with pytest.raises(XtceParseError):
        load(tmp_path / "missing.xml")


def test_public_exports() -> None:
    import asterion.xtce as xtce

    assert len(xtce.__all__) == len(set(xtce.__all__))
    assert all(hasattr(xtce, name) for name in xtce.__all__)


def test_remaining_scalar_types_and_enumeration_alarm() -> None:
    xml = f"""<SpaceSystem xmlns="{XTCE_1_3_NAMESPACE}" name="Satellite">
      <TelemetryMetaData><ParameterTypeSet>
        <FloatParameterType name="float_t"><UnitSet/><FloatDataEncoding sizeInBits="32" byteOrder="leastSignificantByteFirst"/></FloatParameterType>
        <BooleanParameterType name="bool_t"><IntegerDataEncoding sizeInBits="8"/></BooleanParameterType>
        <BinaryParameterType name="binary_t"><BinaryDataEncoding sizeInBits="16"/></BinaryParameterType>
        <StringParameterType name="ascii_t"><StringDataEncoding sizeInBits="16"/></StringParameterType>
        <EnumeratedParameterType name="state_t"><EnumerationList>
          <Enumeration value="2" label="BAD"/>
        </EnumerationList><IntegerDataEncoding sizeInBits="8"/>
          <DefaultAlarm><EnumerationAlarmList>
            <EnumerationAlarm enumerationLabel="BAD" alarmLevel="critical"/>
          </EnumerationAlarmList></DefaultAlarm>
        </EnumeratedParameterType>
      </ParameterTypeSet><ParameterSet>
        <Parameter name="float" parameterTypeRef="float_t"/>
        <Parameter name="bool" parameterTypeRef="bool_t"/>
        <Parameter name="binary" parameterTypeRef="binary_t"/>
        <Parameter name="ascii" parameterTypeRef="ascii_t"><AliasSet><Alias alias="TEXT"/></AliasSet></Parameter>
        <Parameter name="state" parameterTypeRef="state_t"/>
      </ParameterSet><ContainerSet><SequenceContainer name="packet"><EntryList>
        <ParameterRefEntry parameterRef="float"/><ParameterRefEntry parameterRef="bool"/>
        <ParameterRefEntry parameterRef="binary"/><ParameterRefEntry parameterRef="ascii"/>
        <ParameterRefEntry parameterRef="state"/>
      </EntryList></SequenceContainer></ContainerSet></TelemetryMetaData>
    </SpaceSystem>"""
    import struct

    result = loads(xml).decode(
        struct.pack("<f", 1.5) + b"\x01\xaa\xbbOK\x02",
        root_container="/Satellite/packet",
    )

    assert result.parameters[0].value == 1.5
    assert result.parameters[1].value is True
    assert result.parameters[2].value == b"\xaa\xbb"
    assert result.parameters[3].value == "OK"
    assert result.parameters[4].alarm_severity is AlarmSeverity.CRITICAL
    assert result.parameters[4].value == EnumeratedValue(2, "BAD")
    assert result.container.name == QualifiedName.parse("/Satellite/packet")
    assert loads(xml).parameter("TEXT").name == QualifiedName.parse("/Satellite/ascii")


def unsupported_type_document(body: str) -> str:
    return f"""<SpaceSystem xmlns="{XTCE_1_3_NAMESPACE}" name="Satellite">
      <TelemetryMetaData><ParameterTypeSet>{body}</ParameterTypeSet></TelemetryMetaData>
    </SpaceSystem>"""


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            '<IntegerParameterType name="x"><IntegerDataEncoding sizeInBits="8" encoding="signMagnitude"/></IntegerParameterType>',
            "sign-magnitude",
        ),
        (
            '<IntegerParameterType name="x"><IntegerDataEncoding sizeInBits="8" encoding="bcd"/></IntegerParameterType>',
            "integer encoding",
        ),
        (
            '<IntegerParameterType name="x"><IntegerDataEncoding sizeInBits="8" byteOrder="middleEndian"/></IntegerParameterType>',
            "byte order",
        ),
        (
            '<StringParameterType name="x"><StringDataEncoding sizeInBits="8" encoding="UTF-16"/></StringParameterType>',
            "string encoding",
        ),
        (
            '<BinaryParameterType name="x"><BinaryDataEncoding/></BinaryParameterType>',
            "fixed size",
        ),
        (
            '<BinaryParameterType name="x"><BinaryDataEncoding><SizeInBits><DynamicValue/></SizeInBits></BinaryDataEncoding></BinaryParameterType>',
            "dynamic encoded sizes",
        ),
        (
            '<FloatParameterType name="x"><UnitSet><Unit>m</Unit><Unit>s</Unit></UnitSet><FloatDataEncoding sizeInBits="32"/></FloatParameterType>',
            "compound units",
        ),
        (
            '<FloatParameterType name="x"><UnitSet><Unit factor="2">m</Unit></UnitSet><FloatDataEncoding sizeInBits="32"/></FloatParameterType>',
            "scaled or offset units",
        ),
        (
            '<IntegerParameterType name="x"><IntegerDataEncoding sizeInBits="8"><DefaultCalibrator><SplineCalibrator/></DefaultCalibrator></IntegerDataEncoding></IntegerParameterType>',
            "non-polynomial",
        ),
        (
            '<IntegerParameterType name="x"><IntegerDataEncoding sizeInBits="8"><DefaultCalibrator><PolynomialCalibrator/></DefaultCalibrator></IntegerDataEncoding></IntegerParameterType>',
            "requires terms",
        ),
        (
            '<RelativeTimeParameterType name="x"/>',
            "numeric encoding",
        ),
        (
            '<AbsoluteTimeParameterType name="x"><ReferenceTime><Epoch>UNKNOWN</Epoch></ReferenceTime><IntegerDataEncoding sizeInBits="8"/></AbsoluteTimeParameterType>',
            "time epoch",
        ),
    ],
)
def test_strict_unsupported_and_invalid_types(body: str, message: str) -> None:
    with pytest.raises(XtceMappingError, match=message):
        loads(unsupported_type_document(body))


def test_mapping_failures_are_wrapped_with_source() -> None:
    unresolved = f"""<SpaceSystem xmlns="{XTCE_1_3_NAMESPACE}" name="Satellite">
      <TelemetryMetaData><ParameterSet><Parameter name="x" parameterTypeRef="missing"/></ParameterSet></TelemetryMetaData>
    </SpaceSystem>"""
    with pytest.raises(XtceMappingError) as caught:
        loads(unresolved, source_name="unresolved.xml")
    assert caught.value.__cause__ is not None
    assert caught.value.source_name == "unresolved.xml"

    bad_coefficient = unsupported_type_document(
        '<IntegerParameterType name="x"><IntegerDataEncoding sizeInBits="8"><DefaultCalibrator><PolynomialCalibrator><Term exponent="0" coefficient="nope"/></PolynomialCalibrator></DefaultCalibrator></IntegerDataEncoding></IntegerParameterType>'
    )
    with pytest.raises(XtceMappingError, match="invalid XTCE value"):
        loads(bad_coefficient)


@pytest.mark.parametrize(
    "entry",
    [
        '<ContainerRefEntry containerRef="other"/>',
        '<ParameterRefEntry parameterRef="value"><LocationInContainerInBits referenceLocation="previousEntry"><FixedValue>1</FixedValue></LocationInContainerInBits></ParameterRefEntry>',
    ],
)
def test_unsupported_container_entries(entry: str) -> None:
    xml = f"""<SpaceSystem xmlns="{XTCE_1_3_NAMESPACE}" name="Satellite">
      <TelemetryMetaData><ParameterTypeSet><IntegerParameterType name="u8"><IntegerDataEncoding sizeInBits="8"/></IntegerParameterType></ParameterTypeSet>
      <ParameterSet><Parameter name="value" parameterTypeRef="u8"/></ParameterSet>
      <ContainerSet><SequenceContainer name="packet"><EntryList>{entry}</EntryList></SequenceContainer></ContainerSet>
      </TelemetryMetaData></SpaceSystem>"""
    with pytest.raises(UnsupportedXtceFeatureError):
        loads(xml)


def test_invalid_comparison_and_enumeration_alarm() -> None:
    invalid_comparison = telemetry_document().replace(
        'comparisonOperator="=="', 'comparisonOperator="contains"'
    )
    with pytest.raises(UnsupportedXtceFeatureError, match="comparison operator"):
        loads(invalid_comparison)

    invalid_alarm = unsupported_type_document(
        '<EnumeratedParameterType name="state"><EnumerationList><Enumeration value="1" label="OK"/></EnumerationList><IntegerDataEncoding sizeInBits="8"/><DefaultAlarm><EnumerationAlarmList><EnumerationAlarm enumerationLabel="MISSING" alarmLevel="warning"/></EnumerationAlarmList></DefaultAlarm></EnumeratedParameterType>'
    )
    with pytest.raises(XtceMappingError, match="unknown label"):
        loads(invalid_alarm)


def test_file_size_limit_is_checked_before_read(tmp_path: Path) -> None:
    path = tmp_path / "large.xml"
    path.write_text(telemetry_document(), encoding="utf-8")
    with pytest.raises(XtceResourceLimitError, match="document size"):
        load(path, options=XtceLoadOptions(max_document_bytes=10))
