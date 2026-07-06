"""XTCE loader exception hierarchy."""

from __future__ import annotations


class XtceError(Exception):
    """Base exception for XTCE loading failures."""

    source_name: str
    element_path: str | None

    def __init__(
        self, message: str, *, source_name: str, element_path: str | None = None
    ) -> None:
        self.source_name = source_name
        self.element_path = element_path
        location = source_name
        if element_path is not None:
            location += f":{element_path}"
        super().__init__(f"{location}: {message}")


class XtceParseError(XtceError, ValueError):
    """Raised when XML or an XTCE document envelope is malformed."""


class XtceMappingError(XtceError, ValueError):
    """Raised when XTCE definitions cannot be mapped into the MDB."""


class UnsupportedXtceFeatureError(XtceMappingError):
    """Raised for recognized XTCE semantics outside the supported subset."""


class XtceResourceLimitError(XtceParseError):
    """Raised when bounded XML parsing limits are exceeded."""
