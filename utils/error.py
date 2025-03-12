from utils.code import Code


class Error(Exception):
    __ERRORS = set()

    """Base class for exceptions in this module."""
    def __init__(self, message: str, code=Code.OK, user_message=None, identifier=None):
        super().__init__(message)

        self.__identifier = identifier or None
        self.message = message
        self.code = code
        self.details = []
        self.user_message = user_message or message

    @property
    def identifier(self):
        return self.__identifier

    @identifier.setter
    def identifier(self, value):
        if self.__identifier is not None:
            raise AttributeError('Identifier is already set')
        if value in self.__ERRORS:
            raise AttributeError(f'Conflict error identifier: {value}')
        self.__identifier = value
        self.__ERRORS.add(value)

    def to_json(self):
        return {
            'message': self.message,
            'code': self.code,
            'details': self.details,
            'user_message': self.user_message,
            'identifier': self.identifier,
        }

    def __call__(self, details=None, user_message=None, **kwargs):
        message = self.message.format(**kwargs)
        if not user_message:
            user_message = self.user_message.format(**kwargs)

        if details and not isinstance(details, str):
            details = str(details)
        if user_message and not isinstance(user_message, str):
            user_message = str(user_message)
        error = Error(message, code=self.code, user_message=user_message, identifier=self.identifier)
        error.details = self.details.copy()
        if details is not None:
            error.details.append(details)
        return error

    @classmethod
    def register(cls, class_):
        class_name = class_.__name__
        if not class_name.upper().endswith('ERRORS'):
            raise AttributeError('Error class name should end with "Errors"')
        class_name = class_name[:-6].upper()
        for name in class_.__dict__:  # type: str
            e = getattr(class_, name)
            if not isinstance(e, Error):
                continue
            e.identifier = f'{class_name}@{name}'

        return class_


OK = Error('OK', code=Code.OK, identifier='OK')
