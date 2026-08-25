"""Uniform error envelope for the REST layer.

Every API error, whatever raised it, comes back in the same shape:

    {"error": {"type": "validation_error",
               "message": "Enter a valid email address.",
               "detail": {"email": ["Enter a valid email address."]},
               "status_code": 400}}
"""
import logging

from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger("ecommerce")

ERROR_TYPES = {
    400: "bad_request",
    401: "authentication_failed",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    415: "unsupported_media_type",
    429: "throttled",
    500: "server_error",
}


def _first_message(detail):
    """Pull a single human-readable sentence out of a DRF detail structure."""
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        return _first_message(detail[0]) if detail else "Request failed."
    if isinstance(detail, dict):
        for key, value in detail.items():
            message = _first_message(value)
            if key in {"non_field_errors", "detail"}:
                return message
            return f"{key}: {message}"
    return str(detail)


def api_exception_handler(exc, context):
    """DRF ``EXCEPTION_HANDLER``.

    Translates the Django-level exceptions DRF does not handle natively, then
    reshapes every response into the envelope above.
    """
    if isinstance(exc, DjangoValidationError):
        exc = ValidationError(detail=getattr(exc, "message_dict", None) or list(exc.messages))
    elif isinstance(exc, Http404):
        exc = APIException(detail="Not found.")
        exc.status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, PermissionDenied):
        exc = APIException(detail="You do not have permission to perform this action.")
        exc.status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, IntegrityError):
        logger.warning("Integrity error in %s: %s", context.get("view"), exc)
        exc = APIException(detail="That operation conflicts with existing data.")
        exc.status_code = status.HTTP_409_CONFLICT

    response = drf_exception_handler(exc, context)

    if response is None:
        # Unhandled -> let Django's 500 machinery log it with a traceback.
        logger.exception("Unhandled API exception in %s", context.get("view"))
        return None

    status_code = response.status_code
    error_type = "validation_error" if isinstance(exc, ValidationError) else ERROR_TYPES.get(
        status_code, "error"
    )

    payload = {
        "error": {
            "type": error_type,
            "message": _first_message(response.data),
            "detail": response.data,
            "status_code": status_code,
        }
    }
    return Response(payload, status=status_code, headers=dict(response.headers))
