class Jsonify:
    @staticmethod
    def _parse_name(key: str):
        if not isinstance(key, str):
            return None, None

        arrow = key.find('->')
        if arrow == -1:
            yield_key = key
        else:
            yield_key = key[arrow + 2:]
            key = key[:arrow]
        return key, yield_key

    def jsonify(self, *attrs):
        data = dict()
        for key in attrs:  # type: str
            if isinstance(key, tuple):
                args = key[1:]
                key = key[0]
            else:
                args = tuple()

            key, yield_name = self._parse_name(key)

            value = getattr(self, key, None)
            jsonify_func = getattr(self, f'_jsonify_{key}', None)
            if callable(jsonify_func):
                value = jsonify_func(*args)
            data[yield_name] = value
        return data
