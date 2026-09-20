"""Stable diagnostics for format failures; operating-system failures use OSError."""

from .models import Diagnostic


class IcadError(Exception):
    """Base class for inspection failures with a machine-readable diagnostic."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(
            f"{diagnostic.code} at byte {diagnostic.byte_offset}: {diagnostic.message}"
        )


class InvalidFormatError(IcadError):
    """The input violates the inspected prefix framing."""


class UnsupportedFormatError(IcadError):
    """The MOD layout is outside the observed 256-byte variant."""


class LimitExceededError(IcadError):
    """A configured file or record size limit was exceeded."""
