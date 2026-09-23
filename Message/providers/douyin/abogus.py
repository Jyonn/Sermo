"""A-Bogus implementation vendored from DLWangSan/douyin_parse.

Upstream commit: 0896c74d1e9368af8ad0b85449a8039b1b3010bd
"""

from random import choice, randint, random
from re import compile as re_compile
from time import time
from urllib.parse import urlencode
import hashlib

class ABogus:
    _url_encode_filter = re_compile(r'%([0-9A-F]{2})')
    _end_string = 'cus'
    _browser = '1536|742|1536|864|0|0|0|0|1536|864|1536|864|1536|742|24|24|MacIntel'
    _reg_init = [1937774191, 1226093241, 388252375, 3666478592, 2842636476, 372324522, 3817729613, 2969243214]
    _charsets = {
        's4': 'Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe',
    }

    def __init__(self, platform=None):
        self.chunk = []
        self.size = 0
        self.reg = self._reg_init[:]
        self.ua_code = [76, 98, 15, 131, 97, 245, 224, 133, 122, 199, 241, 166, 79, 34, 90, 191, 128, 126, 122, 98, 66, 11, 14, 40, 49, 110, 110, 173, 67, 96, 138, 252]
        self.browser = self._generate_browser_info(platform) if platform else self._browser
        self.browser_len = len(self.browser)
        self.browser_code = [ord(char) for char in self.browser]

    @classmethod
    def _list_1(cls, value=None, a=170, b=85, c=45):
        return cls._random_list(value, a, b, 1, 2, 5, c & a)

    @classmethod
    def _list_2(cls, value=None, a=170, b=85):
        return cls._random_list(value, a, b, 1, 0, 0, 0)

    @classmethod
    def _list_3(cls, value=None, a=170, b=85):
        return cls._random_list(value, a, b, 1, 0, 5, 0)

    @staticmethod
    def _random_list(a=None, b=170, c=85, d=0, e=0, f=0, g=0):
        value = a if a is not None else random() * 10000
        parts = [value, int(value) & 255, int(value) >> 8]
        return [parts[1] & b | d, parts[1] & c | e, parts[2] & b | f, parts[2] & c | g]

    @staticmethod
    def _from_char_code(*args):
        return ''.join(chr(code) for code in args)

    def _generate_string_1(self, first=None, second=None, third=None):
        return self._from_char_code(*self._list_1(first)) + self._from_char_code(*self._list_2(second)) + self._from_char_code(*self._list_3(third))

    def _generate_string_2(self, url_params, method='GET', start_time=0, end_time=0):
        values = self._list_4_list(url_params, method, start_time, end_time)
        checksum = self._end_check_num(values)
        values.extend(self.browser_code)
        values.append(checksum)
        return self._rc4_encrypt(self._from_char_code(*values), 'y')

    def _list_4_list(self, url_params, method='GET', start_time=0, end_time=0):
        start_time = start_time or int(time() * 1000)
        end_time = end_time or start_time + randint(4, 8)
        params_arr = self._generate_params_code(url_params)
        method_arr = self._generate_method_code(method)
        return [
            44, (end_time >> 24) & 255, 0, 0, 0, 0, 24, params_arr[21], method_arr[21], 0,
            self.ua_code[23], (end_time >> 16) & 255, 0, 0, 0, 1, 0, 239, params_arr[22],
            method_arr[22], self.ua_code[24], (end_time >> 8) & 255, 0, 0, 0, 0, end_time & 255,
            0, 0, 14, (start_time >> 24) & 255, (start_time >> 16) & 255, 0,
            (start_time >> 8) & 255, start_time & 255, 3, (end_time // 256 ** 4) & 255, 1,
            (start_time // 256 ** 4) & 255, 1, self.browser_len, 0, 0, 0,
        ]

    @classmethod
    def _generate_method_code(cls, method):
        return cls._sm3_to_array(cls._sm3_to_array(method + cls._end_string))

    def _generate_params_code(self, params):
        return self._sm3_to_array(self._sm3_to_array(params + self._end_string))

    @staticmethod
    def _sm3_to_array(data):
        raw = data.encode('utf-8') if isinstance(data, str) else bytes(data)
        return list(hashlib.new('sm3', raw).digest())

    @staticmethod
    def _end_check_num(values):
        result = 0
        for value in values:
            result ^= value
        return result

    @staticmethod
    def _rc4_encrypt(plaintext, key):
        state = list(range(256))
        position = 0
        for index in range(256):
            position = (position + state[index] + ord(key[index % len(key)])) % 256
            state[index], state[position] = state[position], state[index]
        index = position = 0
        result = []
        for char in plaintext:
            index = (index + 1) % 256
            position = (position + state[index]) % 256
            state[index], state[position] = state[position], state[index]
            result.append(chr(state[(state[index] + state[position]) % 256] ^ ord(char)))
        return ''.join(result)

    @staticmethod
    def _generate_browser_info(platform):
        inner_width = randint(1280, 1920)
        inner_height = randint(720, 1080)
        outer_width = randint(inner_width, 1920)
        outer_height = randint(inner_height, 1080)
        values = [inner_width, inner_height, outer_width, outer_height, 0, choice((0, 30)), 0, 0, outer_width, outer_height, outer_width, outer_height, inner_width, inner_height, 24, 24, platform]
        return '|'.join(str(value) for value in values)

    @classmethod
    def _generate_result(cls, value):
        charset = cls._charsets['s4']
        result = []
        for index in range(0, len(value), 3):
            remaining = len(value) - index
            number = ord(value[index]) << 16
            if remaining >= 2:
                number |= ord(value[index + 1]) << 8
            if remaining >= 3:
                number |= ord(value[index + 2])
            result.extend((charset[(number >> 18) & 63], charset[(number >> 12) & 63]))
            if remaining >= 2:
                result.append(charset[(number >> 6) & 63])
            if remaining >= 3:
                result.append(charset[number & 63])
        result.append('=' * ((4 - len(result) % 4) % 4))
        return ''.join(result)

    def get_value(self, url_params, method='GET'):
        if isinstance(url_params, dict):
            url_params = urlencode(url_params)
        return self._generate_result(self._generate_string_1() + self._generate_string_2(url_params, method))
