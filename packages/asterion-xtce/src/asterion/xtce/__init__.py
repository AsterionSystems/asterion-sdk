"""Strict XTCE telemetry loading for :mod:`asterion.mdb`."""

from .errors import (
    UnsupportedXtceFeatureError,
    XtceError,
    XtceMappingError,
    XtceParseError,
    XtceResourceLimitError,
)
from .loader import (
    XTCE_1_2_NAMESPACE,
    XTCE_1_3_NAMESPACE,
    XtceLoadOptions,
    load,
    loads,
)

__all__ = [
    "XTCE_1_2_NAMESPACE",
    "XTCE_1_3_NAMESPACE",
    "UnsupportedXtceFeatureError",
    "XtceError",
    "XtceLoadOptions",
    "XtceMappingError",
    "XtceParseError",
    "XtceResourceLimitError",
    "load",
    "loads",
]
