class Choice:
    @classmethod
    def to_choices(cls):
        return [(v, v) for k, v in cls.__dict__.items() if not k.startswith('_')]
