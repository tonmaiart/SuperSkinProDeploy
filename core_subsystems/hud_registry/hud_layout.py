
FONT_SIZE = 20
BOTTOM_MARGIN = 40
LINE_HEIGHT = 36
REPORT_ROW = 3
LEFT_MARGIN = 90


def row_y(row: int) -> int:
    return BOTTOM_MARGIN + row * LINE_HEIGHT


def report_y() -> int:
    return row_y(REPORT_ROW)


def above_stack_y() -> int:
    return row_y(REPORT_ROW + 1) + LINE_HEIGHT // 2


def bottom_left() -> tuple[int, int]:
    return LEFT_MARGIN, BOTTOM_MARGIN
