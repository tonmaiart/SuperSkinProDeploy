
import traceback

from ...core.facade import CoreFacade


def report_failure(category: str, where: str, exc: BaseException, *, op=None, message: str = "") -> None:
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
    CoreFacade.debug_log(category, f"{where} raised: {exc!r}\n{detail}")
    try:
        print(f"[SuperSkinPro] {where} failed: {exc!r}\n{detail}")
    except OSError:
        pass
    if op is not None and message:
        op.report({'ERROR'}, message)
