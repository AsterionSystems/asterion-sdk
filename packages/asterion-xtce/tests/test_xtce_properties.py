from contextlib import suppress

from asterion.mdb import ArrayValue
from asterion.xtce import XTCE_1_3_NAMESPACE, XtceError, loads
from hypothesis import given, settings
from hypothesis import strategies as st


@settings(max_examples=100, derandomize=True, deadline=None)
@given(data=st.binary(max_size=256))
def test_arbitrary_bounded_xml_never_leaks_incidental_errors(data: bytes) -> None:
    with suppress(XtceError):
        loads(data)


@settings(max_examples=50, derandomize=True, deadline=None)
@given(
    rows=st.integers(min_value=0, max_value=4),
    columns=st.integers(min_value=0, max_value=4),
    data=st.data(),
)
def test_bounded_multidimensional_arrays_round_trip(
    rows: int, columns: int, data: st.DataObject
) -> None:
    values = data.draw(
        st.lists(
            st.integers(min_value=0, max_value=255),
            min_size=rows * columns,
            max_size=rows * columns,
        )
    )
    xml = f"""<SpaceSystem xmlns="{XTCE_1_3_NAMESPACE}" name="Satellite">
      <TelemetryMetaData><ParameterTypeSet>
        <IntegerParameterType name="u8"><IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
        <ArrayParameterType name="matrix_t" arrayTypeRef="u8"><DimensionList>
          <Dimension><Size><FixedValue>{rows}</FixedValue></Size></Dimension>
          <Dimension><Size><FixedValue>{columns}</FixedValue></Size></Dimension>
        </DimensionList></ArrayParameterType>
      </ParameterTypeSet><ParameterSet><Parameter name="matrix" parameterTypeRef="matrix_t"/></ParameterSet>
      <ContainerSet><SequenceContainer name="packet"><EntryList><ParameterRefEntry parameterRef="matrix"/></EntryList></SequenceContainer></ContainerSet>
      </TelemetryMetaData></SpaceSystem>"""

    matrix = (
        loads(xml)
        .decode(bytes(values), root_container="/Satellite/packet")
        .parameters[0]
        .value
    )

    assert isinstance(matrix, ArrayValue)
    assert len(matrix) == rows
    flattened: list[int] = []
    for row in matrix:
        assert isinstance(row.value, ArrayValue)
        flattened.extend(item.value for item in row.value)  # type: ignore[arg-type]
    assert flattened == values
