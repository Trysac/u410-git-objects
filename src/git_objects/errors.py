"""Typed exceptions for git_objects."""

from __future__ import annotations

from typing import Any


class GitObjectsError(Exception):
    """Base exception for all git_objects operations."""

    def __init__(self, message: str, *, code: str, context: dict[str, Any] | None = None) -> None:
        # Ensure error messages never echo more than 200 characters of untrusted input
        truncated_msg = message[:200] if len(message) > 200 else message
        super().__init__(truncated_msg)
        self.message = truncated_msg
        self.code = code
        self.context = context or {}

    def __str__(self) -> str:
        return self.message


class ObjectNotFoundError(GitObjectsError):
    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="object_not_found", context=context)


class CorruptObjectError(GitObjectsError):
    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="corrupt_object", context=context)


class ObjectSizeLimitError(GitObjectsError):
    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="object_too_large", context=context)


class DeltaDepthExceededError(GitObjectsError):
    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="delta_depth_exceeded", context=context)


class InvalidRefError(GitObjectsError):
    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="invalid_ref_name", context=context)


class PackParseError(GitObjectsError):
    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="pack_parse_error", context=context)


class RefUpdateError(GitObjectsError):
    def __init__(
        self, message: str, *, code: str = "ref_mismatch", context: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code=code, context=context)
