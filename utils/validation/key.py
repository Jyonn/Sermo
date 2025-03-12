class Key:
    def __init__(self, name, verbose_name=None, final_name=None):
        self.name = name
        self.verbose_name = verbose_name or name
        self.final_name = final_name or name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if isinstance(other, Key):
            return self.name == other.name
        if isinstance(other, str):
            return self.name == other
        return False

    def copy(self):
        return Key(self.name, self.verbose_name, self.final_name)

    def __str__(self):
        if self.verbose_name != self.name:
            return f'Key({self.name}, {self.verbose_name})'
        return f'Key({self.name})'
