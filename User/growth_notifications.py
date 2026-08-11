from contextvars import ContextVar


_growth_awards = ContextVar('growth_awards', default=None)


def begin_growth_awards():
    return _growth_awards.set([])


def record_growth_award(points):
    awards = _growth_awards.get()
    if awards is not None and points > 0:
        awards.append(points)


def growth_award_total():
    return sum(_growth_awards.get() or [])


def reset_growth_awards(token):
    _growth_awards.reset(token)
