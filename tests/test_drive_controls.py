"""Drive controls window (owner, November 25): the round drag stick and W A S D keys, and what the window tells
the battery menu. Drawn off screen with Qt's offscreen platform; synthetic controller lines; nothing is sent
to any controller (the window's output is captured). They prove what is said for each press and release, not
how the wheels respond."""
import io
import json
import os
import queue
import time
import unittest
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QKeyEvent, QMouseEvent
    from PySide6.QtWidgets import QApplication
except ImportError:          # pragma: no cover - the GUI extra is not installed
    QApplication = None


def telemetry():
    return json.dumps({'type': 'telemetry', 'odom': {'lin': 0., 'ang': 0.}}).encode()


@unittest.skipIf(QApplication is None, 'PySide6 is not installed')
class DriveControlsTests(unittest.TestCase):
    def setUp(self):
        from r3x.gui.drive_controls import DriveControlsWindow
        self.app = QApplication.instance() or QApplication([])
        self.lines, self.out = queue.Queue(), io.BytesIO()
        self.window = DriveControlsWindow(self.lines, quit_on_end=False, out=self.out)
        self.addCleanup(self.window.deleteLater)
        self.window.show()
        self.app.processEvents()
        self.live()

    def live(self):
        self.lines.put((time.monotonic(), telemetry()))
        self.window._tick()

    def said(self):
        rows = [json.loads(row) for row in self.out.getvalue().splitlines() if row]
        self.out.seek(0); self.out.truncate()
        return [(r['x'], r['y'], r['engaged']) for r in rows]

    def key(self, key, down=True, repeat=False):
        kind = QEvent.Type.KeyPress if down else QEvent.Type.KeyRelease
        event = QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier, '', repeat)
        (self.window.keyPressEvent if down else self.window.keyReleaseEvent)(event)

    def mouse(self, kind, dx, dy):
        """A mouse event on the stick at (dx right, dy forward) in circle radii from the middle."""
        stick = self.window.stick
        cx, cy, radius, _ = stick._circle()
        at = QPointF(cx + dx * radius, cy - dy * radius)
        kinds = {'press': QEvent.Type.MouseButtonPress, 'move': QEvent.Type.MouseMove,
                 'release': QEvent.Type.MouseButtonRelease}
        buttons = Qt.MouseButton.NoButton if kind == 'release' else Qt.MouseButton.LeftButton
        event = QMouseEvent(kinds[kind], at, stick.mapToGlobal(at), Qt.MouseButton.LeftButton, buttons,
                            Qt.KeyboardModifier.NoModifier)
        {'press': stick.mousePressEvent, 'move': stick.mouseMoveEvent, 'release': stick.mouseReleaseEvent}[kind](event)

    def test_w_drives_forward_and_letting_go_stops(self):
        self.said()
        self.key(Qt.Key.Key_W)
        self.assertEqual(self.said(), [(0., .6, True)])                 # The default speed, 60%.
        self.key(Qt.Key.Key_W, repeat=True)                              # The keyboard's own repeat adds nothing.
        self.assertEqual(self.said(), [])
        self.key(Qt.Key.Key_W, down=False)
        self.assertEqual(self.said(), [(0., 0., False)])

    def test_two_keys_curve_and_the_arrows_work_the_same(self):
        self.said()
        self.key(Qt.Key.Key_W); self.key(Qt.Key.Key_A)
        self.assertEqual(self.said()[-1], (-.6, .6, True))
        self.key(Qt.Key.Key_W, down=False); self.key(Qt.Key.Key_A, down=False)
        self.key(Qt.Key.Key_Down)
        self.assertEqual(self.said()[-1], (0., -.6, True))

    def test_a_held_direction_is_repeated_so_the_menu_keeps_driving(self):
        self.said()
        self.key(Qt.Key.Key_D)
        self.window._sent_at -= .2                                        # A moment later...
        self.live()
        self.assertEqual(self.said(), [(.6, 0., True), (.6, 0., True)])
        self.key(Qt.Key.Key_D, down=False)
        self.said()
        self.window._sent_at -= .2
        self.live()
        self.assertEqual(self.said(), [])                                 # Nothing held: nothing repeated.

    def test_the_stick_drives_at_any_angle_in_proportion_and_springs_back(self):
        self.said()
        self.mouse('press', .5, .5)                                       # Half way out, up and to the right.
        x, y, engaged = self.said()[-1]
        self.assertTrue(engaged)
        self.assertAlmostEqual(x, .3, delta=.01); self.assertAlmostEqual(y, .3, delta=.01)   # 60% speed.
        self.mouse('move', -2., 0.)                                       # Dragged past the rim to the left.
        x, y, _ = self.said()[-1]
        self.assertAlmostEqual(x, -.6, delta=.01); self.assertAlmostEqual(y, 0., delta=.01)
        self.mouse('move', -.6, -.8)                                      # Back and left, on the rim.
        x, y, _ = self.said()[-1]
        self.assertAlmostEqual(x, -.36, delta=.01); self.assertAlmostEqual(y, -.48, delta=.01)
        self.window._sent_at -= .2
        self.live()
        self.assertEqual(len(self.said()), 1)                             # Held still: still said every 0.1 s.
        self.mouse('release', -.6, -.8)
        self.assertEqual(self.said(), [(0., 0., False)])
        self.assertEqual(self.window.stick.vector, (0., 0.))

    def test_a_press_in_the_middle_or_outside_the_circle_does_not_move_him(self):
        self.said()
        self.mouse('press', .03, .03)
        self.assertEqual(self.said(), [(0., 0., False)])
        self.mouse('release', .03, .03)
        self.said()
        self.mouse('press', 1.6, 1.6)                                     # A corner of the panel, off the stick.
        self.assertEqual(self.said(), [])
        self.assertFalse(self.window.stick.grabbed)

    def test_space_or_losing_focus_stops_the_stick_and_the_keys(self):
        self.said()
        self.mouse('press', 0., .9)
        self.assertEqual(self.said()[-1][2], True)
        self.key(Qt.Key.Key_Space)
        self.assertEqual(self.said(), [(0., 0., False)])
        self.assertFalse(self.window.stick.grabbed)
        self.key(Qt.Key.Key_S)
        with mock.patch.object(self.window, 'isActiveWindow', return_value=False):   # Another window has the keys.
            self.window.changeEvent(QEvent(QEvent.Type.ActivationChange))
        self.assertEqual(self.said()[-1], (0., 0., False))

    def test_the_speed_slider_scales_and_the_legend_lights_the_held_key(self):
        self.window._speed.setValue(100)
        self.said()
        self.key(Qt.Key.Key_W)
        self.assertEqual(self.said(), [(0., 1., True)])
        self.window._tick()
        self.assertTrue(self.window._caps['forward'].property('held'))
        self.assertFalse(self.window._caps['back'].property('held'))

    def test_without_a_live_controller_nothing_drives(self):
        self.window.model.telemetry_at = time.monotonic() - 10            # The feed went quiet (Rex has the port).
        self.said()
        self.key(Qt.Key.Key_W)
        self.window._tick()
        self.mouse('press', 0., .9)
        self.assertTrue(all(not engaged for _, _, engaged in self.said()))
        self.assertFalse(self.window.stick.grabbed)
        self.assertFalse(self.window.stick.isEnabled())


if __name__ == '__main__':
    unittest.main()
