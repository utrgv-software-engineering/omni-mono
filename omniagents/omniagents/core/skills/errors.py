class SkillError(Exception):
    pass


class ParseError(SkillError):
    pass


class ValidationError(SkillError):
    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors if errors is not None else [message]
