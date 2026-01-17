"""
Custom exceptions for GenMo services.
"""

from typing import Optional


class GenMoError(Exception):  # Changed from GenMoException
    """
    Base exception for all GenMo errors.
    
    Attributes:
        code: Error code for programmatic handling
        message: Human-readable error message
        details: Additional error details
    """
    
    code: str = "GENMO_ERROR"
    status_code: int = 500
    
    def __init__(
        self,
        message: str = "An error occurred",
        code: Optional[str] = None,
        details: Optional[dict] = None
    ):
        self.message = message
        if code:
            self.code = code
        self.details = details or {}
        super().__init__(self.message)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details
            }
        }


class ValidationError(GenMoError):
    """Raised when request data is invalid."""
    code = "VALIDATION_ERROR"
    status_code = 400


class NotFoundError(GenMoError):
    """Raised when a resource is not found."""
    code = "NOT_FOUND"
    status_code = 404


class PermissionDeniedError(GenMoError):
    """Raised when user doesn't have permission."""
    code = "PERMISSION_DENIED"
    status_code = 403


class ConflictError(GenMoError):
    """Raised when there's a resource conflict (e.g., duplicate)."""
    code = "CONFLICT"
    status_code = 409


class ServiceUnavailableError(GenMoError):
    """Raised when an external service is unavailable."""
    code = "SERVICE_UNAVAILABLE"
    status_code = 503


class BusinessRuleError(GenMoError):
    """Raised when a business rule is violated."""
    code = "BUSINESS_RULE_VIOLATION"
    status_code = 422


class InsufficientFundsError(BusinessRuleError):
    """Raised when account has insufficient funds."""
    code = "INSUFFICIENT_FUNDS"


class LimitExceededError(BusinessRuleError):
    """Raised when a limit is exceeded."""
    code = "LIMIT_EXCEEDED"


class InviteExpiredError(BusinessRuleError):
    """Raised when an invite code has expired."""
    code = "INVITE_EXPIRED"


class InviteAlreadyUsedError(BusinessRuleError):
    """Raised when an invite code has already been used."""
    code = "INVITE_ALREADY_USED"


class NotSameBankError(BusinessRuleError):
    """Raised when users are not from the same bank."""
    code = "NOT_SAME_BANK"