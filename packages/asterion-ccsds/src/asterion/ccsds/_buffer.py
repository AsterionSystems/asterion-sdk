"""Internal helpers for copying supported byte buffers."""


class ByteBufferError(ValueError):
    """Internal error describing an unsupported or unusable byte buffer."""


def copy_bytes(data: object) -> bytes:
    """Copy a supported byte buffer into immutable bytes."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ByteBufferError("must be bytes, bytearray, or memoryview")
    try:
        if isinstance(data, bytes):
            return data
        if isinstance(data, bytearray):
            return bytes(data)
        return data.tobytes()
    except (BufferError, OverflowError, TypeError, ValueError) as error:
        raise ByteBufferError(f"is not a usable byte buffer: {error}") from error
