"""Domain exceptions. The API maps each one to an HTTP status and a consistent JSON error body."""


class ServiceError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(ServiceError):
    status_code = 404
    code = "not_found"


class Unauthorized(ServiceError):
    status_code = 401
    code = "unauthorized"


class PermissionDenied(ServiceError):
    status_code = 403
    code = "forbidden"


class WorkflowError(ServiceError):
    status_code = 409
    code = "invalid_state"


class FileRejected(ServiceError):
    status_code = 422
    code = "invalid_file"
