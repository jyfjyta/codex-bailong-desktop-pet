"""Controller uniting dual-channel MacInputHook, PrivacyGuard, Codex auto-typing, and adaptive clock."""
from __future__ import annotations

import queue
import random
import sys
import threading
import time
from pathlib import Path
from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QPainter
from PySide6.QtWidgets import QApplication

from .layout import COMPANION_H, COMPANION_W
from .renderer import TypingCompanionRenderer

CODING_AUTO_VKS: tuple[int, ...] = (
    0x41, 0x43, 0x44, 0x45, 0x46, 0x49, 0x4C, 0x4E, 0x4F, 0x50,
    0x52, 0x53, 0x54, 0x55, 0x4B, 0x20, 0x20, 0x0D, 0x08, 0xBC, 0xBE,
)

ACTIVE_INTERVAL_MS = 16
IDLE_INTERVAL_MS = 120


class TypingCompanionController(QObject):
    """Manages lifecycle, dual-channel macOS input hooks, privacy guard, and Codex auto-typing."""

    wakeupRequested = Signal()
    privacyChanged = Signal(bool)

    def __init__(self, pet_widget, asset_dir: Path | None = None) -> None:
        super().__init__(pet_widget)
        self.pet = pet_widget
        self.renderer = TypingCompanionRenderer(asset_dir=asset_dir)
        self.active = False
        self._paused = False
        self._wakeup_queued = False
        self._prompted_permission = False

        self._events: queue.SimpleQueue = queue.SimpleQueue()
        self._privacy_flag = threading.Event()
        self._hook = None
        self._privacy_guard = None

        self._last_cursor_gx: float | None = None
        self._last_cursor_gy: float | None = None
        self._last_clock = time.perf_counter()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(ACTIVE_INTERVAL_MS)
        self.timer.timeout.connect(self._on_tick)

        self._codex_working = False
        self._real_input_hold_until = 0.0
        self._held_virtual_vk: int | None = None
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self._on_auto_type_step)

        self.wakeupRequested.connect(self._drain_and_step, Qt.ConnectionType.QueuedConnection)
        self.privacyChanged.connect(self._on_privacy_changed, Qt.ConnectionType.QueuedConnection)

    def start(self) -> None:
        if self.active:
            if self._paused:
                self.resume()
            return
        self.active = True
        self._paused = False
        now = time.perf_counter()
        self._last_clock = now
        self.renderer.reset_transient(now)
        self._start_native_watchers()
        self.timer.setInterval(ACTIVE_INTERVAL_MS)
        self.timer.start()
        self._sync_auto_typing()
        self.pet.update()

    def stop(self) -> None:
        if not self.active:
            return
        self.active = False
        self._paused = False
        self.timer.stop()
        self._stop_auto_typing()
        self._stop_native_watchers()
        self.renderer.reset_transient(time.perf_counter())

    def pause(self) -> None:
        if not self.active or self._paused:
            return
        self._paused = True
        self.timer.stop()
        self._stop_auto_typing()
        self._stop_native_watchers()

    def resume(self) -> None:
        if not self.active or not self._paused:
            return
        self._paused = False
        self._last_cursor_gx = None
        self._last_cursor_gy = None
        self._last_clock = time.perf_counter()
        self.renderer.reset_transient(self._last_clock)
        self._start_native_watchers()
        self.timer.setInterval(ACTIVE_INTERVAL_MS)
        self.timer.start()
        self._sync_auto_typing()
        self.pet.update()

    def apply_options(self, settings: dict) -> None:
        self.renderer.show_key_bubbles = bool(settings.get('tc_key_bubbles', True))
        if self._privacy_guard is not None:
            self._privacy_guard.enabled = bool(settings.get('tc_privacy_guard', True))

    def reconnect_permissions(self) -> None:
        if sys.platform == 'darwin':
            from mac_input_hook import reset_and_reauthorize_input_monitoring
            reset_and_reauthorize_input_monitoring()
            if self._hook is not None:
                QTimer.singleShot(500, self._hook.trigger_reconnect)
        elif sys.platform == 'win32' and self._hook is not None:
            self._hook.trigger_reconnect()

    def on_codex_activity(self, event: dict) -> None:
        kind = event.get('kind')
        if kind == 'complete':
            self._codex_working = False
            self._sync_auto_typing()
            if self.active and not self._paused:
                self.renderer.trigger_celebrate(time.perf_counter(), 2.8)
                self._ensure_fast_clock()
                self.pet.update()
            return
        if kind in ('error', 'failed'):
            self._codex_working = False
            self._sync_auto_typing()
            if self.active and not self._paused:
                self.renderer.trigger_angry(time.perf_counter(), 2.8)
                self._ensure_fast_clock()
                self.pet.update()
            return
        if kind == 'status':
            state = event.get('state', 'idle')
            active_count = int(event.get('active', 0) or 0)
            self._codex_working = state in ('working', 'responding') or active_count > 0
            self._sync_auto_typing()

    def on_local_hover(self, pos: QPointF, prev_pos: QPointF | None) -> bool:
        if not self.active or self._paused:
            return False
        w, h = float(self.pet.width()), float(self.pet.height())
        over_head = self.renderer.is_head_point(pos.x(), pos.y(), w, h)
        if over_head and prev_pos is not None:
            now = time.perf_counter()
            if self.renderer.on_head_stroke(pos.x() - prev_pos.x(), pos.y() - prev_pos.y(), now):
                self._ensure_fast_clock()
                self.pet.update()
        return over_head

    def on_single_click(self) -> None:
        if not self.active or self._paused:
            return
        now = time.perf_counter()
        self.renderer.on_pet_click(now)
        self._ensure_fast_clock()
        self.pet.update()

    def on_drag_shake(self) -> None:
        if not self.active or self._paused:
            return
        now = time.perf_counter()
        self.renderer.trigger_angry(now, 2.6)
        self._ensure_fast_clock()
        self.pet.update()

    def trigger_expression(self, expr: str | None) -> None:
        if not self.active or self._paused:
            return
        now = time.perf_counter()
        self.renderer.trigger_manual_expression(expr, now, 6.0)
        self._ensure_fast_clock()
        self.pet.update()

    def hit_test(self, x: float, y: float, w: float, h: float) -> bool:
        return self.renderer.hit_test(x, y, w, h)

    def paint(self, painter: QPainter, rect: QRectF) -> None:
        dpr = self.pet.devicePixelRatioF() if hasattr(self.pet, 'devicePixelRatioF') else 1.0
        self.renderer.paint(painter, rect, dpr, time.perf_counter())

    def _start_native_watchers(self) -> None:
        platform = QApplication.platformName()
        if platform in ('offscreen', 'minimal', 'minimalegl'):
            return
        if sys.platform not in ('darwin', 'win32'):
            return
        if sys.platform == 'darwin' and platform != 'cocoa':
            return
        if sys.platform == 'win32' and platform != 'windows':
            return

        while not self._events.empty():
            try:
                self._events.get_nowait()
            except queue.Empty:
                break

        from privacy_guard import PrivacyGuard

        if sys.platform == 'darwin':
            from mac_input_hook import (
                MacInputHook,
                cleanup_stale_tcc_if_rebuilt,
                has_listen_access,
                open_input_monitoring_settings,
                request_listen_access,
            )
            from platform_paths import data_dir

            cleanup_stale_tcc_if_rebuilt(data_dir())

            if not has_listen_access() and not self._prompted_permission:
                self._prompted_permission = True
                request_listen_access()
                open_input_monitoring_settings()
                if hasattr(self.pet, 'say'):
                    self.pet.say(
                        '请在弹出的「系统设置 → 输入监控」中勾选小白龙（勾选后无需重启，立即自动生效并弹出按键气泡！）',
                        9000,
                    )

            self._privacy_guard = PrivacyGuard(
                self._privacy_flag,
                on_change=lambda active: self.privacyChanged.emit(active),
            )
            self._privacy_guard.enabled = bool(self.pet.settings.get('tc_privacy_guard', True))
            self._privacy_guard.start()

            self._hook = MacInputHook(
                self._events,
                self._privacy_flag,
                notify_ui=self._queue_wakeup,
            )
            self._hook.start()
        elif sys.platform == 'win32':
            from win_input_hook import WinInputHook

            self._privacy_guard = PrivacyGuard(
                self._privacy_flag,
                on_change=lambda active: self.privacyChanged.emit(active),
            )
            self._privacy_guard.enabled = bool(self.pet.settings.get('tc_privacy_guard', True))
            self._privacy_guard.start()

            self._hook = WinInputHook(
                self._events,
                self._privacy_flag,
                notify_ui=self._queue_wakeup,
            )
            self._hook.start()

    def _stop_native_watchers(self) -> None:
        if self._hook is not None:
            self._hook.stop()
            self._hook = None
        if self._privacy_guard is not None:
            self._privacy_guard.stop()
            self._privacy_guard = None
        self._privacy_flag.clear()
        self._wakeup_queued = False

    def _queue_wakeup(self) -> None:
        if not self._wakeup_queued and self.active and not self._paused:
            self._wakeup_queued = True
            self.wakeupRequested.emit()

    def _on_privacy_changed(self, active: bool) -> None:
        if not self.active:
            return
        now = time.perf_counter()
        self.renderer.set_privacy_mode(active, now)
        if active:
            self._release_virtual_key(now)
        self._ensure_fast_clock()
        self.pet.update()

    def _screen_norm_cursor(self, gx: float, gy: float) -> tuple[float, float]:
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is None:
            return 0.5, 0.5
        geo = screen.geometry()
        nx = (gx - geo.left()) / max(1.0, float(geo.width()))
        ny = (gy - geo.top()) / max(1.0, float(geo.height()))
        return max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny))

    def _update_cursor_eye_vector(self, gx: float, gy: float, now: float) -> bool:
        """Compute global vector from Bailong's eyes on screen to the user's real mouse cursor."""
        moved = (
            self._last_cursor_gx is None
            or abs(gx - self._last_cursor_gx) > 1.5
            or abs(gy - self._last_cursor_gy) > 1.5
        )
        self._last_cursor_gx, self._last_cursor_gy = gx, gy
        eye_l = self.renderer.eye_logical_pos()
        w = max(1.0, float(self.pet.width()))
        h = max(1.0, float(self.pet.height()))
        local_pt = QPoint(round(eye_l.x() * w / COMPANION_W), round(eye_l.y() * h / COMPANION_H))
        eye_global = self.pet.mapToGlobal(local_pt)
        self.renderer.on_cursor_vector(gx - float(eye_global.x()), gy - float(eye_global.y()), now)
        return moved

    def _drain_events(self, now: float) -> bool:
        had_any = False
        while not self._events.empty():
            try:
                ev = self._events.get_nowait()
            except queue.Empty:
                break
            had_any = True
            kind = ev[0]
            if kind == 'permission_granted':
                if hasattr(self.pet, 'say'):
                    self.pet.say('键盘输入监听已连接成功！快敲几下键盘试试吧～', 4500)
                continue
            if kind != 'privacy_key':
                self._real_input_hold_until = now + 1.8
                self._release_virtual_key(now)
            if kind == 'key_down':
                self.renderer.on_key_down(ev[1], now, repeat=ev[2], synthetic=False)
            elif kind == 'key_up':
                self.renderer.on_key_up(ev[1], now)
            elif kind == 'privacy_key':
                self.renderer.on_privacy_pulse(now)
            elif kind == 'mouse_move':
                if sys.platform == 'win32':
                    # On Windows DPI-scaled desktops, QCursor.pos() returns DIPs matching QScreen.geometry().
                    pos = QCursor.pos()
                    gx, gy = float(pos.x()), float(pos.y())
                else:
                    gx, gy = float(ev[1]), float(ev[2])
                nx, ny = self._screen_norm_cursor(gx, gy)
                self.renderer.on_mouse_move(nx, ny, now)
                self._update_cursor_eye_vector(gx, gy, now)
            elif kind == 'mouse_down':
                self.renderer.on_mouse_button(ev[1], True, now)
            elif kind == 'mouse_up':
                self.renderer.on_mouse_button(ev[1], False, now)
            elif kind == 'mouse_wheel':
                self.renderer.on_mouse_wheel(ev[1], now)
        return had_any

    def _drain_and_step(self) -> None:
        self._wakeup_queued = False
        if not self.active or self._paused:
            return
        now = time.perf_counter()
        if self._drain_events(now):
            self._ensure_fast_clock()
            dt = now - self._last_clock
            self._last_clock = now
            self.renderer.step(now, dt)
            self.pet.update()

    def _ensure_fast_clock(self) -> None:
        if self.timer.interval() != ACTIVE_INTERVAL_MS:
            self._last_clock = time.perf_counter()
            self.timer.setInterval(ACTIVE_INTERVAL_MS)

    def _on_tick(self) -> None:
        if not self.active or self._paused or not self.pet.isVisible():
            return
        now = time.perf_counter()
        self._drain_events(now)

        # Always track user's real global mouse cursor smoothly for Live2D eye gaze & mouse position
        if QApplication.platformName() in ('cocoa', 'windows'):
            pos = QCursor.pos()
            gx, gy = float(pos.x()), float(pos.y())
            if self._update_cursor_eye_vector(gx, gy, now):
                nx, ny = self._screen_norm_cursor(gx, gy)
                self.renderer.on_mouse_move(nx, ny, now)

        dt = now - self._last_clock
        self._last_clock = now
        self.renderer.step(now, dt)
        self.pet.update()

        target_ms = ACTIVE_INTERVAL_MS if self.renderer.is_animating_fast(now) else IDLE_INTERVAL_MS
        if self.timer.interval() != target_ms:
            self.timer.setInterval(target_ms)

    def _sync_auto_typing(self) -> None:
        want = (
            self.active
            and not self._paused
            and self._codex_working
            and bool(self.pet.settings.get('tc_codex_autotype', True))
        )
        if want and not self._auto_timer.isActive():
            self._auto_timer.start(140)
        elif not want:
            self._stop_auto_typing()

    def _release_virtual_key(self, now: float) -> None:
        if self._held_virtual_vk is not None:
            self.renderer.on_key_up(self._held_virtual_vk, now)
            self._held_virtual_vk = None

    def _stop_auto_typing(self) -> None:
        self._auto_timer.stop()
        self._release_virtual_key(time.perf_counter())

    def _on_auto_type_step(self) -> None:
        if not self.active or self._paused or not self._codex_working:
            self._release_virtual_key(time.perf_counter())
            return
        now = time.perf_counter()
        self._release_virtual_key(now)
        if not self.renderer.privacy_mode and now >= self._real_input_hold_until:
            vk = random.choice(CODING_AUTO_VKS)
            self._held_virtual_vk = vk
            self.renderer.on_key_down(vk, now, repeat=False, synthetic=True)
            self._ensure_fast_clock()
            self.pet.update()
        delay = random.randint(75, 165) if random.random() < 0.82 else random.randint(260, 480)
        self._auto_timer.start(delay)
