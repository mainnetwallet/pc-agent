"""Wait until the screen stops changing.

Why this exists
---------------
Apps, web pages and AI chat sites work asynchronously. Pressing Enter in a chat only
STARTS the answer: the text keeps growing for many seconds. If the agent looks at the
screen right after pressing Enter it sees an empty or half-written answer, cannot tell
that the step is not finished, and moves on (for example by sending the next question
before the previous answer has been written).

A screen that is still changing means "still working". A screen that stayed the same
for a few seconds means "finished". This module measures exactly that.

It has no Windows dependency (only Pillow), and the screen grabber, sleep and clock are
injected, so it can be tested anywhere.
"""

import time
from typing import Any, Callable, Dict, Optional

from PIL import Image, ImageChops

# A screenshot is shrunk to a small grayscale thumbnail before comparing. At this size a
# blinking text cursor or the taskbar clock disappears, while a new line of text, a
# spinner, a page load or a growing chat answer still moves enough pixels to be noticed.
THUMB_SIZE = (192, 108)
PIXEL_DELTA = 12          # gray-level change at which one thumbnail pixel counts as changed
CHANGED_FRACTION = 0.001  # share of thumbnail pixels that must change to count as "changed"


def _thumb(img: Image.Image) -> Image.Image:
    return img.convert("L").resize(THUMB_SIZE, Image.BILINEAR)


def frame_changed(a: Image.Image, b: Image.Image,
                  pixel_delta: int = PIXEL_DELTA,
                  fraction: float = CHANGED_FRACTION) -> bool:
    """True when frame b differs visibly from frame a."""
    hist = ImageChops.difference(_thumb(a), _thumb(b)).histogram()
    changed = sum(hist[pixel_delta + 1:])
    total = THUMB_SIZE[0] * THUMB_SIZE[1]
    return changed / total > fraction


def clamp(value: Any, low: float, high: float, default: float) -> float:
    """Coerce an LLM-supplied number (may be a string, None or junk) into [low, high]."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def wait_until_stable(grab: Callable[[], Optional[Image.Image]],
                      timeout: float = 15.0, quiet_for: float = 2.0,
                      interval: float = 0.5,
                      should_stop: Optional[Callable[[], bool]] = None,
                      sleep: Callable[[float], None] = time.sleep,
                      clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    """Poll the screen until it has not visibly changed for `quiet_for` seconds.

    Each new frame is compared with the last frame that counted as a change (not with the
    previous frame), so a slow trickle of small changes still adds up and is noticed.

    Returns {"stable": bool, "waited": seconds, "changes": n}. stable=False means the
    screen was still changing when `timeout` ran out (or "stopped": True if should_stop
    fired). It never raises for a missing screen: it reports stable with a note.
    """
    start = clock()
    anchor = grab()
    if anchor is None:
        return {"stable": True, "waited": 0.0, "changes": 0, "note": "no screen to watch"}
    quiet_since = start
    changes = 0

    while True:
        now = clock()
        waited = round(now - start, 1)
        if should_stop is not None and should_stop():
            return {"stable": False, "stopped": True, "waited": waited, "changes": changes}
        if now - quiet_since >= quiet_for:
            return {"stable": True, "waited": waited, "changes": changes}
        if now - start >= timeout:
            return {"stable": False, "waited": waited, "changes": changes}
        sleep(interval)
        frame = grab()
        if frame is not None and frame_changed(anchor, frame):
            anchor = frame
            changes += 1
            quiet_since = clock()
