"""Mission database exception hierarchy."""


class MdbError(Exception):
    """Base exception for mission database failures."""


class MdbValidationError(MdbError, ValueError):
    """Raised when mission database definitions are invalid."""


class ReferenceResolutionError(MdbValidationError):
    """Raised when a definition reference cannot be resolved uniquely."""


class MdbDecodeError(MdbError, ValueError):
    """Raised when telemetry bytes cannot be decoded."""


class InsufficientDataError(MdbDecodeError):
    """Raised when a field extends beyond the available input."""

    required_bits: int
    available_bits: int

    def __init__(self, *, required_bits: int, available_bits: int) -> None:
        self.required_bits = required_bits
        self.available_bits = available_bits
        super().__init__(
            f"field requires {required_bits} bits, but only {available_bits} are available"
        )


class MdbEvaluationError(MdbDecodeError):
    """Base exception for runtime calibration, validity, and alarm evaluation."""


class CalibrationSelectionError(MdbEvaluationError):
    """Raised when contextual calibration cannot be selected unambiguously."""


class CalibrationError(MdbEvaluationError):
    """Raised when a selected calibrator cannot evaluate a raw value."""


class ValidityEvaluationError(MdbEvaluationError):
    """Raised when parameter validity criteria cannot be evaluated."""


class AlarmEvaluationError(MdbEvaluationError):
    """Raised when alarm state cannot be evaluated."""


class DynamicDimensionError(MdbDecodeError):
    """Raised when a dynamic dimension cannot be resolved safely."""


class StructureLimitError(MdbDecodeError):
    """Raised when structured decoding exceeds a configured resource limit."""


class ContainerSelectionError(MdbDecodeError):
    """Base exception for derived-container selection failures."""


class NoMatchingContainerError(ContainerSelectionError):
    """Raised when no derived container restrictions match."""


class AmbiguousContainerError(ContainerSelectionError):
    """Raised when multiple derived container restrictions match."""
