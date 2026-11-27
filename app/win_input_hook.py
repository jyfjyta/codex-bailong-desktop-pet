"""Passive Windows global keyboard & mouse listener via ctypes SetWindowsHookExW (zero third-party deps)."""
from __future__ import annotations

import ctypes
import queue
import sys
import threading
import time
from typing import Callable

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14

WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A

LLKHF_EXTENDED = 0x01
VK_RETURN = 0x0D
VK_NUMPAD_ENTER = 0x10D


def normalize_win_vk(vk: int, flags: int = 0) -> int:
    """Map Windows virtual-key + low-level flags to unified TypingCompanion VK codes."""
    vk = int(vk) & 0xFFFF
    if vk == VK_RETURN and (int(flags) & LLKHF_EXTENDED):
        return VK_NUMPAD_ENTER
    return vk


def decode_wheel_delta(mouse_data: int) -> int:
    """Extract signed wheel notch direction from MSLLHOOKSTRUCT.mouseData high word."""
    high = (int(mouse_data) >> 16) & 0xFFFF
    signed = high - 0x10000 if high >= 0x8000 else high
    if signed > 0:
        return 1
    if signed < 0:
        return -1
    return 0


class _POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ('vkCode', ctypes.c_uint32),
        ('scanCode', ctypes.c_uint32),
        ('flags', ctypes.c_uint32),
        ('time', ctypes.c_uint32),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ('pt', _POINT),
        ('mouseData', ctypes.c_uint32),
        ('flags', ctypes.c_uint32),
        ('time', ctypes.c_uint32),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


class _MSG(ctypes.Structure):
    _fields_ = [
        ('hwnd', ctypes.c_void_p),
        ('message', ctypes.c_uint32),
        ('wParam', ctypes.c_size_t),
        ('lParam', ctypes.c_ssize_t),
        ('time', ctypes.c_uint32),
        ('pt', _POINT),
    ]


class WinInputHook:
    """Passive Win32 WH_KEYBOARD_LL + WH_MOUSE_LL listener matching MacInputHook's event contract."""

    def __init__(
        self,
        events: queue.SimpleQueue,
        privacy_flag: threading.Event,
        notify_ui: Callable[[], None] | None = None,
    ) -> None:
        self.events = events
        self.privacy_flag = privacy_flag
        self.notify_ui = notify_ui
        self.keyboard_active = False
        self.mouse_active = False
        self.key_events_received = 0

        self._running = False
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._thread_id: int = 0
        self._pressed: set[int] = set()
        self._last_move_emit = 0.0
        self._kb_cb_ref = None
        self._ms_cb_ref = None

    def _emit(self, item: tuple) -> None:
        self.events.put(item)
        if self.notify_ui is not None:
            try:
                self.notify_ui()
            except RuntimeError:
                pass

    def _handle_key_event(self, msg: int, vk_code: int, flags: int) -> None:
        vk = normalize_win_vk(vk_code, flags)
        if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
            self.key_events_received += 1
            self.keyboard_active = True
            repeat = vk in self._pressed
            if self.privacy_flag.is_set():
                if not repeat:
                    self._pressed.add(vk)
                    self._emit(('privacy_key',))
                return
            self._pressed.add(vk)
            self._emit(('key_down', vk, repeat))
        elif msg in (WM_KEYUP, WM_SYSKEYUP):
            was_down = vk in self._pressed
            self._pressed.discard(vk)
            if self.privacy_flag.is_set():
                return
            if was_down:
                self._emit(('key_up', vk))

    def _handle_mouse_event(self, msg: int, x: int, y: int, mouse_data: int) -> None:
        if msg == WM_MOUSEMOVE:
            now = time.perf_counter()
            if now - self._last_move_emit >= 0.014:
                self._last_move_emit = now
                self._emit(('mouse_move', float(x), float(y)))
        elif msg == WM_LBUTTONDOWN:
            self._emit(('mouse_down', 'left'))
        elif msg == WM_LBUTTONUP:
            self._emit(('mouse_up', 'left'))
        elif msg == WM_RBUTTONDOWN:
            self._emit(('mouse_down', 'right'))
        elif msg == WM_RBUTTONUP:
            self._emit(('mouse_up', 'right'))
        elif msg == WM_MBUTTONDOWN:
            self._emit(('mouse_down', 'middle'))
        elif msg == WM_MBUTTONUP:
            self._emit(('mouse_up', 'middle'))
        elif msg == WM_MOUSEWHEEL:
            delta = decode_wheel_delta(mouse_data)
            if delta != 0:
                self._emit(('mouse_wheel', delta))

    def start(self) -> bool:
        if sys.platform != 'win32':
            return False
        with self._lock:
            if self._running and self._thread is not None and self._thread.is_alive():
                return True
            self._running = True
            self._pressed.clear()
            ready = threading.Event()
            self._thread = threading.Thread(
                target=self._hook_loop, args=(ready,), name='BailongWinInputHook', daemon=True
            )
            self._thread.start()
        ready.wait(timeout=1.0)
        return self.keyboard_active or self.mouse_active

    def trigger_reconnect(self) -> None:
        if sys.platform != 'win32':
            return
        self.stop()
        self.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            tid = self._thread_id
            thread = self._thread
            self._thread_id = 0
            self._thread = None
        if sys.platform == 'win32' and tid:
            try:
                user32 = ctypes.WinDLL('user32', use_last_error=True)
                user32.PostThreadMessageW.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_ssize_t]
                user32.PostThreadMessageW.restype = ctypes.c_int
                user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
            except OSError:
                pass
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self.keyboard_active = False
        self.mouse_active = False
        self._pressed.clear()

    def _hook_loop(self, ready: threading.Event) -> None:
        if sys.platform != 'win32':
            ready.set()
            return
        try:
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

            LRESULT = ctypes.c_ssize_t
            WPARAM = ctypes.c_size_t
            LPARAM = ctypes.c_ssize_t
            HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)

            kernel32.GetCurrentThreadId.restype = ctypes.c_uint32
            kernel32.GetCurrentThreadId.argtypes = []
            user32.SetWindowsHookExW.restype = ctypes.c_void_p
            user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, ctypes.c_uint32]
            user32.CallNextHookEx.restype = LRESULT
            user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, WPARAM, LPARAM]
            user32.UnhookWindowsHookEx.restype = ctypes.c_int
            user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
            user32.GetMessageW.restype = ctypes.c_int
            user32.GetMessageW.argtypes = [ctypes.POINTER(_MSG), ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
            user32.PeekMessageW.restype = ctypes.c_int
            user32.PeekMessageW.argtypes = [ctypes.POINTER(_MSG), ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]

            def _on_kb(n_code: int, w_param: int, l_param: int) -> int:
                if n_code >= 0 and self._running and l_param:
                    try:
                        info = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                        self._handle_key_event(int(w_param), int(info.vkCode), int(info.flags))
                    except Exception:
                        pass
                return int(user32.CallNextHookEx(None, n_code, w_param, l_param))

            def _on_mouse(n_code: int, w_param: int, l_param: int) -> int:
                if n_code >= 0 and self._running and l_param:
                    try:
                        info = ctypes.cast(l_param, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                        self._handle_mouse_event(int(w_param), int(info.pt.x), int(info.pt.y), int(info.mouseData))
                    except Exception:
                        pass
                return int(user32.CallNextHookEx(None, n_code, w_param, l_param))

            kb_cb = HOOKPROC(_on_kb)
            ms_cb = HOOKPROC(_on_mouse)
            self._kb_cb_ref = kb_cb
            self._ms_cb_ref = ms_cb

            # Ensure the thread has a message queue before storing thread_id for PostThreadMessageW.
            msg = _MSG()
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            with self._lock:
                self._thread_id = int(kernel32.GetCurrentThreadId())

            kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, kb_cb, None, 0)
            ms_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, ms_cb, None, 0)
            self.keyboard_active = bool(kb_hook)
            self.mouse_active = bool(ms_hook)
            ready.set()

            if not kb_hook and not ms_hook:
                return

            try:
                while self._running:
                    ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                    if ret <= 0:
                        break
            finally:
                if kb_hook:
                    user32.UnhookWindowsHookEx(kb_hook)
                if ms_hook:
                    user32.UnhookWindowsHookEx(ms_hook)
        except Exception:
            ready.set()
        finally:
            with self._lock:
                self._thread_id = 0
            self.keyboard_active = False
            self.mouse_active = False
