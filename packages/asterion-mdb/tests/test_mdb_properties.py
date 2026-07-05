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
