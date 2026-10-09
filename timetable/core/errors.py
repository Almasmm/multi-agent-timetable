class TimetableError(Exception):
    """Base class for errors with a human-readable explanation."""


class DataError(TimetableError):
    pass


class ToolError(TimetableError):
    pass


class LimitExceeded(TimetableError):
    """Raised when a step / time / loop limit is hit (anti-looping guard)."""
