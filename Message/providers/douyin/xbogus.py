"""X-Bogus implementation vendored from DLWangSan/douyin_parse.

Upstream commit: 0896c74d1e9368af8ad0b85449a8039b1b3010bd
"""

import base64
import hashlib
import time


class XBogus:
    def __init__(self, user_agent=None):
        self._char_map = [None] * 128
        for index in range(10):
            self._char_map[48 + index] = index
        for index in range(6):
            self._char_map[97 + index] = 10 + index
        self._charset = 'Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe='
        self._ua_key = b'\x00\x01\x0c'
        self.user_agent = user_agent or 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'

    def _md5_str_to_array(self, value):
        if len(value) > 32:
            return [ord(char) for char in value]
        result = []
        for index in range(0, len(value), 2):
            result.append((self._char_map[ord(value[index])] << 4) | self._char_map[ord(value[index + 1])])
        return result

    @staticmethod
    def _md5(data):
        if isinstance(data, str):
            data = [ord(char) for char in data]
        if not isinstance(data, list):
            raise ValueError('Invalid input type')
        return hashlib.md5(bytes(data)).hexdigest()

    def _md5_encrypt(self, value):
        return self._md5_str_to_array(self._md5(self._md5_str_to_array(self._md5(value))))

    @staticmethod
    def _encoding_conversion(a, b, c, e, d, t, f, r, n, o, i, underscore, x, u, s, l, v, h, p):
        values = [a, int(i), b, underscore, c, x, e, u, d, s, t, l, f, v, r, h, n, p, o]
        return bytes(values).decode('ISO-8859-1')

    @staticmethod
    def _rc4_encrypt(key, data):
        state = list(range(256))
        position = 0
        for index in range(256):
            position = (position + state[index] + key[index % len(key)]) % 256
            state[index], state[position] = state[position], state[index]
        index = position = 0
        result = bytearray()
        for byte in data:
            index = (index + 1) % 256
            position = (position + state[index]) % 256
            state[index], state[position] = state[position], state[index]
            result.append(byte ^ state[(state[index] + state[position]) % 256])
        return result

    def _calc(self, first, second, third):
        value = ((first & 255) << 16) | ((second & 255) << 8) | third
        return ''.join((self._charset[(value >> 18) & 63], self._charset[(value >> 12) & 63], self._charset[(value >> 6) & 63], self._charset[value & 63]))

    def get_xbogus(self, url_path):
        encrypted_ua = self._rc4_encrypt(self._ua_key, self.user_agent.encode('ISO-8859-1'))
        first = self._md5_str_to_array(self._md5(base64.b64encode(encrypted_ua).decode('ISO-8859-1')))
        second = self._md5_str_to_array(self._md5(self._md5_str_to_array('d41d8cd98f00b204e9800998ecf8427e')))
        path = self._md5_encrypt(url_path)
        timestamp = int(time.time())
        constant = 536919696
        values = [64, 0.00390625, 1, 12, path[14], path[15], second[14], second[15], first[14], first[15], (timestamp >> 24) & 255, (timestamp >> 16) & 255, (timestamp >> 8) & 255, timestamp & 255, (constant >> 24) & 255, (constant >> 16) & 255, (constant >> 8) & 255, constant & 255]
        checksum = values[0]
        for value in values[1:]:
            checksum ^= int(value) if isinstance(value, float) else value
        values.append(checksum)
        merged = values[::2] + values[1::2]
        encrypted = self._rc4_encrypt(b'\xff', self._encoding_conversion(*merged).encode('ISO-8859-1')).decode('ISO-8859-1')
        garbled = chr(2) + chr(255) + encrypted
        signature = ''.join(self._calc(ord(garbled[index]), ord(garbled[index + 1]), ord(garbled[index + 2])) for index in range(0, len(garbled), 3))
        return f'{url_path}&X-Bogus={signature}', signature, self.user_agent
