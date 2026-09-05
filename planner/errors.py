class AppError(Exception):
    """A safe, actionable error; never put source documents or credentials here."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
