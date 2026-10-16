import base64
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def application_icon():
    image = Path(__file__).with_name('assets').joinpath('frienden-180.png').read_bytes()
    return 'data:image/png;base64,' + base64.b64encode(image).decode('ascii')
