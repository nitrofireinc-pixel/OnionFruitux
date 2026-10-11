"""python -m onionfruitux"""

from __future__ import annotations

import sys

from onionfruitux.cli import main

# Linux task comm is the interpreter name after exec. Name this process
# onionfruitux (fits in the 15-character comm field) so the running window
# matches the desktop file instead of a generic Python icon.
_PR_SET_NAME = 15


def _name_process() -> None:
    if sys.platform != "linux":
        return
    try:
        import ctypes

        ctypes.CDLL(None).prctl(_PR_SET_NAME, b"onionfruitux", 0, 0, 0)
    except (AttributeError, OSError, TypeError):
        return


if __name__ == "__main__":
    _name_process()
    sys.exit(main())
