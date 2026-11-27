"""Unit & visual integration tests for Bailong Typing Companion v7 (dynamic KuroCat-style arms + compact BongoCat tilt)."""
from __future__ import annotations

import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'app'))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu

from desktop_core import DEFAULTS, normalized
from mac_input_hook import MACOS_KEYCODE_TO_VK, _KeyDeduplicator, hid_usage_to_vk
from typing_companion.layout import COMPANION_H, COMPANION_W, PerspectiveDeck
from typing_companion.renderer import TypingCompanionRenderer


class TypingCompanionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_defaults_and_normalization(self) -> None:
        self.assertIs(DEFAULTS['typing_companion'], False)
        self.assertIs(DEFAULTS['tc_key_bubbles'], True)
        self.assertIs(DEFAULTS['tc_privacy_guard'], True)
        self.assertIs(DEFAULTS['tc_codex_autotype'], True)
        cfg = normalized({'typing_companion': True, 'tc_key_bubbles': 'bad'})
        self.assertIs(cfg['typing_companion'], True)
        self.assertIs(cfg['tc_key_bubbles'], True)

    def test_compact_tilted_180_keyboard_and_keycodes(self) -> None:
        self.assertEqual((COMPANION_W, COMPANION_H), (280, 236))
        deck = PerspectiveDeck()
        self.assertGreaterEqual(len(deck.keys), 60)

        self.assertLess(deck.keys['space'].local_center.y(), deck.keys['1'].local_center.y())
        self.assertGreater(deck.keys['esc'].local_center.x(), deck.KB_LOCAL_CX + 45.0)
        self.assertGreater(deck.keys['esc'].local_center.y(), deck.keys['space'].local_center.y() + 25.0)
        self.assertLess(deck.keys['j'].local_center.x(), deck.KB_LOCAL_CX)
        self.assertGreater(deck.keys['f'].local_center.x(), deck.KB_LOCAL_CX)

        for kc in (0x00, 0x0E, 0x31, 0x24, 0x33, 0x35, 0x38, 0x37, 0x7B, 0x7E):
            vk = MACOS_KEYCODE_TO_VK.get(kc)
            self.assertIsNotNone(vk)
            self.assertIsNotNone(deck.resolve_vk(vk))

        for usage in (0x04, 0x08, 0x28, 0x2A, 0x2C, 0xE1, 0xE3, 0x50, 0x52):
            vk = hid_usage_to_vk(usage)
            self.assertIsNotNone(vk)
            self.assertIsNotNone(deck.resolve_vk(vk))

        dedup = _KeyDeduplicator()
        self.assertIs(dedup.on_down(0x45, src=0, repeat=False), False)
        self.assertIsNone(dedup.on_down(0x45, src=1, repeat=False))
        self.assertTrue(dedup.on_up(0x45))
        self.assertFalse(dedup.on_up(0x45))

    def test_v7_live_per_key_paw_tracking_mouse_glide_and_expressions(self) -> None:
        r = TypingCompanionRenderer()
        sprites = r.ensure_sprites()
        for key in TypingCompanionRenderer.SPRITE_NAMES:
            self.assertFalse(sprites[key].isNull(), f'missing v7 sprite {key}')

        # 1) Mouse gliding visibly moves left_paw across mouse pad AND global eyes follow cursor!
        r.reset_transient(1.0)
        r.on_mouse_move(0.10, 0.20, 1.0)
        r.on_cursor_vector(-300.0, -150.0, 1.0)
        for i in range(25):
            r.step(1.0 + (i + 1) * 0.016, 0.016)
        lx_left = r.left_paw.x
        r.on_mouse_move(0.95, 0.85, 1.6)
        for i in range(25):
            r.step(1.6 + (i + 1) * 0.016, 0.016)
        self.assertGreater(r.left_paw.x - lx_left, 10.0)

        # 2) Pressing keys moves paws directly onto the REAL key regions (Enter & Esc) while face stays calm!
        t2 = 2.5
        r.on_key_down(0x0D, t2)         # Enter (left-middle keyboard zone)
        r.on_key_down(0x1B, t2 + 0.01)  # Esc (bottom-right keyboard corner)
        for i in range(18):
            r.step(t2 + 0.01 + (i + 1) * 0.016, 0.016)
        self.assertEqual(r.choose_expression(t2 + 0.30), 'idle')
        self.assertEqual(r.left_paw.mode, 'poke')
        self.assertEqual(r.right_paw.mode, 'poke')
        enter_c = r.deck.keys['enter'].center
        esc_c = r.deck.keys['esc'].center
        self.assertLess(math.hypot(r.left_paw.x - enter_c.x(), r.left_paw.y - enter_c.y()), 6.0)
        self.assertLess(math.hypot(r.right_paw.x - esc_c.x(), r.right_paw.y - esc_c.y()), 8.0)

        # 3) Full KuroCat interactive expression suite: headpat -> happy (B), click -> angry (💢),
        #    Codex complete -> starry, Backspace spam -> flustered, idle 40s -> sleepy,
        #    typing >= 20 keys + pause stays calm idle (NO auto smug!), privacy -> shy!
        r.reset_transient(4.0)
        r.on_head_stroke(30.0, 0.0, 4.02)
        self.assertEqual(r.choose_expression(4.05), 'happy')

        r.reset_transient(5.0)
        self.assertEqual(r.on_pet_click(5.02), 'angry')
        self.assertEqual(r.choose_expression(5.10), 'angry')

        r.reset_transient(7.0)
        r.trigger_celebrate(7.02, 2.5)
        self.assertEqual(r.choose_expression(7.20), 'starry')

        r.reset_transient(8.0)
        for i in range(7):
            r.on_key_down(0x08, 8.0 + i * 0.12)
        self.assertEqual(r.choose_expression(8.90), 'flustered')

        r.reset_transient(10.0)
        r.next_blink_at = 999.0
        for i in range(22):
            t_k = 10.0 + i * 0.05
            r.on_key_down(0x44, t_k)
            r.on_key_up(0x44, t_k + 0.02)
        r.step(12.2, 0.016)
        self.assertEqual(r.choose_expression(12.2), 'idle')

        r.reset_transient(14.0)
        r.next_blink_at = 999.0
        r.step(55.0, 0.016)
        self.assertEqual(r.choose_expression(55.0), 'sleepy')

        # 4) Privacy guard clears keys/bubbles and switches to shy
        r.set_privacy_mode(True, 60.0)
        self.assertEqual(r.choose_expression(60.1), 'shy')
        self.assertEqual(len(r.pressed_keys), 0)
        self.assertEqual(len(r.bubbles), 0)

        preview_out = os.environ.get('TC_PREVIEW_OUT')
        if preview_out:
            self._save_showcase(Path(preview_out))

    def _save_showcase(self, path: Path) -> None:
        scale = 2
        pw, ph = COMPANION_W * scale, COMPANION_H * scale
        sheet = QImage(pw * 4, ph * 2, QImage.Format.Format_RGBA8888)
        sheet.fill(QColor('#f3effa'))
        p = QPainter(sheet)

        def _render_cell(col: int, row: int, setup_fn, t_end: float) -> None:
            r = TypingCompanionRenderer()
            r.reset_transient(1.0)
            r.next_blink_at = 999.0
            setup_fn(r)
            r.paint(p, QRectF(pw * col, ph * row, pw, ph), 1.0, t_end)

        # Row 0: (0) Happy B (headpat) | (1) Wink D (click) | (2) Angry (4x poke) | (3) Sleepy (idle Zzz)
        def _happy(r: TypingCompanionRenderer) -> None:
            r.on_head_stroke(36.0, 0.0, 1.02)
            r.trigger_happy(1.04, 2.5)
            for i in range(14):
                r.step(1.04 + (i + 1) * 0.016, 0.016)

        def _wink(r: TypingCompanionRenderer) -> None:
            r.on_pet_click(1.04)
            for i in range(14):
                r.step(1.04 + (i + 1) * 0.016, 0.016)

        def _angry(r: TypingCompanionRenderer) -> None:
            r.trigger_angry(1.04, 2.8)
            r.on_key_down(0x0D, 1.06)
            for i in range(14):
                r.step(1.06 + (i + 1) * 0.016, 0.016)

        def _sleepy(r: TypingCompanionRenderer) -> None:
            r.step(46.0, 0.016)
            for i in range(12):
                r.step(46.0 + (i + 1) * 0.016, 0.016)

        # Row 1: (0) Starry (Codex complete) | (1) Smug (typing burst done) | (2) Flustered (Backspace spam) | (3) Shy (password guard)
        def _starry(r: TypingCompanionRenderer) -> None:
            r.trigger_celebrate(1.04, 2.6)
            for i in range(14):
                r.step(1.04 + (i + 1) * 0.016, 0.016)

        def _smug(r: TypingCompanionRenderer) -> None:
            r.trigger_smug(1.04, 2.4)
            r.on_key_down(0x44, 1.06)
            for i in range(14):
                r.step(1.06 + (i + 1) * 0.016, 0.016)

        def _flustered(r: TypingCompanionRenderer) -> None:
            r.trigger_flustered(1.04, 2.4)
            r.on_key_down(0x08, 1.06)
            for i in range(14):
                r.step(1.06 + (i + 1) * 0.016, 0.016)

        def _shy(r: TypingCompanionRenderer) -> None:
            r.set_privacy_mode(True, 1.04)
            r.on_privacy_pulse(1.06)
            for i in range(12):
                r.step(1.06 + (i + 1) * 0.016, 0.016)

        _render_cell(0, 0, _happy, 1.30)
        _render_cell(1, 0, _wink, 1.30)
        _render_cell(2, 0, _angry, 1.30)
        _render_cell(3, 0, _sleepy, 46.25)
        _render_cell(0, 1, _starry, 1.30)
        _render_cell(1, 1, _smug, 1.30)
        _render_cell(2, 1, _flustered, 1.30)
        _render_cell(3, 1, _shy, 1.28)

        p.end()
        path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(str(path))

    def test_desktop_pet_menu_and_dock_position_preservation(self) -> None:
        from desktop import DesktopPet

        with tempfile.TemporaryDirectory() as folder:
            tmp = Path(folder)
            patches = [
                patch('enhanced.CONFIG', tmp / 'settings.json'),
                patch('enhanced.STATE_DIR', tmp),
                patch('desktop.CONFIG', tmp / 'settings.json'),
                patch('desktop.STATE_DIR', tmp),
                patch('desktop_settings.autostart_enabled', return_value=False),
            ]
            for pt in patches:
                pt.start()
            try:
                pet = DesktopPet(monitor=False)
                pet.show()

                root_menu = QMenu()
                pet.build_menu(root_menu, tray=False)
                names = [a.objectName() for a in root_menu.actions() if a.objectName()]
                self.assertIn('command_typing_companion', names)
                self.assertEqual(names.index('command_typing_companion'), names.index('command_walk') + 1)

                pet.apply_settings({'scale': 0.8})
                screen = QApplication.primaryScreen()
                phys = screen.geometry()
                parked_x = phys.right() - pet.width() - 11
                parked_y = phys.bottom() - pet.height() + 16
                pet.move(parked_x, parked_y)

                pet.apply_settings({'typing_companion': True})
                self.assertTrue(pet.is_typing_companion())
                self.assertEqual((pet.width(), pet.height()), (round(COMPANION_W * 0.8), round(COMPANION_H * 0.8)))
                self.assertLessEqual(pet.geometry().right(), phys.right())
                self.assertEqual(pet.geometry().bottom() + 1, parked_y + round(256 * 0.8))

                pet.apply_settings({'typing_companion': False})
                self.assertFalse(pet.is_typing_companion())
                self.assertEqual((pet.x(), pet.y()), (parked_x, parked_y))

                pet.close()
            finally:
                for pt in reversed(patches):
                    pt.stop()


if __name__ == '__main__':
    unittest.main()
