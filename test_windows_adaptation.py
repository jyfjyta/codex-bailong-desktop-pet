"""Cross-platform unit tests for Windows 10/11 adaptation (WinInputHook, PrivacyGuard, Antigravity Windows)."""
from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'app'))

from PySide6.QtWidgets import QApplication

APP = QApplication.instance() or QApplication([])

import antigravity_adapter as ag
import privacy_guard
import win_input_hook as wih
from typing_companion.layout import PerspectiveDeck


class WinInputHookTests(unittest.TestCase):
    def test_vk_normalization_and_deck_mapping(self):
        deck = PerspectiveDeck()
        self.assertEqual(wih.normalize_win_vk(0x0D, 0), 0x0D)
        self.assertEqual(wih.normalize_win_vk(0x0D, wih.LLKHF_EXTENDED), wih.VK_NUMPAD_ENTER)
        for vk in (0x41, 0x5A, 0x30, 0x20, 0x0D, 0x10D, 0x08, 0xA0, 0xA2, 0x5B, 0xBC, 0xBE):
            self.assertIsNotNone(deck.resolve_vk(vk), f'VK {hex(vk)} should map onto 65% keyboard')

    def test_keyboard_repeat_dedup_and_privacy_masking(self):
        ev_q: queue.SimpleQueue = queue.SimpleQueue()
        privacy = threading.Event()
        wakes = []
        hook = wih.WinInputHook(ev_q, privacy, notify_ui=lambda: wakes.append(1))

        hook._handle_key_event(wih.WM_KEYDOWN, 0x41, 0)
        hook._handle_key_event(wih.WM_KEYDOWN, 0x41, 0)  # repeat
        hook._handle_key_event(wih.WM_KEYUP, 0x41, 0)

        items = []
        while not ev_q.empty():
            items.append(ev_q.get_nowait())
        self.assertEqual(items, [
            ('key_down', 0x41, False),
            ('key_down', 0x41, True),
            ('key_up', 0x41),
        ])
        self.assertEqual(len(wakes), 3)

        # Privacy active -> must NEVER emit raw VK, only generic ('privacy_key',)
        privacy.set()
        hook._handle_key_event(wih.WM_KEYDOWN, 0x50, 0)
        hook._handle_key_event(wih.WM_KEYDOWN, 0x50, 0)  # repeat suppressed in privacy
        hook._handle_key_event(wih.WM_KEYUP, 0x50, 0)
        priv_items = []
        while not ev_q.empty():
            priv_items.append(ev_q.get_nowait())
        self.assertEqual(priv_items, [('privacy_key',)])

    def test_mouse_buttons_and_wheel_decoding(self):
        self.assertEqual(wih.decode_wheel_delta(120 << 16), 1)
        self.assertEqual(wih.decode_wheel_delta(((-120) & 0xFFFF) << 16), -1)
        self.assertEqual(wih.decode_wheel_delta(0), 0)

        ev_q: queue.SimpleQueue = queue.SimpleQueue()
        hook = wih.WinInputHook(ev_q, threading.Event())
        hook._handle_mouse_event(wih.WM_LBUTTONDOWN, 100, 200, 0)
        hook._handle_mouse_event(wih.WM_LBUTTONUP, 100, 200, 0)
        hook._handle_mouse_event(wih.WM_RBUTTONDOWN, 100, 200, 0)
        hook._handle_mouse_event(wih.WM_RBUTTONUP, 100, 200, 0)
        hook._handle_mouse_event(wih.WM_MBUTTONDOWN, 100, 200, 0)
        hook._handle_mouse_event(wih.WM_MBUTTONUP, 100, 200, 0)
        hook._handle_mouse_event(wih.WM_MOUSEWHEEL, 100, 200, 120 << 16)
        hook._handle_mouse_event(wih.WM_MOUSEWHEEL, 100, 200, ((-120) & 0xFFFF) << 16)

        items = []
        while not ev_q.empty():
            items.append(ev_q.get_nowait())
        self.assertEqual(items, [
            ('mouse_down', 'left'),
            ('mouse_up', 'left'),
            ('mouse_down', 'right'),
            ('mouse_up', 'right'),
            ('mouse_down', 'middle'),
            ('mouse_up', 'middle'),
            ('mouse_wheel', 1),
            ('mouse_wheel', -1),
        ])


class WindowsAntigravityAdapterTests(unittest.TestCase):
    def test_windows_cli_and_app_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / 'AppData' / 'Local'
            agy_exe = local / 'agy' / 'bin' / 'agy.exe'
            agy_exe.parent.mkdir(parents=True, exist_ok=True)
            agy_exe.write_bytes(b'MZ')
            app_exe = local / 'Programs' / 'Antigravity' / 'Antigravity.exe'
            app_exe.parent.mkdir(parents=True, exist_ok=True)
            app_exe.write_bytes(b'MZ')

            with patch.object(ag.sys, 'platform', 'win32'), \
                 patch.dict(os.environ, {'LOCALAPPDATA': str(local), 'BAILONG_AGY': ''}, clear=False), \
                 patch('antigravity_adapter.shutil.which', return_value=None):
                self.assertEqual(ag.find_agy_cli(), agy_exe.resolve())
                self.assertIn(app_exe, ag.antigravity_app_paths())

    def test_windows_powershell_hooks_merge_and_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            cfg = tmp_dir / 'hooks.json'
            user_hook = {'type': 'command', 'command': 'echo keep-me'}
            cfg.write_text(json.dumps({'hooks': {'Stop': [user_hook]}}), encoding='utf-8')

            with patch.object(ag.sys, 'platform', 'win32'), \
                 patch('antigravity_adapter.data_dir', return_value=tmp_dir):
                self.assertFalse(ag.hooks_installed(cfg))
                ag.configure_hooks(True, cfg)
                self.assertTrue(ag.hooks_installed(cfg))
                ps1 = tmp_dir / 'antigravity-hook-bridge.ps1'
                self.assertTrue(ps1.is_file())
                saved = json.loads(cfg.read_text(encoding='utf-8'))
                self.assertIn(user_hook, saved['hooks']['Stop'])
                our_cmd = saved['hooks']['Stop'][-1]['command']
                self.assertIn('powershell.exe', our_cmd)
                self.assertIn('-Marker ' + ag.HOOK_MARKER, our_cmd)
                self.assertNotIn('#', our_cmd)  # cmd /c must not receive shell # comment

                ag.configure_hooks(False, cfg)
                self.assertFalse(ag.hooks_installed(cfg))
                cleaned = json.loads(cfg.read_text(encoding='utf-8'))
                self.assertEqual(cleaned['hooks']['Stop'], [user_hook])

    def test_windows_statusline_capture_ps1_quotes_paths(self):
        script = ag._win_statusline_capture_ps1(Path(r"C:\Users\O'Brien\status.json"))
        self.assertIn("O''Brien", script)
        self.assertIn('WriteAllText', script)

    def test_windows_build_scripts_include_new_modules_and_assets(self):
        br = (ROOT / 'build_release.py').read_text(encoding='utf-8')
        for token in ('typing_companion', 'win_input_hook', 'privacy_guard', 'antigravity_adapter', 'app/typing_companion/assets'):
            self.assertIn(token, br)
        bl = (ROOT / 'build_local.py').read_text(encoding='utf-8')
        self.assertIn('app/typing_companion', bl)

    def test_privacy_guard_win_probe_instantiates_safely(self):
        probe = privacy_guard._WinPasswordProbe()
        if sys.platform != 'win32':
            self.assertFalse(probe.is_password())


if __name__ == '__main__':
    unittest.main()
