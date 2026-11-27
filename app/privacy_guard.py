"""Cross-platform native password-field privacy detector (macOS Secure Input/AX + Windows Win32/UIA)."""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from typing import Callable

K_CF_STRING_ENCODING_UTF8 = 0x08000100

_libs_loaded = False
_carbon = None
_ax = None
_cf = None


def _ensure_libs() -> bool:
    global _libs_loaded, _carbon, _ax, _cf
    if sys.platform != 'darwin':
        return False
    if _libs_loaded:
        return _carbon is not None
    _libs_loaded = True
    try:
        carbon = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/Carbon.framework/Carbon')
        carbon.IsSecureEventInputEnabled.restype = ctypes.c_uint8
        carbon.IsSecureEventInputEnabled.argtypes = []

        ax = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices'
        )
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        ax.AXIsProcessTrusted.argtypes = []
        ax.AXUIElementCreateSystemWide.restype = ctypes.c_void_p
        ax.AXUIElementCreateSystemWide.argtypes = []
        ax.AXUIElementCopyAttributeValue.restype = ctypes.c_int32
        ax.AXUIElementCopyAttributeValue.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]

        cf = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation'
        )
        cf.CFRelease.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFStringCreateWithBytes.restype = ctypes.c_void_p
        cf.CFStringCreateWithBytes.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_ssize_t,
            ctypes.c_uint32,
            ctypes.c_uint8,
        ]
        cf.CFGetTypeID.restype = ctypes.c_ulong
        cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
        cf.CFStringGetTypeID.restype = ctypes.c_ulong
        cf.CFStringGetTypeID.argtypes = []
        cf.CFEqual.restype = ctypes.c_uint8
        cf.CFEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        _carbon, _ax, _cf = carbon, ax, cf
        return True
    except (OSError, AttributeError):
        _carbon = _ax = _cf = None
        return False


def _cf_string(text: str) -> int | None:
    if _cf is None:
        return None
    raw = text.encode('utf-8')
    return _cf.CFStringCreateWithBytes(None, raw, len(raw), K_CF_STRING_ENCODING_UTF8, 0)


def _ax_focused_is_secure() -> bool:
    if _ax is None or _cf is None:
        return False
    try:
        if not _ax.AXIsProcessTrusted():
            return False
        sys_wide = _ax.AXUIElementCreateSystemWide()
        if not sys_wide:
            return False
        attr_focused = _cf_string('AXFocusedUIElement')
        attr_subrole = _cf_string('AXSubrole')
        secure_role = _cf_string('AXSecureTextField')
        is_secure = False
        try:
            if attr_focused and attr_subrole and secure_role:
                focused = ctypes.c_void_p()
                if _ax.AXUIElementCopyAttributeValue(sys_wide, attr_focused, ctypes.byref(focused)) == 0 and focused.value:
                    try:
                        subrole = ctypes.c_void_p()
                        if _ax.AXUIElementCopyAttributeValue(focused.value, attr_subrole, ctypes.byref(subrole)) == 0 and subrole.value:
                            try:
                                if (
                                    _cf.CFGetTypeID(subrole.value) == _cf.CFStringGetTypeID()
                                    and _cf.CFEqual(subrole.value, secure_role) != 0
                                ):
                                    is_secure = True
                            finally:
                                _cf.CFRelease(subrole.value)
                    finally:
                        _cf.CFRelease(focused.value)
        finally:
            for handle in (secure_role, attr_subrole, attr_focused, sys_wide):
                if handle:
                    _cf.CFRelease(handle)
        return is_secure
    except (OSError, ValueError):
        return False


class _WinPasswordProbe:
    """Windows focused password-input detector: Win32 ES_PASSWORD/EM_GETPASSWORDCHAR + UIA IsPassword."""

    GWL_STYLE = -16
    ES_PASSWORD = 0x0020
    EM_GETPASSWORDCHAR = 0x00D2
    SMTO_ABORTIFHUNG = 0x0002

    def __init__(self) -> None:
        self._com = None
        self._client = None
        self._uia = None
        self._user32 = None
        self._gui_type = None
        if sys.platform != 'win32':
            return
        try:
            from ctypes import wintypes

            class _GUITHREADINFO(ctypes.Structure):
                _fields_ = [
                    ('cbSize', wintypes.DWORD),
                    ('flags', wintypes.DWORD),
                    ('hwndActive', wintypes.HWND),
                    ('hwndFocus', wintypes.HWND),
                    ('hwndCapture', wintypes.HWND),
                    ('hwndMenuOwner', wintypes.HWND),
                    ('hwndMoveSize', wintypes.HWND),
                    ('hwndCaret', wintypes.HWND),
                    ('rcCaret', wintypes.RECT),
                ]

            u = ctypes.WinDLL('user32', use_last_error=True)
            u.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(_GUITHREADINFO)]
            u.GetGUIThreadInfo.restype = wintypes.BOOL
            u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
            u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            u.SendMessageTimeoutW.argtypes = [
                wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
                wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
            ]
            u.SendMessageTimeoutW.restype = ctypes.c_ssize_t
            self._user32 = u
            self._gui_type = _GUITHREADINFO
        except (OSError, AttributeError):
            self._user32 = None
        try:
            import comtypes
            import comtypes.client
            self._com = comtypes
            self._client = comtypes.client
        except ImportError:
            self._com = None
            self._client = None

    def open_thread(self) -> None:
        if self._com is None:
            return
        try:
            self._com.CoInitializeEx(0)
        except OSError:
            pass

    def close_thread(self) -> None:
        self._uia = None
        if self._com is not None:
            try:
                self._com.CoUninitialize()
            except OSError:
                pass

    def _win32_is_password(self) -> bool:
        if self._user32 is None or self._gui_type is None:
            return False
        try:
            gui = self._gui_type()
            gui.cbSize = ctypes.sizeof(gui)
            if not self._user32.GetGUIThreadInfo(0, ctypes.byref(gui)):
                return False
            hwnd = gui.hwndFocus
            if not hwnd:
                return False
            style = int(self._user32.GetWindowLongPtrW(hwnd, self.GWL_STYLE))
            if style & self.ES_PASSWORD:
                return True
            out = ctypes.c_size_t(0)
            ok = self._user32.SendMessageTimeoutW(
                hwnd, self.EM_GETPASSWORDCHAR, 0, 0, self.SMTO_ABORTIFHUNG, 60, ctypes.byref(out)
            )
            if ok and out.value != 0:
                return True
        except (OSError, ValueError):
            return False
        return False

    def _uia_is_password(self) -> bool:
        if self._client is None:
            return False
        try:
            if self._uia is None:
                types = self._client.GetModule('UIAutomationCore.dll')
                uia = self._client.CreateObject(types.CUIAutomation8, interface=types.IUIAutomation2)
                uia.ConnectionTimeout = 400
                uia.TransactionTimeout = 500
                self._uia = uia
            focused = self._uia.GetFocusedElement()
            if focused is not None:
                return bool(focused.CurrentIsPassword)
        except Exception:
            self._uia = None
        return False

    def is_password(self) -> bool:
        return self._win32_is_password() or self._uia_is_password()


def is_focused_password_field() -> bool:
    """Return True if a password input has focus (macOS Secure Input/AX, or Windows Win32)."""
    if sys.platform == 'win32':
        probe = _WinPasswordProbe()
        return probe._win32_is_password()
    if not _ensure_libs():
        return False
    try:
        if _carbon.IsSecureEventInputEnabled() != 0:
            return True
    except OSError:
        pass
    return _ax_focused_is_secure()


class PrivacyGuard:
    """Background watcher that sets a shared Event when a password input is active."""

    def __init__(
        self,
        privacy_flag: threading.Event,
        on_change: Callable[[bool], None] | None = None,
        interval_s: float = 0.15,
    ) -> None:
        self.privacy_flag = privacy_flag
        self.on_change = on_change
        self.interval_s = interval_s
        self.enabled = True
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='BailongPrivacyGuard', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.privacy_flag.clear()
        self._thread = None

    def _run(self) -> None:
        active = False
        clear_streak = 0
        win_probe = _WinPasswordProbe() if sys.platform == 'win32' else None
        if win_probe is not None:
            win_probe.open_thread()
        try:
            while not self._stop.wait(self.interval_s):
                if not self.enabled:
                    if active:
                        active = False
                        clear_streak = 0
                        self.privacy_flag.clear()
                        if self.on_change:
                            self.on_change(False)
                    continue
                is_pwd = win_probe.is_password() if win_probe is not None else is_focused_password_field()
                if is_pwd:
                    clear_streak = 0
                    if not active:
                        active = True
                        self.privacy_flag.set()
                        if self.on_change:
                            self.on_change(True)
                elif active:
                    clear_streak += 1
                    if clear_streak >= 2:
                        active = False
                        clear_streak = 0
                        self.privacy_flag.clear()
                        if self.on_change:
                            self.on_change(False)
        finally:
            if win_probe is not None:
                win_probe.close_thread()
