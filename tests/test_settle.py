"""Headless tests for screen.settle: "has the screen stopped changing?".

Screens are synthetic Pillow images and the clock/sleep are fakes, so these run on any OS.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))

from PIL import Image, ImageDraw  # noqa: E402

from screen.settle import clamp, frame_changed, wait_until_stable  # noqa: E402

W, H = 1920, 1080


def blank():
    return Image.new("RGB", (W, H), "white")


def with_text_lines(n):
    """A chat-like page with n lines of text (an answer that is n lines long so far)."""
    img = blank()
    d = ImageDraw.Draw(img)
    for i in range(n):
        d.text((300, 120 + i * 24), "This is one line of the answer that is being written " * 2,
               fill="black")
    return img


def with_cursor(on):
    img = with_text_lines(5)
    if on:
        ImageDraw.Draw(img).rectangle((820, 300, 822, 318), fill="black")  # text caret
    return img


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run(frames, **kw):
    """Feed scripted frames (last one repeats forever) to wait_until_stable."""
    clock = FakeClock()
    it = iter(frames)
    last = {"f": None}

    def grab():
        try:
            last["f"] = next(it)
        except StopIteration:
            pass
        return last["f"]

    return wait_until_stable(grab, sleep=clock.sleep, clock=clock.now, **kw)


class TestFrameChanged(unittest.TestCase):
    def test_identical_frames_are_not_a_change(self):
        self.assertFalse(frame_changed(with_text_lines(5), with_text_lines(5)))

    def test_a_new_line_of_text_is_a_change(self):
        self.assertTrue(frame_changed(with_text_lines(5), with_text_lines(6)))

    def test_blinking_cursor_is_not_a_change(self):
        self.assertFalse(frame_changed(with_cursor(False), with_cursor(True)))

    def test_whole_page_change_is_a_change(self):
        self.assertTrue(frame_changed(blank(), with_text_lines(10)))


class TestWaitUntilStable(unittest.TestCase):
    def test_static_screen_is_stable_after_the_quiet_period(self):
        res = run([blank()], timeout=15, quiet_for=2, interval=0.5)
        self.assertTrue(res["stable"])
        self.assertEqual(res["changes"], 0)
        self.assertGreaterEqual(res["waited"], 2.0)

    def test_growing_answer_waits_until_it_stops_growing(self):
        # The answer grows for 10 frames (5 s), then stays the same.
        frames = [with_text_lines(i) for i in range(1, 11)]
        res = run(frames, timeout=30, quiet_for=2, interval=0.5)
        self.assertTrue(res["stable"])
        self.assertGreaterEqual(res["changes"], 8)
        self.assertGreaterEqual(res["waited"], 5.0 + 2.0 - 0.6)

    def test_still_changing_at_timeout_is_reported_not_stable(self):
        frames = [with_text_lines(i % 30 + 1) for i in range(200)]
        res = run(frames, timeout=10, quiet_for=2, interval=0.5)
        self.assertFalse(res["stable"])
        self.assertGreaterEqual(res["waited"], 10.0)

    def test_blinking_cursor_does_not_keep_it_waiting(self):
        frames = [with_cursor(i % 2 == 0) for i in range(100)]
        res = run(frames, timeout=15, quiet_for=2, interval=0.5)
        self.assertTrue(res["stable"])
        self.assertEqual(res["changes"], 0)

    def test_slow_trickle_of_changes_still_adds_up(self):
        # One new line every 4 frames: each step is small, but compared with the last
        # counted change it must still be noticed and keep the wait going.
        frames = []
        for n in range(1, 8):
            frames += [with_text_lines(n)] * 4
        res = run(frames, timeout=30, quiet_for=3, interval=0.5)
        self.assertGreaterEqual(res["changes"], 5)

    def test_should_stop_ends_the_wait(self):
        clock = FakeClock()
        res = wait_until_stable(lambda: with_text_lines(3), timeout=30, quiet_for=20,
                                should_stop=lambda: clock.t >= 1.0,
                                sleep=clock.sleep, clock=clock.now)
        self.assertFalse(res["stable"])
        self.assertTrue(res["stopped"])

    def test_no_screen_does_not_raise(self):
        res = wait_until_stable(lambda: None, sleep=lambda s: None)
        self.assertTrue(res["stable"])
        self.assertIn("note", res)


class TestClamp(unittest.TestCase):
    def test_coerces_llm_values(self):
        self.assertEqual(clamp("10", 0, 60, 3), 10.0)
        self.assertEqual(clamp(None, 0, 60, 3), 3.0)
        self.assertEqual(clamp("soon", 0, 60, 3), 3.0)
        self.assertEqual(clamp(9999, 0, 60, 3), 60.0)
        self.assertEqual(clamp(-5, 0, 60, 3), 0.0)


if __name__ == "__main__":
    unittest.main()
