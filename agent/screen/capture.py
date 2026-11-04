"""Screen Capture Engine.

Dedicated capture system independent of task execution. Supports full desktop,
active window, multiple monitors, configurable resolution/FPS, JPEG compression,
and recording to video. Operates independently from the task engine so streaming
never blocks automation.
"""

import os
import time
import threading
from typing import List, Optional, Tuple

import cv2
import mss
import numpy as np
from PIL import Image

from config import (
    SCREEN_CAPTURE_FPS,
    SCREEN_CAPTURE_QUALITY,
    SCREEN_CAPTURE_MONITOR,
    SCREEN_CAPTURE_ENABLED,
    RECORDINGS_DIR,
    SCREENSHOTS_DIR,
    STREAMING_INDICATOR,
)


class ScreenCaptureEngine:
    def __init__(self):
        self._sct = mss.MSS()
        self._lock = threading.Lock()
        self._recording = False
        _record_thread: Optional[threading.Thread] = None
        self._fps = SCREEN_CAPTURE_FPS
        self._quality = SCREEN_CAPTURE_QUALITY
        self._monitor_index = SCREEN_CAPTURE_MONITOR

    # ------------------------------------------------------------------ config
    def set_fps(self, fps: int) -> None:
        self._fps = max(1, min(fps, 30))

    def set_quality(self, quality: int) -> None:
        self._quality = max(10, min(quality, 100))

    def set_monitor(self, index: int) -> None:
        self._monitor_index = index

    def get_monitors(self) -> List[dict]:
        monitors = []
        for i, mon in enumerate(self._sct.monitors):
            if i == 0 and len(self._sct.monitors) > 1:
                continue
            monitors.append({
                "index": i,
                "left": mon["left"],
                "top": mon["top"],
                "width": mon["width"],
                "height": mon["height"],
                "primary": (i == 1 and len(self._sct.monitors) > 1),
            })
        if not monitors:
            mon = self._sct.monitors[0]
            monitors.append({"index": 0, "left": mon["left"], "top": mon["top"],
                             "width": mon["width"], "height": mon["height"], "primary": True})
        return monitors

    def get_resolution(self, monitor_index: Optional[int] = None) -> Tuple[int, int]:
        idx = self._monitor_index if monitor_index is None else monitor_index
        mons = self.get_monitors()
        if not mons:
            return (0, 0)
        m = mons[min(max(idx, 0), len(mons) - 1)]
        return (m["width"], m["height"])

    # ------------------------------------------------------------------ capture
    def capture(self, monitor_index: Optional[int] = None,
                quality: Optional[int] = None) -> Tuple[Optional[str], Optional[bytes]]:
        """Capture a frame.

        Returns (file_path, jpeg_bytes).
        """
        if not SCREEN_CAPTURE_ENABLED:
            return None, None
        idx = self._monitor_index if monitor_index is None else monitor_index
        q = self._quality if quality is None else quality
        mons = self.get_monitors()
        if not mons:
            return None, None
        m = mons[min(max(idx, 0), len(mons) - 1)]

        with self._lock:
            img = self._sct.grab({
                "left": m["left"], "top": m["top"],
                "width": m["width"], "height": m["height"],
            })
        frame = np.array(img)  # BGRA
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

        path = None
        jpeg = None
        ts = int(time.time() * 1000)
        path = os.path.join(SCREENSHOTS_DIR, f"cap_{ts}.jpg")
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        pil.save(path, "JPEG", quality=q)

        # Also produce in-memory JPEG for streaming
        buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, q])[1]
        jpeg = buf.tobytes()
        return path, jpeg

    def capture_active_window(self, quality: Optional[int] = None) -> Tuple[Optional[str], Optional[bytes]]:
        from control.desktop import DesktopControl
        desktop = DesktopControl()
        win = desktop.get_active_window()
        q = self._quality if quality is None else quality
        if win is None:
            return self.capture(quality=q)
        try:
            left, top = win.left, win.top
            width = max(win.right - left, 1)
            height = max(win.bottom - top, 1)
            with self._lock:
                img = self._sct.grab({"left": left, "top": top, "width": width, "height": height})
            frame = cv2.cvtColor(np.array(img), cv2.COLOR_BGRA2BGR)
            ts = int(time.time() * 1000)
            path = os.path.join(SCREENSHOTS_DIR, f"win_{ts}.jpg")
            os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
            cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, q])
            buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, q])[1]
            return path, buf.tobytes()
        except Exception:
            return self.capture(quality=q)

    # ------------------------------------------------------------------ recording
    def start_recording(self, duration_seconds: int = 60, fps: Optional[int] = None,
                        monitor_index: Optional[int] = None,
                        output_path: Optional[str] = None) -> Optional[str]:
        """Record screen for a duration (blocking). Returns output path."""
        fps = fps or self._fps
        idx = self._monitor_index if monitor_index is None else monitor_index
        mons = self.get_monitors()
        if not mons:
            return None
        m = mons[min(max(idx, 0), len(mons) - 1)]
        if output_path is None:
            os.makedirs(RECORDINGS_DIR, exist_ok=True)
            output_path = os.path.join(RECORDINGS_DIR,
                                       f"record_{int(time.time())}.avi")

        fourcc = cv2.VideoWriter_fourcc(*"XVID")
        out = cv2.VideoWriter(output_path, fourcc, fps, (m["width"], m["height"]))
        self._recording = True
        start = time.time()
        try:
            while self._recording and (time.time() - start) < duration_seconds:
                with self._lock:
                    img = self._sct.grab({
                        "left": m["left"], "top": m["top"],
                        "width": m["width"], "height": m["height"],
                    })
                frame = cv2.cvtColor(np.array(img), cv2.COLOR_BGRA2BGR)
                out.write(frame)
                time.sleep(max(0, 1.0 / fps - 0.005))
        finally:
            out.release()
            self._recording = False
        return output_path if os.path.exists(output_path) else None

    def stop_recording(self) -> None:
        self._recording = False

    def is_recording(self) -> bool:
        return self._recording


capture_engine = ScreenCaptureEngine()
