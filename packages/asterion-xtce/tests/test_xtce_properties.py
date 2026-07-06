from contextlib import suppress

from asterion.xtce import XtceError, loads
from hypothesis import given, settings
from hypothesis import strategies as st


@settings(max_examples=100, derandomize=True, deadline=None)
@given(data=st.binary(max_size=256))
def test_arbitrary_bounded_xml_never_leaks_incidental_errors(data: bytes) -> None:
    with suppress(XtceError):
        loads(data)
