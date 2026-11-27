"""Self-healing dual-channel macOS global keyboard & mouse listener (CGEventTap + IOHIDManager)."""
from __future__ import annotations

import ctypes
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

BUNDLE_ID = 'io.github.wunsch2048.bailong-enhanced'

K_CG_EVENT_LEFT_MOUSE_DOWN = 1
K_CG_EVENT_LEFT_MOUSE_UP = 2
K_CG_EVENT_RIGHT_MOUSE_DOWN = 3
K_CG_EVENT_RIGHT_MOUSE_UP = 4
K_CG_EVENT_MOUSE_MOVED = 5
K_CG_EVENT_LEFT_MOUSE_DRAGGED = 6
K_CG_EVENT_RIGHT_MOUSE_DRAGGED = 7
K_CG_EVENT_KEY_DOWN = 10
K_CG_EVENT_KEY_UP = 11
K_CG_EVENT_FLAGS_CHANGED = 12
K_CG_EVENT_SCROLL_WHEEL = 22
K_CG_EVENT_OTHER_MOUSE_DOWN = 25
K_CG_EVENT_OTHER_MOUSE_UP = 26
K_CG_EVENT_TAP_DISABLED_BY_TIMEOUT = 0xFFFFFFFE
K_CG_EVENT_TAP_DISABLED_BY_USER_INPUT = 0xFFFFFFFF

K_CG_SESSION_EVENT_TAP = 1
K_CG_HEAD_INSERT_EVENT_TAP = 0
K_CG_EVENT_TAP_OPTION_LISTEN_ONLY = 1

K_CG_KEYBOARD_EVENT_AUTOREPEAT = 8
K_CG_KEYBOARD_EVENT_KEYCODE = 9
K_CG_SCROLL_WHEEL_EVENT_DELTA_AXIS_1 = 11

K_CF_STRING_ENCODING_UTF8 = 0x08000100
K_CF_NUMBER_SINT32_TYPE = 3
K_HID_PAGE_GENERIC_DESKTOP = 0x01
K_HID_USAGE_GD_KEYBOARD = 0x06
K_HID_USAGE_GD_KEYPAD = 0x07
K_HID_PAGE_KEYBOARD_OR_KEYPAD = 0x07

FLAG_CAPS_LOCK = 0x00010000
FLAG_SHIFT = 0x00020000
FLAG_CONTROL = 0x00040000
FLAG_OPTION = 0x00080000
FLAG_COMMAND = 0x00100000

MACOS_KEYCODE_TO_VK: dict[int, int] = {
    0x00: 0x41, 0x0B: 0x42, 0x08: 0x43, 0x02: 0x44, 0x0E: 0x45, 0x03: 0x46,
    0x05: 0x47, 0x04: 0x48, 0x22: 0x49, 0x26: 0x4A, 0x28: 0x4B, 0x25: 0x4C,
    0x2E: 0x4D, 0x2D: 0x4E, 0x1F: 0x4F, 0x23: 0x50, 0x0C: 0x51, 0x0F: 0x52,
    0x01: 0x53, 0x11: 0x54, 0x20: 0x55, 0x09: 0x56, 0x0D: 0x57, 0x07: 0x58,
    0x10: 0x59, 0x06: 0x5A,
    0x1D: 0x30, 0x12: 0x31, 0x13: 0x32, 0x14: 0x33, 0x15: 0x34, 0x17: 0x35,
    0x16: 0x36, 0x1A: 0x37, 0x1C: 0x38, 0x19: 0x39,
    0x1B: 0xBD, 0x18: 0xBB, 0x21: 0xDB, 0x1E: 0xDD, 0x2A: 0xDC, 0x29: 0xBA,
    0x27: 0xDE, 0x2B: 0xBC, 0x2F: 0xBE, 0x2C: 0xBF, 0x32: 0xC0,
    0x35: 0x1B, 0x30: 0x09, 0x31: 0x20, 0x24: 0x0D, 0x4C: 0x10D, 0x33: 0x08,
    0x75: 0x2E, 0x39: 0x14,
    0x47: 0x90, 0x4B: 0x6F, 0x43: 0x6A, 0x4E: 0x6D, 0x45: 0x6B, 0x41: 0x6E,
    0x52: 0x60, 0x53: 0x61, 0x54: 0x62, 0x55: 0x63, 0x56: 0x64, 0x57: 0x65,
    0x58: 0x66, 0x59: 0x67, 0x5B: 0x68, 0x5C: 0x69,
    0x38: 0xA0, 0x3C: 0xA1, 0x3B: 0xA2, 0x3E: 0xA3, 0x3A: 0xA4, 0x3D: 0xA5,
    0x37: 0x5B, 0x36: 0x5C,
    0x7B: 0x25, 0x7C: 0x27, 0x7D: 0x28, 0x7E: 0x26,
    0x73: 0x24, 0x77: 0x23, 0x74: 0x21, 0x79: 0x22,
    0x7A: 0x70, 0x78: 0x71, 0x63: 0x72, 0x76: 0x73, 0x60: 0x74, 0x61: 0x75,
    0x62: 0x76, 0x64: 0x77, 0x65: 0x78, 0x6D: 0x79, 0x67: 0x7A, 0x6F: 0x7B,
}

MODIFIER_KEYCODE_MASK: dict[int, int] = {
    0x38: FLAG_SHIFT,
    0x3C: FLAG_SHIFT,
    0x3B: FLAG_CONTROL,
    0x3E: FLAG_CONTROL,
    0x3A: FLAG_OPTION,
    0x3D: FLAG_OPTION,
    0x37: FLAG_COMMAND,
    0x36: FLAG_COMMAND,
}


def hid_usage_to_vk(usage: int) -> int | None:
    """Map USB HID Keyboard/Keypad usage (Page 0x07) to unified VK."""
    if 0x04 <= usage <= 0x1D:
        return 0x41 + (usage - 0x04)
    if 0x1E <= usage <= 0x26:
        return 0x31 + (usage - 0x1E)
    if usage == 0x27:
        return 0x30
    if 0x3A <= usage <= 0x45:
        return 0x70 + (usage - 0x3A)
    if 0x59 <= usage <= 0x61:
        return 0x61 + (usage - 0x59)
    table = {
        0x28: 0x0D, 0x29: 0x1B, 0x2A: 0x08, 0x2B: 0x09, 0x2C: 0x20,
        0x2D: 0xBD, 0x2E: 0xBB, 0x2F: 0xDB, 0x30: 0xDD, 0x31: 0xDC, 0x32: 0xDC,
        0x33: 0xBA, 0x34: 0xDE, 0x35: 0xC0, 0x36: 0xBC, 0x37: 0xBE, 0x38: 0xBF,
        0x39: 0x14, 0x49: 0x2D, 0x4A: 0x24, 0x4B: 0x21, 0x4C: 0x2E, 0x4D: 0x23,
        0x4E: 0x22, 0x4F: 0x27, 0x50: 0x25, 0x51: 0x28, 0x52: 0x26,
        0x53: 0x90, 0x54: 0x6F, 0x55: 0x6A, 0x56: 0x6D, 0x57: 0x6B, 0x58: 0x10D,
        0x62: 0x60, 0x63: 0x6E,
        0xE0: 0xA2, 0xE1: 0xA0, 0xE2: 0xA4, 0xE3: 0x5B,
        0xE4: 0xA3, 0xE5: 0xA1, 0xE6: 0xA5, 0xE7: 0x5C,
    }
    return table.get(usage)


class _CGPoint(ctypes.Structure):
    _fields_ = [('x', ctypes.c_double), ('y', ctypes.c_double)]


_TAP_CALLBACK_TYPE = ctypes.CFUNCTYPE(
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.c_void_p,
)

_HID_CALLBACK_TYPE = ctypes.CFUNCTYPE(
    None,
    ctypes.c_void_p,
    ctypes.c_int32,
    ctypes.c_void_p,
    ctypes.c_void_p,
)

_cg_api = None


def _api():
    global _cg_api
    if sys.platform != 'darwin':
        return None
    if _cg_api is not None:
        return _cg_api
    try:
        cg = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        cf = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        iokit = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/IOKit.framework/IOKit')

        cg.CGPreflightListenEventAccess.restype = ctypes.c_bool
        cg.CGPreflightListenEventAccess.argtypes = []
        cg.CGRequestListenEventAccess.restype = ctypes.c_bool
        cg.CGRequestListenEventAccess.argtypes = []
        cg.CGEventTapCreate.restype = ctypes.c_void_p
        cg.CGEventTapCreate.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_uint64, _TAP_CALLBACK_TYPE, ctypes.c_void_p,
        ]
        cg.CGEventTapEnable.restype = None
        cg.CGEventTapEnable.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        cg.CGEventTapIsEnabled.restype = ctypes.c_bool
        cg.CGEventTapIsEnabled.argtypes = [ctypes.c_void_p]
        cg.CGEventGetLocation.restype = _CGPoint
        cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
        cg.CGEventGetIntegerValueField.restype = ctypes.c_int64
        cg.CGEventGetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        cg.CGEventGetFlags.restype = ctypes.c_uint64
        cg.CGEventGetFlags.argtypes = [ctypes.c_void_p]

        cf.CFStringCreateWithBytes.restype = ctypes.c_void_p
        cf.CFStringCreateWithBytes.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ssize_t, ctypes.c_uint32, ctypes.c_uint8]
        cf.CFNumberCreate.restype = ctypes.c_void_p
        cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p]
        cf.CFArrayCreate.restype = ctypes.c_void_p
        cf.CFArrayCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_ssize_t, ctypes.c_void_p]
        cf.CFDictionaryCreate.restype = ctypes.c_void_p
        cf.CFDictionaryCreate.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_void_p,
        ]
        cf.CFMachPortCreateRunLoopSource.restype = ctypes.c_void_p
        cf.CFMachPortCreateRunLoopSource.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]
        cf.CFMachPortInvalidate.restype = None
        cf.CFMachPortInvalidate.argtypes = [ctypes.c_void_p]
        cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
        cf.CFRunLoopGetCurrent.argtypes = []
        cf.CFRunLoopAddSource.restype = None
        cf.CFRunLoopAddSource.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        cf.CFRunLoopRemoveSource.restype = None
        cf.CFRunLoopRemoveSource.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        cf.CFRunLoopRunInMode.restype = ctypes.c_int32
        cf.CFRunLoopRunInMode.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_uint8]
        cf.CFRunLoopStop.restype = None
        cf.CFRunLoopStop.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]

        common_modes = ctypes.c_void_p.in_dll(cf, 'kCFRunLoopCommonModes').value
        default_mode = ctypes.c_void_p.in_dll(cf, 'kCFRunLoopDefaultMode').value
        dict_key_cb = ctypes.addressof(ctypes.c_uint8.in_dll(cf, 'kCFTypeDictionaryKeyCallBacks'))
        dict_val_cb = ctypes.addressof(ctypes.c_uint8.in_dll(cf, 'kCFTypeDictionaryValueCallBacks'))
        arr_cb = ctypes.addressof(ctypes.c_uint8.in_dll(cf, 'kCFTypeArrayCallBacks'))

        iokit.IOHIDManagerCreate.restype = ctypes.c_void_p
        iokit.IOHIDManagerCreate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        iokit.IOHIDManagerSetDeviceMatchingMultiple.restype = None
        iokit.IOHIDManagerSetDeviceMatchingMultiple.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        iokit.IOHIDManagerRegisterInputValueCallback.restype = None
        iokit.IOHIDManagerRegisterInputValueCallback.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        iokit.IOHIDManagerScheduleWithRunLoop.restype = None
        iokit.IOHIDManagerScheduleWithRunLoop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        iokit.IOHIDManagerUnscheduleFromRunLoop.restype = None
        iokit.IOHIDManagerUnscheduleFromRunLoop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        iokit.IOHIDManagerOpen.restype = ctypes.c_int32
        iokit.IOHIDManagerOpen.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        iokit.IOHIDManagerClose.restype = ctypes.c_int32
        iokit.IOHIDManagerClose.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        iokit.IOHIDValueGetElement.restype = ctypes.c_void_p
        iokit.IOHIDValueGetElement.argtypes = [ctypes.c_void_p]
        iokit.IOHIDValueGetIntegerValue.restype = ctypes.c_ssize_t
        iokit.IOHIDValueGetIntegerValue.argtypes = [ctypes.c_void_p]
        iokit.IOHIDElementGetUsagePage.restype = ctypes.c_uint32
        iokit.IOHIDElementGetUsagePage.argtypes = [ctypes.c_void_p]
        iokit.IOHIDElementGetUsage.restype = ctypes.c_uint32
        iokit.IOHIDElementGetUsage.argtypes = [ctypes.c_void_p]

        _cg_api = (cg, cf, iokit, common_modes, default_mode, dict_key_cb, dict_val_cb, arr_cb)
        return _cg_api
    except (OSError, AttributeError, ValueError):
        _cg_api = None
        return None


def has_listen_access() -> bool:
    api = _api()
    if api is None:
        return False
    try:
        return bool(api[0].CGPreflightListenEventAccess())
    except OSError:
        return False


def request_listen_access() -> bool:
    api = _api()
    if api is None:
        return False
    try:
        return bool(api[0].CGRequestListenEventAccess())
    except OSError:
        return False


def open_input_monitoring_settings() -> None:
    if sys.platform != 'darwin':
        return
    try:
        subprocess.Popen(
            ['open', 'x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        pass


def _current_app_bundle() -> Path | None:
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:
        if parent.suffix == '.app':
            return parent
    return None


def _current_adhoc_cdhash() -> str | None:
    bundle = _current_app_bundle()
    if bundle is None:
        return None
    try:
        proc = subprocess.run(
            ['/usr/bin/codesign', '-dvvv', str(bundle)],
            capture_output=True,
            text=True,
            timeout=3,
        )
        m = re.search(r'CDHash=([0-9a-fA-F]+)', proc.stdout + '\n' + proc.stderr)
        return m.group(1).lower() if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def cleanup_stale_tcc_if_rebuilt(state_dir: Path) -> bool:
    """Purge stale macOS TCC ghost entries automatically when an ad-hoc .app rebuild changes CDHash."""
    if sys.platform != 'darwin':
        return False
    cdhash = _current_adhoc_cdhash()
    if not cdhash:
        return False
    marker = state_dir / 'macos-adhoc-cdhash.txt'
    try:
        prev = marker.read_text(encoding='utf-8').strip() if marker.is_file() else None
    except OSError:
        prev = None

    reset_done = False
    if prev != cdhash and not has_listen_access():
        try:
            subprocess.run(['/usr/bin/tccutil', 'reset', 'ListenEvent', BUNDLE_ID], capture_output=True, timeout=3)
            reset_done = True
            time.sleep(0.15)
        except (OSError, subprocess.SubprocessError):
            pass
    if prev != cdhash:
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            marker.write_text(cdhash + '\n', encoding='utf-8')
        except OSError:
            pass
    return reset_done


def reset_and_reauthorize_input_monitoring() -> None:
    """Manual 1-click repair: clears stale TCC ListenEvent entry, triggers OS prompt, and opens settings."""
    if sys.platform != 'darwin':
        return
    def _worker():
        try:
            subprocess.run(['/usr/bin/tccutil', 'reset', 'ListenEvent', BUNDLE_ID], capture_output=True, timeout=3)
            time.sleep(0.2)
        except (OSError, subprocess.SubprocessError):
            pass
        request_listen_access()
        open_input_monitoring_settings()
    threading.Thread(target=_worker, name='BailongTCCRepair', daemon=True).start()


class _KeyDeduplicator:
    """Deduplicates keystrokes across CGEventTap (src=0) and IOHIDManager (src=1)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pressed: dict[int, tuple[float, int]] = {}
        self._recent_down: dict[int, tuple[float, int]] = {}

    def on_down(self, vk: int, src: int, repeat: bool) -> bool | None:
        """Return bool(is_repeat) if event should be emitted, or None if duplicate."""
        now = time.perf_counter()
        with self._lock:
            cur = self._pressed.get(vk)
            if cur is not None:
                if cur[1] != src:
                    return None
                return True if repeat else None
            prev = self._recent_down.get(vk)
            if prev is not None and prev[1] != src and (now - prev[0]) < 0.045:
                return None
            self._pressed[vk] = (now, src)
            self._recent_down[vk] = (now, src)
            return False

    def on_up(self, vk: int) -> bool:
        with self._lock:
            if vk not in self._pressed:
                return False
            del self._pressed[vk]
            return True

    def clear(self) -> None:
        with self._lock:
            self._pressed.clear()
            self._recent_down.clear()


class MacInputHook:
    """Self-healing CGEventTap + IOHIDManager listener that auto-connects as soon as permission is granted."""

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
        self._reconnect = threading.Event()
        self._lock = threading.Lock()
        self._tap_rl: int | None = None
        self._hid_rl: int | None = None
        self._tap_thread: threading.Thread | None = None
        self._hid_thread: threading.Thread | None = None
        self._dedup = _KeyDeduplicator()
        self._mod_down: set[int] = set()
        self._last_move_emit = 0.0
        self._tap_cb_ref = None
        self._hid_cb_ref = None

    def start(self) -> bool:
        if sys.platform != 'darwin' or _api() is None:
            return False
        with self._lock:
            if self._running and self._tap_thread is not None and self._tap_thread.is_alive():
                return True
            self._running = True
            self._reconnect.clear()
            self._dedup.clear()
            self._mod_down.clear()
            ready = threading.Event()
            self._tap_thread = threading.Thread(
                target=self._tap_loop, args=(ready,), name='BailongEventTap', daemon=True
            )
            self._hid_thread = threading.Thread(
                target=self._hid_loop, name='BailongIOHID', daemon=True
            )
            self._tap_thread.start()
            self._hid_thread.start()
        ready.wait(timeout=0.5)
        self.keyboard_active = has_listen_access()
        return self.keyboard_active or self.mouse_active

    def trigger_reconnect(self) -> None:
        self._reconnect.set()
        api = _api()
        if api is None:
            return
        cf = api[1]
        with self._lock:
            for rl in (self._tap_rl, self._hid_rl):
                if rl:
                    try:
                        cf.CFRunLoopStop(rl)
                    except OSError:
                        pass

    def stop(self) -> None:
        with self._lock:
            self._running = False
            tap_rl, hid_rl = self._tap_rl, self._hid_rl
            self._tap_rl = None
            self._hid_rl = None
        api = _api()
        if api is not None:
            cf = api[1]
            for rl in (tap_rl, hid_rl):
                if rl:
                    try:
                        cf.CFRunLoopStop(rl)
                    except OSError:
                        pass
        self.keyboard_active = False
        self.mouse_active = False
        self._dedup.clear()
        self._mod_down.clear()

    def _emit(self, item: tuple) -> None:
        self.events.put(item)
        if self.notify_ui is not None:
            try:
                self.notify_ui()
            except RuntimeError:
                pass

    def _tap_loop(self, ready: threading.Event) -> None:
        api = _api()
        if api is None:
            ready.set()
            return
        cg, cf, _, common_modes, default_mode, *_ = api

        mouse_mask = (
            (1 << K_CG_EVENT_LEFT_MOUSE_DOWN)
            | (1 << K_CG_EVENT_LEFT_MOUSE_UP)
            | (1 << K_CG_EVENT_RIGHT_MOUSE_DOWN)
            | (1 << K_CG_EVENT_RIGHT_MOUSE_UP)
            | (1 << K_CG_EVENT_OTHER_MOUSE_DOWN)
            | (1 << K_CG_EVENT_OTHER_MOUSE_UP)
            | (1 << K_CG_EVENT_MOUSE_MOVED)
            | (1 << K_CG_EVENT_LEFT_MOUSE_DRAGGED)
            | (1 << K_CG_EVENT_RIGHT_MOUSE_DRAGGED)
            | (1 << K_CG_EVENT_SCROLL_WHEEL)
        )
        key_mask = (
            (1 << K_CG_EVENT_KEY_DOWN)
            | (1 << K_CG_EVENT_KEY_UP)
            | (1 << K_CG_EVENT_FLAGS_CHANGED)
        )

        port_box = [None]

        def _on_tap(_proxy, event_type, event, _info):
            if event_type in (K_CG_EVENT_TAP_DISABLED_BY_TIMEOUT, K_CG_EVENT_TAP_DISABLED_BY_USER_INPUT):
                if port_box[0] and self._running:
                    cg.CGEventTapEnable(port_box[0], True)
                return event
            if not self._running or not event:
                return event
            try:
                if event_type == K_CG_EVENT_KEY_DOWN:
                    self.key_events_received += 1
                    self.keyboard_active = True
                    repeat = cg.CGEventGetIntegerValueField(event, K_CG_KEYBOARD_EVENT_AUTOREPEAT) != 0
                    if self.privacy_flag.is_set():
                        if not repeat:
                            self._emit(('privacy_key',))
                        return event
                    kc = int(cg.CGEventGetIntegerValueField(event, K_CG_KEYBOARD_EVENT_KEYCODE)) & 0xFFFF
                    vk = MACOS_KEYCODE_TO_VK.get(kc)
                    if vk is not None:
                        rep = self._dedup.on_down(vk, 0, repeat)
                        if rep is not None:
                            self._emit(('key_down', vk, rep))
                elif event_type == K_CG_EVENT_KEY_UP:
                    if self.privacy_flag.is_set():
                        return event
                    kc = int(cg.CGEventGetIntegerValueField(event, K_CG_KEYBOARD_EVENT_KEYCODE)) & 0xFFFF
                    vk = MACOS_KEYCODE_TO_VK.get(kc)
                    if vk is not None and self._dedup.on_up(vk):
                        self._emit(('key_up', vk))
                elif event_type == K_CG_EVENT_FLAGS_CHANGED:
                    self.key_events_received += 1
                    self.keyboard_active = True
                    if self.privacy_flag.is_set():
                        return event
                    kc = int(cg.CGEventGetIntegerValueField(event, K_CG_KEYBOARD_EVENT_KEYCODE)) & 0xFFFF
                    vk = MACOS_KEYCODE_TO_VK.get(kc)
                    flags = int(cg.CGEventGetFlags(event))
                    if kc == 0x39 and vk is not None:
                        if flags & FLAG_CAPS_LOCK:
                            if self._dedup.on_down(vk, 0, False) is not None:
                                self._emit(('key_down', vk, False))
                        elif self._dedup.on_up(vk):
                            self._emit(('key_up', vk))
                    elif vk is not None and kc in MODIFIER_KEYCODE_MASK:
                        mask = MODIFIER_KEYCODE_MASK[kc]
                        if (flags & mask) and kc not in self._mod_down:
                            self._mod_down.add(kc)
                            if self._dedup.on_down(vk, 0, False) is not None:
                                self._emit(('key_down', vk, False))
                        elif kc in self._mod_down:
                            self._mod_down.discard(kc)
                            if self._dedup.on_up(vk):
                                self._emit(('key_up', vk))
                elif event_type in (K_CG_EVENT_MOUSE_MOVED, K_CG_EVENT_LEFT_MOUSE_DRAGGED, K_CG_EVENT_RIGHT_MOUSE_DRAGGED):
                    now = time.perf_counter()
                    if now - self._last_move_emit >= 0.014:
                        self._last_move_emit = now
                        pt = cg.CGEventGetLocation(event)
                        self._emit(('mouse_move', float(pt.x), float(pt.y)))
                elif event_type == K_CG_EVENT_LEFT_MOUSE_DOWN:
                    self._emit(('mouse_down', 'left'))
                elif event_type == K_CG_EVENT_LEFT_MOUSE_UP:
                    self._emit(('mouse_up', 'left'))
                elif event_type == K_CG_EVENT_RIGHT_MOUSE_DOWN:
                    self._emit(('mouse_down', 'right'))
                elif event_type == K_CG_EVENT_RIGHT_MOUSE_UP:
                    self._emit(('mouse_up', 'right'))
                elif event_type == K_CG_EVENT_OTHER_MOUSE_DOWN:
                    self._emit(('mouse_down', 'middle'))
                elif event_type == K_CG_EVENT_OTHER_MOUSE_UP:
                    self._emit(('mouse_up', 'middle'))
                elif event_type == K_CG_EVENT_SCROLL_WHEEL:
                    delta = int(cg.CGEventGetIntegerValueField(event, K_CG_SCROLL_WHEEL_EVENT_DELTA_AXIS_1))
                    if delta != 0:
                        self._emit(('mouse_wheel', delta))
            except Exception:
                pass
            return event

        cb = _TAP_CALLBACK_TYPE(_on_tap)
        self._tap_cb_ref = cb
        rl = cf.CFRunLoopGetCurrent()
        with self._lock:
            self._tap_rl = rl

        first_iter = True
        while self._running:
            self._reconnect.clear()
            had_listen = has_listen_access()
            mask = (key_mask | mouse_mask) if had_listen else mouse_mask
            port = cg.CGEventTapCreate(
                K_CG_SESSION_EVENT_TAP,
                K_CG_HEAD_INSERT_EVENT_TAP,
                K_CG_EVENT_TAP_OPTION_LISTEN_ONLY,
                mask,
                cb,
                None,
            )
            if not port and had_listen:
                port = cg.CGEventTapCreate(
                    K_CG_SESSION_EVENT_TAP,
                    K_CG_HEAD_INSERT_EVENT_TAP,
                    K_CG_EVENT_TAP_OPTION_LISTEN_ONLY,
                    mouse_mask,
                    cb,
                    None,
                )
                had_listen = False

            self.mouse_active = bool(port)
            self.keyboard_active = bool(port and had_listen)
            if first_iter:
                first_iter = False
                ready.set()

            if not port:
                time.sleep(1.5)
                continue

            source = cf.CFMachPortCreateRunLoopSource(None, port, 0)
            if not source:
                cf.CFRelease(port)
                time.sleep(1.5)
                continue

            port_box[0] = port
            cf.CFRunLoopAddSource(rl, source, common_modes)
            cg.CGEventTapEnable(port, True)

            try:
                while self._running and not self._reconnect.is_set():
                    cf.CFRunLoopRunInMode(default_mode, 1.5, 0)
                    now_listen = has_listen_access()
                    # Auto-upgrade from mouse-only tap to full keyboard+mouse tap the moment user enables permission!
                    if not had_listen and now_listen:
                        self._emit(('permission_granted',))
                        break
                    if not cg.CGEventTapIsEnabled(port):
                        cg.CGEventTapEnable(port, True)
                        if not cg.CGEventTapIsEnabled(port):
                            break
            finally:
                port_box[0] = None
                try:
                    cf.CFRunLoopRemoveSource(rl, source, common_modes)
                    cf.CFMachPortInvalidate(port)
                    cf.CFRelease(source)
                    cf.CFRelease(port)
                except OSError:
                    pass

        ready.set()

    def _make_hid_dict(self, cf, dict_key_cb, dict_val_cb, usage_page: int, usage: int) -> int | None:
        kp_bytes = b'DeviceUsagePage'
        ku_bytes = b'DeviceUsage'
        k_page = cf.CFStringCreateWithBytes(None, kp_bytes, len(kp_bytes), K_CF_STRING_ENCODING_UTF8, 0)
        k_usage = cf.CFStringCreateWithBytes(None, ku_bytes, len(ku_bytes), K_CF_STRING_ENCODING_UTF8, 0)
        c_page = ctypes.c_int32(usage_page)
        c_usage = ctypes.c_int32(usage)
        v_page = cf.CFNumberCreate(None, K_CF_NUMBER_SINT32_TYPE, ctypes.byref(c_page))
        v_usage = cf.CFNumberCreate(None, K_CF_NUMBER_SINT32_TYPE, ctypes.byref(c_usage))
        keys = (ctypes.c_void_p * 2)(k_page, k_usage)
        vals = (ctypes.c_void_p * 2)(v_page, v_usage)
        d = cf.CFDictionaryCreate(None, keys, vals, 2, dict_key_cb, dict_val_cb)
        for h in (k_page, k_usage, v_page, v_usage):
            if h:
                cf.CFRelease(h)
        return d

    def _hid_loop(self) -> None:
        api = _api()
        if api is None:
            return
        _, cf, iokit, common_modes, default_mode, dict_key_cb, dict_val_cb, arr_cb = api

        def _on_hid(_ctx, _res, _sender, value):
            if not self._running or not value:
                return
            try:
                elem = iokit.IOHIDValueGetElement(value)
                if not elem:
                    return
                if iokit.IOHIDElementGetUsagePage(elem) != K_HID_PAGE_KEYBOARD_OR_KEYPAD:
                    return
                usage = int(iokit.IOHIDElementGetUsage(elem))
                if not (0x04 <= usage <= 0xE7):
                    return
                vk = hid_usage_to_vk(usage)
                if vk is None:
                    return
                pressed = iokit.IOHIDValueGetIntegerValue(value) != 0
                self.key_events_received += 1
                self.keyboard_active = True
                if self.privacy_flag.is_set():
                    if pressed:
                        self._emit(('privacy_key',))
                    return
                if pressed:
                    rep = self._dedup.on_down(vk, 1, False)
                    if rep is not None:
                        self._emit(('key_down', vk, rep))
                else:
                    if self._dedup.on_up(vk):
                        self._emit(('key_up', vk))
            except Exception:
                pass

        hid_cb = _HID_CALLBACK_TYPE(_on_hid)
        self._hid_cb_ref = hid_cb
        rl = cf.CFRunLoopGetCurrent()
        with self._lock:
            self._hid_rl = rl

        while self._running:
            if not has_listen_access():
                time.sleep(1.5)
                continue

            mgr = iokit.IOHIDManagerCreate(None, 0)
            if not mgr:
                time.sleep(2.0)
                continue

            d_kb = self._make_hid_dict(cf, dict_key_cb, dict_val_cb, K_HID_PAGE_GENERIC_DESKTOP, K_HID_USAGE_GD_KEYBOARD)
            d_kp = self._make_hid_dict(cf, dict_key_cb, dict_val_cb, K_HID_PAGE_GENERIC_DESKTOP, K_HID_USAGE_GD_KEYPAD)
            if d_kb and d_kp:
                arr_items = (ctypes.c_void_p * 2)(d_kb, d_kp)
                arr = cf.CFArrayCreate(None, arr_items, 2, arr_cb)
                if arr:
                    iokit.IOHIDManagerSetDeviceMatchingMultiple(mgr, arr)
                    cf.CFRelease(arr)
            for h in (d_kb, d_kp):
                if h:
                    cf.CFRelease(h)

            iokit.IOHIDManagerRegisterInputValueCallback(mgr, ctypes.cast(hid_cb, ctypes.c_void_p), None)
            iokit.IOHIDManagerScheduleWithRunLoop(mgr, rl, common_modes)
            ret = iokit.IOHIDManagerOpen(mgr, 0)
            if ret != 0:
                iokit.IOHIDManagerRegisterInputValueCallback(mgr, None, None)
                iokit.IOHIDManagerUnscheduleFromRunLoop(mgr, rl, common_modes)
                cf.CFRelease(mgr)
                time.sleep(2.0)
                continue

            self.keyboard_active = True
            try:
                while self._running and has_listen_access():
                    cf.CFRunLoopRunInMode(default_mode, 1.5, 0)
            finally:
                try:
                    iokit.IOHIDManagerRegisterInputValueCallback(mgr, None, None)
                    iokit.IOHIDManagerUnscheduleFromRunLoop(mgr, rl, common_modes)
                    iokit.IOHIDManagerClose(mgr, 0)
                    cf.CFRelease(mgr)
                except OSError:
                    pass
                time.sleep(0.3)
