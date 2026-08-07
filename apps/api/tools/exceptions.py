"""Typed tool exceptions. Tools never return None on error — they raise one of these."""


class ToolError(Exception):
    pass


class ToolNotFound(ToolError):
    pass


class RecordNotFound(ToolError):
    pass


class ServiceUnavailable(ToolError):
    pass


class PermissionDenied(ToolError):
    pass


class SeatsUnavailable(ToolError):
    pass
