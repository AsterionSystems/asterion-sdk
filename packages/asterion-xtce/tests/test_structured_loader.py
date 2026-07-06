import pytest
from asterion.mdb import AggregateValue, ArrayValue
from asterion.xtce import (
    XTCE_1_2_NAMESPACE,
    XTCE_1_3_NAMESPACE,
    UnsupportedXtceFeatureError,
    XtceLoadOptions,
    XtceMappingError,
    XtceResourceLimitError,
    loads,
)


def document(
    types: str, parameters: str, entries: str, namespace: str = XTCE_1_3_NAMESPACE
) -> str:
    return f"""<SpaceSystem xmlns="{namespace}" name="Satellite">
      <TelemetryMetaData><ParameterTypeSet>{types}</ParameterTypeSet>
      <ParameterSet>{parameters}</ParameterSet>
      <ContainerSet><SequenceContainer name="packet"><EntryList>{entries}</EntryList></SequenceContainer></ContainerSet>
      </TelemetryMetaData></SpaceSystem>"""


def integer_type(name: str = "u8", *, calibrator: bool = False) -> str:
    calibration = ""
    if calibrator:
        calibration = """<DefaultCalibrator><PolynomialCalibrator>
          <Term exponent="1" coefficient="2"/>
        </PolynomialCalibrator></DefaultCalibrator>"""
    return f'<IntegerParameterType name="{name}"><IntegerDataEncoding sizeInBits="8">{calibration}</IntegerDataEncoding></IntegerParameterType>'


def dynamic_value(
    parameter: str, *, calibrated: bool = True, slope: str = "1", intercept: str = "0"
) -> str:
    value = str(calibrated).lower()
    return f"""<DynamicValue>
      <ParameterInstanceRef parameterRef="{parameter}" useCalibratedValue="{value}"/>
      <LinearAdjustment slope="{slope}" intercept="{intercept}"/>
    </DynamicValue>"""


@pytest.mark.parametrize("namespace", [XTCE_1_3_NAMESPACE, XTCE_1_2_NAMESPACE])
def test_fixed_and_dynamic_arrays_preserve_raw_selector(namespace: str) -> None:
    types = (
        integer_type("count_t", calibrator=True)
        + integer_type()
        + f"""
      <ArrayParameterType name="raw_t" arrayTypeRef="u8"><DimensionList><Dimension>
        <Size>{dynamic_value("count", calibrated=False)}</Size>
      </Dimension></DimensionList></ArrayParameterType>
      <ArrayParameterType name="engineering_t" arrayTypeRef="u8"><DimensionList><Dimension>
        <Size>{dynamic_value("count")}</Size>
      </Dimension></DimensionList></ArrayParameterType>"""
    )
    parameters = """<Parameter name="count" parameterTypeRef="count_t"/>
      <Parameter name="raw" parameterTypeRef="raw_t"/>
      <Parameter name="engineering" parameterTypeRef="engineering_t"/>"""
    entries = """<ParameterRefEntry parameterRef="count"/>
      <ParameterRefEntry parameterRef="raw"/>
      <ParameterRefEntry parameterRef="engineering"/>"""

    result = loads(document(types, parameters, entries, namespace)).decode(
        b"\x02\x01\x02\x03\x04\x05\x06", root_container="/Satellite/packet"
    )

    raw = result.parameters[1].value
    engineering = result.parameters[2].value
    assert isinstance(raw, ArrayValue)
    assert isinstance(engineering, ArrayValue)
    assert [item.value for item in raw] == [1, 2]
    assert [item.value for item in engineering] == [3, 4, 5, 6]


def test_multidimensional_array_maps_to_nested_arrays() -> None:
    types = (
        integer_type()
        + """
      <ArrayParameterType name="matrix_t" arrayTypeRef="u8"><DimensionList>
        <Dimension><StartingIndex><FixedValue>0</FixedValue></StartingIndex><Size><FixedValue>2</FixedValue></Size></Dimension>
        <Dimension><EndingIndex><FixedValue>2</FixedValue></EndingIndex></Dimension>
      </DimensionList></ArrayParameterType>"""
    )
    result = loads(
        document(
            types,
            '<Parameter name="matrix" parameterTypeRef="matrix_t"/>',
            '<ParameterRefEntry parameterRef="matrix"/>',
        )
    ).decode(bytes(range(1, 7)), root_container="/Satellite/packet")

    outer = result.parameters[0].value
    assert isinstance(outer, ArrayValue)
    assert len(outer) == 2
    assert all(isinstance(item.value, ArrayValue) for item in outer)
    assert [element.value for element in outer.elements[1].value] == [4, 5, 6]  # type: ignore[union-attr]


def test_arrays_of_aggregates_and_nested_aggregates() -> None:
    types = (
        integer_type()
        + """
      <AggregateParameterType name="pair_t"><MemberList>
        <Member name="x" typeRef="u8"/><Member name="y" typeRef="u8"/>
      </MemberList></AggregateParameterType>
      <AggregateParameterType name="wrapped_t"><MemberList>
        <Member name="pair" typeRef="pair_t"/>
      </MemberList></AggregateParameterType>
      <ArrayParameterType name="records_t" arrayTypeRef="wrapped_t"><DimensionList>
        <Dimension><Size><FixedValue>2</FixedValue></Size></Dimension>
      </DimensionList></ArrayParameterType>"""
    )
    result = loads(
        document(
            types,
            '<Parameter name="records" parameterTypeRef="records_t"/>',
            '<ParameterRefEntry parameterRef="records"/>',
        )
    ).decode(b"\x01\x02\x03\x04", root_container="/Satellite/packet")

    records = result.parameters[0].value
    assert isinstance(records, ArrayValue)
    first = records.elements[0].value
    assert isinstance(first, AggregateValue)
    pair = first["pair"].value
    assert isinstance(pair, AggregateValue)
    assert pair["x"].value == 1
    assert pair["y"].value == 2


def test_dynamic_binary_and_string_sizes() -> None:
    types = (
        integer_type("length_t")
        + f"""
      <BinaryParameterType name="blob_t"><BinaryDataEncoding><SizeInBits>
        {dynamic_value("length", slope="8")}
      </SizeInBits></BinaryDataEncoding></BinaryParameterType>
      <StringParameterType name="text_t"><StringDataEncoding encoding="UTF-8"><SizeInBits>
        {dynamic_value("length", slope="8")}
      </SizeInBits></StringDataEncoding></StringParameterType>"""
    )
    parameters = """<Parameter name="length" parameterTypeRef="length_t"/>
      <Parameter name="blob" parameterTypeRef="blob_t"/>
      <Parameter name="text" parameterTypeRef="text_t"/>"""
    entries = """<ParameterRefEntry parameterRef="length"/>
      <ParameterRefEntry parameterRef="blob"/><ParameterRefEntry parameterRef="text"/>"""

    result = loads(document(types, parameters, entries)).decode(
        b"\x02\xaa\xbbHi", root_container="/Satellite/packet"
    )

    assert result.parameters[1].value == b"\xaa\xbb"
    assert result.parameters[2].value == "Hi"


def test_fixed_and_dynamic_repeat_entries() -> None:
    types = integer_type("count_t") + integer_type()
    parameters = """<Parameter name="count" parameterTypeRef="count_t"/>
      <Parameter name="value" parameterTypeRef="u8"/>"""
    entries = f"""<ParameterRefEntry parameterRef="count"/>
      <RepeatEntry name="dynamic"><Count>{dynamic_value("count")}</Count><EntryList>
        <ParameterRefEntry parameterRef="value"/>
      </EntryList></RepeatEntry>
      <RepeatEntry><Count><FixedValue>2</FixedValue></Count><EntryList>
        <ParameterRefEntry parameterRef="value"/>
      </EntryList></RepeatEntry>"""

    result = loads(document(types, parameters, entries)).decode(
        b"\x02\x01\x02\x03\x04", root_container="/Satellite/packet"
    )

    assert [row[0].value for row in result.repeats_by_name["dynamic"].rows] == [1, 2]
    assert [row[0].value for row in result.repeats_by_name["repeat_3"].rows] == [3, 4]


def test_contextual_polynomial_calibrator() -> None:
    types = (
        integer_type("mode_t")
        + """
      <IntegerParameterType name="value_t"><IntegerDataEncoding sizeInBits="8">
        <DefaultCalibrator><PolynomialCalibrator><Term exponent="1" coefficient="2"/></PolynomialCalibrator></DefaultCalibrator>
        <ContextCalibratorList><ContextCalibrator>
          <ContextMatch><ComparisonList><Comparison parameterRef="mode" comparisonOperator="==" value="1"/></ComparisonList></ContextMatch>
          <Calibrator><PolynomialCalibrator><Term exponent="1" coefficient="4"/></PolynomialCalibrator></Calibrator>
        </ContextCalibrator></ContextCalibratorList>
      </IntegerDataEncoding></IntegerParameterType>"""
    )
    parameters = """<Parameter name="mode" parameterTypeRef="mode_t"/>
      <Parameter name="value" parameterTypeRef="value_t"/>"""
    entries = '<ParameterRefEntry parameterRef="mode"/><ParameterRefEntry parameterRef="value"/>'

    database = loads(document(types, parameters, entries))
    assert (
        database.decode(b"\x00\x03", root_container="/Satellite/packet")
        .parameters[1]
        .value
        == 6.0
    )
    assert (
        database.decode(b"\x01\x03", root_container="/Satellite/packet")
        .parameters[1]
        .value
        == 12.0
    )


@pytest.mark.parametrize(
    ("fragment", "message"),
    [
        (
            '<ArrayParameterType name="x" arrayTypeRef="u8"><DimensionList><Dimension><StartingIndex><FixedValue>1</FixedValue></StartingIndex><Size><FixedValue>2</FixedValue></Size></Dimension></DimensionList></ArrayParameterType>',
            "starting index",
        ),
        (
            '<ArrayParameterType name="x" arrayTypeRef="u8"><DimensionList/></ArrayParameterType>',
            "at least one",
        ),
        (
            '<ArrayParameterType name="x" arrayTypeRef="u8"><DimensionList><Dimension><Size><DynamicValue><ParameterInstanceRef parameterRef="count"/><LinearAdjustment slope="0.5"/></DynamicValue></Size></Dimension></DimensionList></ArrayParameterType>',
            "noninteger",
        ),
        (
            '<AggregateParameterType name="x"><MemberList><Member name="bad"/></MemberList></AggregateParameterType>',
            "typeRef",
        ),
    ],
)
def test_invalid_structured_definitions(fragment: str, message: str) -> None:
    with pytest.raises((XtceMappingError, UnsupportedXtceFeatureError), match=message):
        loads(document(integer_type() + fragment, "", ""))


def test_structured_resource_limits() -> None:
    array = """<ArrayParameterType name="x" arrayTypeRef="u8"><DimensionList><Dimension>
      <Size><FixedValue>5</FixedValue></Size>
    </Dimension></DimensionList></ArrayParameterType>"""
    with pytest.raises(XtceResourceLimitError, match="dimension"):
        loads(
            document(integer_type() + array, "", ""),
            options=XtceLoadOptions(max_dynamic_elements=4),
        )

    repeat = """<RepeatEntry><Count><FixedValue>5</FixedValue></Count><EntryList>
      <ParameterRefEntry parameterRef="value"/>
    </EntryList></RepeatEntry>"""
    with pytest.raises(XtceResourceLimitError, match="dimension"):
        loads(
            document(
                integer_type(),
                '<Parameter name="value" parameterTypeRef="u8"/>',
                repeat,
            ),
            options=XtceLoadOptions(max_repeat_count=4),
        )


def test_nested_and_non_parameter_repeat_entries_are_rejected() -> None:
    nested = """<RepeatEntry><Count><FixedValue>1</FixedValue></Count><EntryList>
      <RepeatEntry><Count><FixedValue>1</FixedValue></Count><EntryList/></RepeatEntry>
    </EntryList></RepeatEntry>"""
    with pytest.raises(UnsupportedXtceFeatureError, match="nested"):
        loads(document(integer_type(), "", nested))
