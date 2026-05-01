"""Exception handling middleware."""

import traceback
from typing import Union

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from common.exceptions.base import AppException, ValidationException


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers."""

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        """Handle custom application exceptions."""
        return JSONResponse(
            status_code=200,  # Use 200 to keep consistent response format
            content={
                "code": exc.code,
                "message": exc.message,
                "data": None,
            },
        )

    @app.exception_handler(ValidationException)
    async def validation_exception_handler(request: Request, exc: ValidationException):
        """Handle validation exceptions."""
        return JSONResponse(
            status_code=200,
            content={
                "code": exc.code,
                "message": exc.message,
                "data": {"errors": exc.errors},
            },
        )

    @app.exception_handler(ValidationError)
    async def pydantic_validation_exception_handler(request: Request, exc: ValidationError):
        """Handle Pydantic validation errors."""
        errors = []
        for error in exc.errors():
            errors.append({
                "field": ".".join(str(loc) for loc in error["loc"]),
                "message": error["msg"],
            })
        return JSONResponse(
            status_code=200,
            content={
                "code": 422,
                "message": "Validation error",
                "data": {"errors": errors},
            },
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Handle general exceptions."""
        # Log the error for debugging
        traceback.print_exc()

        # Don't expose internal errors in production
        return JSONResponse(
            status_code=200,
            content={
                "code": 500,
                "message": "Internal server error",
                "data": None,
            },
        )