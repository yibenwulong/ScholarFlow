class ScholarError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable=False, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


def not_found(resource: str):
    return ScholarError(404, 'not_found', f'{resource} not found')


def revision_conflict(current: int):
    return ScholarError(409, 'revision_conflict', 'The resource has changed; reload before saving',
                        details={'current_revision': current})
