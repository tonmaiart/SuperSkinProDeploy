
import re

SIDE_WORDS = (
    ("left", "right"),
    ("lft", "rgt"),
    ("lft", "rght"),
    ("lf", "rt"),
    ("lt", "rt"),
    ("l", "r"),
)

LEFT, RIGHT = "L", "R"


def _build_index():
    index = {}
    for left, right in SIDE_WORDS:
        for word, other, side in ((left, right, LEFT), (right, left, RIGHT)):
            entry = index.setdefault(word, [])
            if (other, side) not in entry:
                entry.append((other, side))
    return index


_INDEX = _build_index()

_RUN = re.compile(r"[A-Za-z]+")
_CASE_SPLIT = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+")


def _tokens(name):
    spans = []
    for run in _RUN.finditer(name):
        for part in _CASE_SPLIT.finditer(run.group()):
            spans.append((run.start() + part.start(), run.start() + part.end()))
    return spans


def _match_case(template, word):
    if template.isupper() and (len(template) > 1 or len(word) == 1):
        return word.upper()
    if template[:1].isupper():
        return word[:1].upper() + word[1:].lower()
    return word.lower()


def side_swaps(name):
    out = []
    seen = set()

    def _add(candidate, side):
        if candidate != name and candidate not in seen:
            seen.add(candidate)
            out.append((candidate, side))

    hits = []
    for start, end in _tokens(name):
        token = name[start:end]
        for other, side in _INDEX.get(token.lower(), ()):
            hits.append((start, end, _match_case(token, other), side))
    hits.sort(key=lambda h: h[1] - h[0], reverse=True)
    for start, end, repl, side in hits:
        _add(name[:start] + repl + name[end:], side)

    if len({(h[0], h[1]) for h in hits}) > 1:
        sides = {h[3] for h in hits}
        if len(sides) == 1:
            first = {}
            for start, end, repl, _side in hits:
                first.setdefault((start, end), repl)
            swapped = name
            for (start, end), repl in sorted(first.items(), reverse=True):
                swapped = swapped[:start] + repl + swapped[end:]
            _add(swapped, next(iter(sides)))
    return out


def find_counterpart(name, names):
    for candidate, side in side_swaps(name):
        if candidate in names:
            return candidate, side
    return None
