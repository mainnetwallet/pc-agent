"""Desktop control: mouse, keyboard, screenshots, OCR, clipboard.

Uses PyAutoGUI for input, MSS for fast screenshot capture, and optional
Tesseract OCR. All coordinates are in screen pixels and are DPI-aware.
"""

import os
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import mss
import pyautogui
from PIL import Image

from config import (
    PYAUTOGUI_FAILSAFE,
    PYAUTOGUI_PAUSE,
    SCREENSHOT_MAX_DIMENSION,
    SCREENSHOT_QUALITY,
    SCREENSHOTS_DIR,
)

# PyAutoGUI safety
pyautogui.PAUSE = PYAUTOGUI_PAUSE
pyautogui.FAILSAFE = PYAUTOGUI_FAILSAFE  # throw mouse to top-left corner to abort

try:  # OCR is optional; degrade gracefully when Tesseract is absent
    import pytesseract  # type: ignore

    _TESSERACT_AVAILABLE = True
except Exception:  # pragma: no cover - depends on system install
    pytesseract = None
    _TESSERACT_AVAILABLE = False

try:
    import pygetwindow as gw  # type: ignore
except Exception:  # pragma: no cover
    gw = None


@dataclass
class Monitor:
    index: int
    left: int
    top: int
    width: int
    height: int
    primary: bool = False

    def to_dict(self):
        return {
            "index": self.index,
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
            "primary": self.primary,
        }


class DesktopControl:
    """Controls the physical desktop (mouse, keyboard, screen capture)."""

    def __init__(self):
        self._sct = mss.MSS()
        self._last_screenshot: Optional[str] = None

    # ------------------------------------------------------------------ mouse
    def move_to(self, x: int, y: int, duration: float = 0.2) -> None:
        pyautogui.moveTo(x, y, duration=duration)

    def click(self, x: Optional[int] = None, y: Optional[int] = None,
              button: str = "left", clicks: int = 1, interval: float = 0.0) -> None:
        if x is not None and y is not None:
            pyautogui.click(x, y, clicks=clicks, interval=interval, button=button)
        else:
            pyautogui.click(clicks=clicks, interval=interval, button=button)

    def double_click(self, x: Optional[int] = None, y: Optional[int] = None) -> None:
        if x is not None and y is not None:
            pyautogui.doubleClick(x, y)
        else:
            pyautogui.doubleClick()

    def right_click(self, x: Optional[int] = None, y: Optional[int] = None) -> None:
        if x is not None and y is not None:
            pyautogui.rightClick(x, y)
        else:
            pyautogui.rightClick()

    def middle_click(self, x: Optional[int] = None, y: Optional[int] = None) -> None:
        if x is not None and y is not None:
            pyautogui.middleClick(x, y)
        else:
            pyautogui.middleClick()

    def drag_to(self, x: int, y: int, from_x: Optional[int] = None,
                from_y: Optional[int] = None, duration: float = 0.5) -> None:
        if from_x is not None and from_y is not None:
            pyautogui.moveTo(from_x, from_y)
        pyautogui.dragTo(x, y, duration=duration)

    def scroll(self, clicks: int, x: Optional[int] = None, y: Optional[int] = None) -> None:
        pyautogui.scroll(clicks, x=x, y=y)

    def get_mouse_position(self) -> Tuple[int, int]:
        return pyautogui.position()

    # ------------------------------------------------------------------ keyboard
    def type_text(self, text: str, interval: float = 0.01) -> None:
        """Type text at the focus.

        pyautogui.typewrite only knows ASCII keys: it silently SKIPS every other character
        (Bengali, Arabic, accents, emoji, curly quotes...). Such text is pasted through the
        clipboard instead, so what ends up on screen is what the LLM asked for.
        """
        if text.isascii():
            pyautogui.typewrite(text, interval=interval)
        else:
            self._paste_text(text)

    def _paste_text(self, text: str) -> None:
        import pyperclip  # installed with pyautogui
        previous = ""
        try:
            previous = pyperclip.paste()
        except Exception:
            pass
        pyperclip.copy(text)
        time.sleep(0.05)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.2)  # let the target app read the clipboard before it is restored
        if previous:
            try:
                pyperclip.copy(previous)
            except Exception:
                pass

    def press(self, key: str, presses: int = 1, interval: float = 0.0) -> None:
        pyautogui.press(key, presses=presses, interval=interval)

    def key_down(self, key: str) -> None:
        pyautogui.keyDown(key)

    def key_up(self, key: str) -> None:
        pyautogui.keyUp(key)

    def hotkey(self, *keys: str) -> None:
        pyautogui.hotkey(*keys)

    def key_combo(self, keys: List[str]) -> None:
        """Press a key combination (e.g. ['ctrl', 'c'])."""
        for k in keys:
            pyautogui.keyDown(k)
        for k in reversed(keys):
            pyautogui.keyUp(k)

    # ------------------------------------------------------------------ screen
    def get_monitors(self) -> List[Monitor]:
        monitors: List[Monitor] = []
        for i, mon in enumerate(self._sct.monitors):
            # monitors[0] is the "all-in-one" bounding box; skip it when real ones exist
            if i == 0 and len(self._sct.monitors) > 1:
                continue
            monitors.append(Monitor(
                index=i,
                left=mon["left"],
                top=mon["top"],
                width=mon["width"],
                height=mon["height"],
                primary=(i == 1 and len(self._sct.monitors) > 1),
            ))
        if not monitors:
            mon = self._sct.monitors[0]
            monitors.append(Monitor(0, mon["left"], mon["top"], mon["width"],
                                    mon["height"], primary=True))
        return monitors

    def get_primary_monitor(self) -> Monitor:
        for m in self.get_monitors():
            if m.primary:
                return m
        return self.get_monitors()[0]

    def get_screen_size(self) -> Tuple[int, int]:
        return pyautogui.size()

    def screenshot(self, monitor_index: int = 0, save: bool = True,
                   filename: Optional[str] = None) -> Tuple[Optional[str], Optional[Image.Image]]:
        """Capture a screenshot.

        monitor_index: index into get_monitors() (0 = primary/first real monitor).
        Returns (file_path, PIL_Image).
        """
        monitors = self.get_monitors()
        if not monitors:
            return None, None
        idx = min(max(monitor_index, 0), len(monitors) - 1)
        mon = monitors[idx]

        region = {"left": mon.left, "top": mon.top,
                  "width": mon.width, "height": mon.height}
        img = self._sct.grab(region)
        pil = Image.frombytes("RGB", (img.size.width, img.size.height), img.rgb)

        # Downscale very large captures for streaming/storage efficiency
        if max(pil.size) > SCREENSHOT_MAX_DIMENSION:
            pil.thumbnail((SCREENSHOT_MAX_DIMENSION, SCREENSHOT_MAX_DIMENSION), Image.LANCZOS)

        path = None
        if save:
            os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
            if filename is None:
                filename = f"screenshot_{int(time.time()*1000)}.png"
            path = os.path.join(SCREENSHOTS_DIR, filename)
            pil.save(path, quality=SCREENSHOT_QUALITY)
            self._last_screenshot = path
        return path, pil

    def capture_active_window(self, save: bool = True,
                              filename: Optional[str] = None) -> Tuple[Optional[str], Optional[Image.Image]]:
        """Capture the currently active foreground window."""
        win = self.get_active_window()
        if win is None:
            return self.screenshot(save=save, filename=filename)
        try:
            left, top = win.left, win.top
            right, bottom = win.right, win.bottom
            width = max(right - left, 1)
            height = max(bottom - top, 1)
            img = self._sct.grab({"left": left, "top": top, "width": width, "height": height})
            pil = Image.frombytes("RGB", (img.size.width, img.size.height), img.rgb)
            path = None
            if save:
                os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
                if filename is None:
                    filename = f"window_{int(time.time()*1000)}.png"
                path = os.path.join(SCREENSHOTS_DIR, filename)
                pil.save(path, quality=SCREENSHOT_QUALITY)
            return path, pil
        except Exception:
            return self.screenshot(save=save, filename=filename)

    def record_screen(self, duration_seconds: int, fps: int = 5,
                      monitor_index: int = 0,
                      output_path: Optional[str] = None) -> Optional[str]:
        """Record the screen for a duration and save as an AVI/MP4 via OpenCV."""
        import cv2
        monitors = self.get_monitors()
        if not monitors:
            return None
        mon = monitors[min(max(monitor_index, 0), len(monitors) - 1)]

        if output_path is None:
            os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
            output_path = os.path.join(SCREENSHOTS_DIR,
                                       f"record_{int(time.time())}.avi")

        fourcc = cv2.VideoWriter_fourcc(*"XVID")
        out = cv2.VideoWriter(output_path, fourcc, fps, (mon.width, mon.height))

        start = time.time()
        try:
            while time.time() - start < duration_seconds:
                img = self._sct.grab({
                    "left": mon.left, "top": mon.top,
                    "width": mon.width, "height": mon.height,
                })
                frame = np_from_mss(img)
                out.write(frame)
        finally:
            out.release()
        return output_path if os.path.exists(output_path) else None

    # ------------------------------------------------------------------ OCR
    def ocr(self, image_path: Optional[str] = None,
            image: Optional[Image.Image] = None) -> str:
        """Extract text from an image. Returns empty string if Tesseract missing."""
        if not _TESSERACT_AVAILABLE:
            return ""
        if image_path is not None:
            return pytesseract.image_to_string(Image.open(image_path))
        if image is not None:
            return pytesseract.image_to_string(image)
        return ""

    def ocr_screen(self, monitor_index: int = 0) -> str:
        _, pil = self.screenshot(monitor_index=monitor_index, save=False)
        if pil is None:
            return ""
        return self.ocr(image=pil)

    # ------------------------------------------------------------------ clipboard
    def clipboard_read(self) -> str:
        return pyautogui.pyperclip_paste() if hasattr(pyautogui, "pyperclip_paste") else _clipboard_read()

    def clipboard_write(self, text: str) -> None:
        pyautogui.key_down("ctrl")  # ensure clean
        pyautogui.key_up("ctrl")
        import pyperclip
        pyperclip.copy(text)

    # ------------------------------------------------------------------ windows
    def get_active_window(self):
        if gw is None:
            return None
        try:
            return gw.getActiveWindow()
        except Exception:
            return None

    def get_all_windows(self):
        if gw is None:
            return []
        try:
            return gw.getAllWindows()
        except Exception:
            return []

    def switch_to_window(self, title_contains: str) -> bool:
        if gw is None:
            return False
        for w in gw.getAllWindows():
            if title_contains.lower() in (w.title or "").lower():
                try:
                    w.activate()
                    return True
                except Exception:
                    continue
        return False

    def close_window(self, title_contains: str) -> bool:
        if gw is None:
            return False
        for w in gw.getAllWindows():
            if title_contains.lower() in (w.title or "").lower():
                try:
                    w.close()
                    return True
                except Exception:
                    continue
        return False


def np_from_mss(img):
    """Convert an MSS Image to a numpy BGR array (for OpenCV)."""
    import numpy as np
    return np.array(img)


def _clipboard_read() -> str:
    import pyperclip
    try:
        return pyperclip.paste()
    except Exception:
        return ""
