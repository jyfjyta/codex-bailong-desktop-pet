"""v8 Compact Steam-BongoCat Bailong Renderer: 100% original-art shoulder-pivoted cuffed paws with live swing, mouse-glide & lift-slap."""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)

from .layout import COMPANION_H, COMPANION_W, DESK_TILT_DEG, LINEART_HEX, TAIL_KEY_IDS, UI_FONT_FAMILY, PerspectiveDeck


class Spring:
    """240Hz sub-stepped second-order damped harmonic oscillator."""

    def __init__(self, initial: float = 0.0, hz: float = 3.0, damping: float = 0.85) -> None:
        self.x = initial
        self.v = 0.0
        self.hz = hz
        self.damping = damping

    def step(self, target: float, dt: float) -> float:
        if dt <= 0.0:
            return self.x
        dt = min(0.05, dt)
        w = 2.0 * math.pi * self.hz
        n = max(1, math.ceil(dt * 240.0))
        h = dt / n
        for _ in range(n):
            self.v += (w * w * (target - self.x) - 2.0 * self.damping * w * self.v) * h
            self.x += self.v * h
        return self.x

    def kick(self, dv: float) -> None:
        self.v += dv


@dataclass
class PawState:
    """Live KuroCat 4-state paw kinematics driving original-art shoulder-pivoted cuffed paws."""

    side: str  # 'left' (mouse + left keyboard zone) | 'right' (right keyboard zone)
    x: float
    y: float
    tx: float
    ty: float
    mode: str = 'idle'  # 'mouse' | 'poke' | 'curled' | 'idle'
    poke_time: float = 0.0
    last_active_at: float = -100.0
    squash: float = 0.0
    lift: float = 0.0   # 0 = resting/pressing on desk; 1 = lifted up mid-swing / curled hover
    held_vks: list[int] = field(default_factory=list)


@dataclass
class KeyBubble:
    label: str
    is_space: bool
    accent_color: str | None
    x: float
    y: float
    vx: float
    vy: float
    rot: float
    born: float
    ttl: float = 0.86


@dataclass
class FxParticle:
    kind: str  # 'spark' | 'heart' | 'zzz'
    x: float
    y: float
    vx: float
    vy: float
    life: float = 1.0


@dataclass
class KeyRipple:
    x: float
    y: float
    r: float = 3.5
    life: float = 1.0


class TypingCompanionRenderer:
    """Renders compact BongoCat-tilted Bailong with 100% original-art shoulder-pivoted cuffed paws & global mouse eye tracking."""

    CHAR_DRAW_W = 228.0
    CHAR_DRAW_H = 228.0
    CHAR_ORIGIN_X = 26.0
    CHAR_ORIGIN_Y = -6.0

    SPRITE_NAMES: tuple[str, ...] = (
        'bongo_body_idle',
        'bongo_body_blink',
        'bongo_body_happy',
        'bongo_body_wink',
        'bongo_body_angry',
        'bongo_body_sleepy',
        'bongo_body_starry',
        'bongo_body_smug',
        'bongo_body_flustered',
        'bongo_body_shy',
        'bongo_sclera',
        'bongo_irises',
        'bongo_eye_frame',
        'bongo_collar_front',
        'bongo_paw_l',
        'bongo_paw_r',
    )

    EXPRESSION_NAMES: tuple[str, ...] = (
        'happy',
        'wink',
        'angry',
        'sleepy',
        'starry',
        'smug',
        'flustered',
    )

    def __init__(self, asset_dir: Path | None = None) -> None:
        if asset_dir is None:
            try:
                from platform_paths import app_root
                candidate = app_root() / 'app' / 'typing_companion' / 'assets'
                asset_dir = candidate if candidate.is_dir() else (Path(__file__).resolve().parent / 'assets')
            except Exception:
                asset_dir = Path(__file__).resolve().parent / 'assets'
        self.asset_dir = asset_dir
        self.deck = PerspectiveDeck()
        self._raw_images: dict[str, QImage] | None = None
        self._pixmaps: dict[str, QPixmap] | None = None
        self._scaled_key: tuple[int, int] | None = None
        self._scaled_cache: dict[str, QPixmap] = {}

        self._body_size = 512.0
        self._eye_center_norm = (0.5078, 0.5020)
        self._shoulder_l_norm = (0.4141, 0.6367)
        self._shoulder_r_norm = (0.5977, 0.6562)
        self._paw_meta: dict[str, dict] = {
            'left': {'w': 98, 'h': 76, 'pivot': [78.0, 14.0], 'tip': [34.0, 54.0]},
            'right': {'w': 78, 'h': 78, 'pivot': [38.0, 10.0], 'tip': [34.0, 54.0]},
        }
        meta_path = self.asset_dir / 'meta.json'
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding='utf-8'))
                if meta.get('version', 0) >= 8:
                    self._body_size = float(meta.get('body_size', 512.0))
                    self._eye_center_norm = tuple(meta['eye_center_norm'])
                    self._shoulder_l_norm = tuple(meta['shoulder_l_norm'])
                    self._shoulder_r_norm = tuple(meta['shoulder_r_norm'])
                    self._paw_meta = {'left': meta['paw_l'], 'right': meta['paw_r']}
            except (OSError, ValueError, KeyError):
                pass

        self.shift_spring = Spring(0.0, hz=5.2, damping=0.88)
        self.bob_spring = Spring(0.0, hz=4.6, damping=0.84)
        self.pet_lean_spring = Spring(0.0, hz=4.8, damping=0.88)

        # KuroCat-style fast liquid Live2D eye gaze tracking REAL global desktop mouse cursor!
        self.eye_x_spring = Spring(0.0, hz=8.8, damping=0.94)
        self.eye_y_spring = Spring(0.0, hz=8.8, damping=0.94)
        self.cursor_dx = 0.0
        self.cursor_dy = 0.0
        self.has_cursor_vec = False

        self.mouse_nx = 0.5
        self.mouse_ny = 0.5
        self.mouse_left = False
        self.mouse_right = False
        self.mouse_middle = False
        self.mouse_left_press = 0.0
        self.mouse_right_press = 0.0
        self.mouse_middle_press = 0.0
        self.mouse_active_at = 0.0
        self.wheel_pulse = 0.0

        mx, my = self._mouse_grip_world()
        rx, ry = self._right_rest_world()
        self.left_paw = PawState('left', mx, my, mx, my, mode='mouse')
        self.right_paw = PawState('right', rx, ry, rx, ry, mode='idle')

        self.pressed_keys: dict[str, float] = {}
        self.vk_owner: dict[int, str] = {}
        self.vk_to_key_id: dict[int, str] = {}
        self._last_tap_side = 'right'

        self.privacy_mode = False
        self.privacy_pulse_at = -100.0
        self.show_key_bubbles = True

        self.last_input_at = 0.0
        self.last_key_at = -100.0
        self.last_key_x = self.deck.KB_CX
        self.key_timestamps: list[float] = []
        self.typing_heat = 0.0
        self.mood_tier = 0
        self.mood_changed_at = 0.0

        self.next_blink_at = 4.2
        self.blink_until = 0.0
        self.celebrate_until = 0.0
        self.happy_until = 0.0
        self.wink_until = 0.0
        self.angry_until = 0.0
        self.starry_until = 0.0
        self.smug_until = 0.0
        self.flustered_until = 0.0
        self.manual_expr: str | None = None
        self.manual_expr_until = 0.0
        self.click_timestamps: list[float] = []
        self.backspace_timestamps: list[float] = []
        self.pet_energy = 0.0
        self.pet_dir_x = 0.0
        self.drowsy = False
        self.sleeping = False
        self._next_zzz_at = 0.0

        self.bubbles: list[KeyBubble] = []
        self.particles: list[FxParticle] = []
        self.ripples: list[KeyRipple] = []

        self._font_bubble = QFont(UI_FONT_FAMILY)
        self._font_bubble.setPixelSize(10)
        self._font_bubble.setWeight(QFont.Weight.Bold)

        self._font_badge = QFont(UI_FONT_FAMILY)
        self._font_badge.setPixelSize(8)
        self._font_badge.setWeight(QFont.Weight.Bold)

        self._font_key_alpha = QFont(UI_FONT_FAMILY)
        self._font_key_alpha.setPixelSize(6)
        self._font_key_alpha.setWeight(QFont.Weight.Bold)

        self._font_key_mod = QFont(UI_FONT_FAMILY)
        self._font_key_mod.setPixelSize(5)
        self._font_key_mod.setWeight(QFont.Weight.DemiBold)

    def eye_logical_pos(self) -> QPointF:
        return QPointF(
            self.CHAR_ORIGIN_X + self.shift_spring.x + self._eye_center_norm[0] * self.CHAR_DRAW_W,
            self.CHAR_ORIGIN_Y + self._clamped_bob() + self._eye_center_norm[1] * self.CHAR_DRAW_H,
        )

    def _shoulder_world(self, side: str) -> QPointF:
        nx, ny = self._shoulder_l_norm if side == 'left' else self._shoulder_r_norm
        sx = self.CHAR_ORIGIN_X + self.shift_spring.x + nx * self.CHAR_DRAW_W
        sy = self.CHAR_ORIGIN_Y + self._clamped_bob() + ny * self.CHAR_DRAW_H
        ang = self.pet_lean_spring.x
        if abs(ang) < 0.002:
            return QPointF(sx, sy)
        px, py = self.deck.DESK_PIVOT_X, self.deck.DESK_PIVOT_Y
        dx, dy = sx - px, sy - py
        c, s = math.cos(ang), math.sin(ang)
        return QPointF(px + dx * c - dy * s, py + dx * s + dy * c)

    def _right_rest_world(self) -> tuple[float, float]:
        # Natural resting tip of her original-art right cuffed paw right on the upper keyboard edge
        return self.deck.local_to_world_xy(42.0, 12.0)

    def _map_key_to_natural_paw_target(self, pk, side: str) -> tuple[float, float]:
        """Map every key directly onto its REAL keyboard region so her paws genuinely hit each key zone!"""
        if pk.spec.id == 'space':
            lx = 4.0 if side == 'left' else 44.0
            ly = 13.5
            return self.deck.local_to_world_xy(lx, ly)
        lx = max(-38.0, min(90.0, pk.local_center.x()))
        ly = max(12.0, min(40.0, pk.local_center.y() - 1.5))
        return self.deck.local_to_world_xy(lx, ly)

    def _ensure_raw_images(self) -> dict[str, QImage]:
        if self._raw_images is None:
            imgs: dict[str, QImage] = {}
            for name in self.SPRITE_NAMES:
                p = self.asset_dir / f'{name}.png'
                imgs[name] = QImage(str(p)) if p.is_file() else QImage()
            self._raw_images = imgs
        return self._raw_images

    def ensure_sprites(self) -> dict[str, QPixmap]:
        if self._pixmaps is None:
            raw = self._ensure_raw_images()
            self._pixmaps = {
                name: (QPixmap.fromImage(im) if not im.isNull() else QPixmap())
                for name, im in raw.items()
            }
        return self._pixmaps

    def _scaled_sprites(self, pixel_w: int, pixel_h: int) -> dict[str, QPixmap]:
        key = (max(1, pixel_w), max(1, pixel_h))
        if self._scaled_key == key and self._scaled_cache:
            return self._scaled_cache
        raw = self._ensure_raw_images()
        scaled: dict[str, QPixmap] = {}
        for name in (
            'bongo_body_idle',
            'bongo_body_blink',
            'bongo_body_happy',
            'bongo_body_wink',
            'bongo_body_angry',
            'bongo_body_sleepy',
            'bongo_body_starry',
            'bongo_body_smug',
            'bongo_body_flustered',
            'bongo_body_shy',
            'bongo_sclera',
            'bongo_irises',
            'bongo_eye_frame',
            'bongo_collar_front',
        ):
            im = raw.get(name)
            if im is not None and not im.isNull():
                sim = im.scaled(
                    key[0], key[1],
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                scaled[name] = QPixmap.fromImage(sim)
            else:
                scaled[name] = QPixmap()
        self._scaled_key = key
        self._scaled_cache = scaled
        return scaled

    def choose_expression(self, now: float) -> str:
        """Rich KuroCat interactive expression state machine: normal typing/clicking keeps calm idle + cursor gaze!"""
        if self.privacy_mode:
            return 'shy'
        if self.manual_expr and now < self.manual_expr_until:
            return self.manual_expr
        if now < self.angry_until:
            return 'angry'
        if now < self.flustered_until:
            return 'flustered'
        if now < self.starry_until or now < self.celebrate_until or self.mood_tier == 2:
            return 'starry'
        if self.pet_energy > 0.22 or now < self.happy_until:
            return 'happy'
        if now < self.wink_until:
            return 'wink'
        if now < self.smug_until:
            return 'smug'
        if self.sleeping or now < self.blink_until:
            return 'blink'
        if self.drowsy:
            return 'sleepy'
        return 'idle'

    def reset_transient(self, now: float) -> None:
        self.pressed_keys.clear()
        self.vk_owner.clear()
        self.vk_to_key_id.clear()
        self.left_paw.held_vks.clear()
        self.right_paw.held_vks.clear()
        self.mouse_left = self.mouse_right = self.mouse_middle = False
        self.mouse_left_press = self.mouse_right_press = self.mouse_middle_press = 0.0
        mx, my = self._mouse_grip_world()
        rx, ry = self._right_rest_world()
        self.left_paw.x = self.left_paw.tx = mx
        self.left_paw.y = self.left_paw.ty = my
        self.left_paw.mode = 'mouse'
        self.left_paw.lift = 0.0
        self.left_paw.squash = 0.0
        self.right_paw.x = self.right_paw.tx = rx
        self.right_paw.y = self.right_paw.ty = ry
        self.right_paw.mode = 'idle'
        self.right_paw.lift = 0.0
        self.right_paw.squash = 0.0
        self.bubbles.clear()
        self.ripples.clear()
        self.last_input_at = now
        self.mouse_active_at = now
        self.drowsy = False
        self.sleeping = False
        self.pet_energy = 0.0
        self.celebrate_until = 0.0
        self.happy_until = 0.0
        self.wink_until = 0.0
        self.angry_until = 0.0
        self.starry_until = 0.0
        self.smug_until = 0.0
        self.flustered_until = 0.0
        self.manual_expr = None
        self.manual_expr_until = 0.0
        self.click_timestamps.clear()
        self.backspace_timestamps.clear()
        self.pet_dir_x = 0.0
        self.mood_tier = 0
        self.mood_changed_at = now
        self.next_blink_at = now + random.uniform(4.2, 6.8)

    def set_privacy_mode(self, active: bool, now: float) -> None:
        if self.privacy_mode == active:
            return
        self.privacy_mode = active
        if active:
            self.pressed_keys.clear()
            self.vk_owner.clear()
            self.vk_to_key_id.clear()
            self.left_paw.held_vks.clear()
            self.right_paw.held_vks.clear()
            self.bubbles.clear()
            self.ripples.clear()
            self.drowsy = False
            self.sleeping = False
        self.last_input_at = now

    def on_privacy_pulse(self, now: float) -> None:
        self.privacy_pulse_at = now
        self.last_input_at = now
        self.bob_spring.kick(7.0)

    def _wake_up(self, now: float) -> None:
        self.drowsy = False
        self.sleeping = False
        self.last_input_at = now

    def trigger_celebrate(self, now: float, duration: float = 2.6) -> None:
        """Codex completion / milestone -> sparkling starry-eyed excitement!"""
        self.celebrate_until = max(self.celebrate_until, now + duration)
        self.starry_until = max(self.starry_until, now + duration)
        self._wake_up(now)
        self.bob_spring.kick(-15.0)
        self._spawn_particle('spark', random.uniform(44.0, 56.0), random.uniform(62.0, 76.0))
        self._spawn_particle('spark', random.uniform(222.0, 236.0), random.uniform(62.0, 76.0))

    def trigger_happy(self, now: float, duration: float = 2.4) -> None:
        """Big bright laughing smile (Option B)."""
        self.happy_until = max(self.happy_until, now + duration)
        self._wake_up(now)
        self.bob_spring.kick(-12.0)
        self._spawn_particle('spark', random.uniform(44.0, 58.0), random.uniform(64.0, 78.0))
        self._spawn_particle('heart', random.uniform(220.0, 234.0), random.uniform(64.0, 78.0))

    def trigger_wink(self, now: float, duration: float = 1.9) -> None:
        """Playful sweet wink greeting (Option D)."""
        self.wink_until = max(self.wink_until, now + duration)
        self.pet_energy = 0.0
        self.happy_until = 0.0
        self._wake_up(now)
        self.bob_spring.kick(-10.0)
        self._spawn_particle('heart', random.uniform(220.0, 234.0), random.uniform(64.0, 78.0))

    def trigger_angry(self, now: float, duration: float = 2.8) -> None:
        """Cute pouty angry Bailong (💢)."""
        self.angry_until = max(self.angry_until, now + duration)
        self.happy_until = 0.0
        self.wink_until = 0.0
        self.pet_energy = 0.0
        self._wake_up(now)
        self.bob_spring.kick(14.0)
        self.pet_lean_spring.kick(random.choice((-0.45, 0.45)))

    def trigger_smug(self, now: float, duration: float = 2.2) -> None:
        """Proud '哼哼' smug smile after finishing a typing burst."""
        self.smug_until = max(self.smug_until, now + duration)
        self._wake_up(now)
        self.bob_spring.kick(-8.0)

    def trigger_flustered(self, now: float, duration: float = 2.4) -> None:
        """Teary flustered Bailong (💧) when shaken or spamming Backspace."""
        self.flustered_until = max(self.flustered_until, now + duration)
        self._wake_up(now)
        self.bob_spring.kick(12.0)
        self.pet_lean_spring.kick(random.choice((-0.55, 0.55)))

    def trigger_manual_expression(self, expr: str | None, now: float, duration: float = 6.0) -> None:
        """Let the user preview/hold any interactive expression from the right-click menu."""
        if not expr or expr == 'auto':
            self.manual_expr = None
            self.manual_expr_until = 0.0
            self.angry_until = self.flustered_until = self.starry_until = 0.0
            self.happy_until = self.wink_until = self.smug_until = self.celebrate_until = 0.0
            self._wake_up(now)
            return
        if expr in self.EXPRESSION_NAMES:
            self.manual_expr = expr
            self.manual_expr_until = now + duration
            self._wake_up(now)
            if expr in ('happy', 'starry'):
                self._spawn_particle('spark', 50.0, 68.0)
                self._spawn_particle('spark', 228.0, 68.0)
            elif expr == 'wink':
                self._spawn_particle('heart', 226.0, 68.0)
            elif expr == 'sleepy':
                self._spawn_particle('zzz', 214.0, 58.0)
            self.bob_spring.kick(-10.0)

    def on_pet_click(self, now: float) -> str:
        """Single click pokes Bailong -> random playful expression (flustered, wink, smug, happy, starry) without immediate repeats!"""
        pool = ('flustered', 'wink', 'smug', 'happy', 'starry')
        last = getattr(self, '_last_click_expr', None)
        choices = [e for e in pool if e != last] or list(pool)
        chosen = random.choice(choices)
        self._last_click_expr = chosen
        # Clear previous transient expression timers so every click immediately shows the newly rolled reaction.
        self.angry_until = self.flustered_until = self.starry_until = 0.0
        self.happy_until = self.wink_until = self.smug_until = 0.0
        self.pet_energy = 0.0
        if chosen == 'flustered':
            self.trigger_flustered(now, 2.4)
        elif chosen == 'wink':
            self.trigger_wink(now, 2.1)
        elif chosen == 'smug':
            self.trigger_smug(now, 2.2)
        elif chosen == 'happy':
            self.trigger_happy(now, 2.2)
        else:
            self.trigger_starry(now, 2.2)
        return chosen

    def on_head_stroke(self, dx: float, dy: float, now: float) -> bool:
        dist = math.hypot(dx, dy)
        if dist < 1.5:
            return False
        self._wake_up(now)
        prev = self.pet_energy
        self.pet_energy = min(1.0, self.pet_energy + dist * 0.010)
        # Sustained gentle head-petting soothes her back to happy without 2px post-click jitter cancelling reactions
        if self.pet_energy > 0.35:
            self.angry_until = 0.0
            self.flustered_until = 0.0
        self.pet_dir_x = max(-1.0, min(1.0, self.pet_dir_x * 0.82 + (dx / max(4.0, dist)) * 0.35))
        if self.pet_energy > 0.28 and random.random() < 0.12:
            hx = random.uniform(44.0, 56.0) if random.random() < 0.5 else random.uniform(222.0, 236.0)
            self._spawn_particle('spark' if random.random() < 0.5 else 'heart', hx, random.uniform(62.0, 78.0))
        return self.pet_energy > 0.22 or prev <= 0.22

    def on_cursor_vector(self, dx_px: float, dy_px: float, now: float) -> None:
        self.cursor_dx = dx_px
        self.cursor_dy = dy_px
        self.has_cursor_vec = True
        if not self.sleeping and not self.drowsy:
            self.last_input_at = max(self.last_input_at, now)

    def on_key_down(self, vk: int, now: float, repeat: bool = False, synthetic: bool = False) -> None:
        if self.privacy_mode:
            if not repeat:
                self.on_privacy_pulse(now)
            return

        if not synthetic:
            self.drowsy = False
            self.sleeping = False
            self.last_input_at = now
            if vk in (0x08, 0x2E):  # Backspace / Delete spam -> cute pouty angry Bailong (💢)!
                self.backspace_timestamps = [t for t in self.backspace_timestamps if now - t < 2.2]
                self.backspace_timestamps.append(now)
                if len(self.backspace_timestamps) >= 7:
                    self.backspace_timestamps.clear()
                    self.trigger_angry(now, 2.6)

        pk = self.deck.resolve_vk(vk)
        if pk is None:
            return

        self.pressed_keys[pk.spec.id] = 1.0
        if repeat:
            return

        self.last_key_at = now
        self.key_timestamps.append(now)
        if len(self.key_timestamps) > 48:
            del self.key_timestamps[:-48]

        cx, cy = pk.center.x(), pk.center.y()
        self.last_key_x = cx
        self.bob_spring.kick(9.0)

        mouse_btn_held = self.mouse_left or self.mouse_right
        if mouse_btn_held:
            side = 'right'
        elif pk.spec.id == 'space':
            side = 'left' if self._last_tap_side == 'right' else 'right'
        elif pk.local_center.x() < self.deck.KB_LOCAL_CX - 2.0:
            side = 'left'
        else:
            side = 'right'

        paw = self.left_paw if side == 'left' else self.right_paw
        self.vk_owner[vk] = side
        self.vk_to_key_id[vk] = pk.spec.id
        self._last_tap_side = side
        if vk in paw.held_vks:
            paw.held_vks.remove(vk)
        paw.held_vks.append(vk)

        tx, ty = self._map_key_to_natural_paw_target(pk, side)
        paw.mode = 'poke'
        paw.tx = tx
        paw.ty = ty
        paw.poke_time = 0.0
        paw.last_active_at = now
        paw.squash = 1.0
        paw.lift = 0.85  # crisp initial lift-then-slap arc!

        if pk.spec.id in TAIL_KEY_IDS:
            self.ripples.append(KeyRipple(cx, cy, r=3.5, life=1.0))

        if self.show_key_bubbles and not synthetic and pk.spec.bubble:
            if len(self.bubbles) >= 9:
                self.bubbles.pop(0)
            self.bubbles.append(
                KeyBubble(
                    label=pk.spec.bubble,
                    is_space=(pk.spec.id == 'space'),
                    accent_color=None,
                    x=cx + random.uniform(-3.0, 3.0),
                    y=cy - 19.0,
                    vx=random.uniform(-7.0, 7.0),
                    vy=-48.0 - random.uniform(0.0, 12.0),
                    rot=random.uniform(-5.0, 5.0),
                    born=now,
                )
            )

    def on_key_up(self, vk: int, now: float) -> None:
        pk = self.deck.resolve_vk(vk)
        if pk is not None:
            self.pressed_keys.pop(pk.spec.id, None)
        self.vk_to_key_id.pop(vk, None)
        owner = self.vk_owner.pop(vk, None)
        for paw in (self.left_paw, self.right_paw):
            if vk in paw.held_vks:
                paw.held_vks.remove(vk)
        if owner == 'left' and self.left_paw.held_vks:
            self._retarget_to_latest_held(self.left_paw)
        elif owner == 'right' and self.right_paw.held_vks:
            self._retarget_to_latest_held(self.right_paw)

    def _retarget_to_latest_held(self, paw: PawState) -> None:
        while paw.held_vks:
            vk = paw.held_vks[-1]
            kid = self.vk_to_key_id.get(vk)
            pk = self.deck.keys.get(kid) if kid else None
            if pk is not None:
                paw.tx, paw.ty = self._map_key_to_natural_paw_target(pk, paw.side)
                return
            paw.held_vks.pop()

    def on_mouse_move(self, nx: float, ny: float, now: float) -> None:
        self.mouse_nx = max(0.0, min(1.0, nx))
        self.mouse_ny = max(0.0, min(1.0, ny))
        self.mouse_active_at = now
        if not self.left_paw.held_vks:
            self.left_paw.mode = 'mouse'
        if not self.sleeping:
            self.last_input_at = now

    def on_mouse_button(self, button: str, down: bool, now: float) -> None:
        if down:
            self.sleeping = False
        self.last_input_at = now
        self.mouse_active_at = now
        if button == 'left':
            self.mouse_left = down
            if down:
                self.mouse_left_press = 1.0
        elif button == 'right':
            self.mouse_right = down
            if down:
                self.mouse_right_press = 1.0
        else:
            self.mouse_middle = down
            if down:
                self.mouse_middle_press = 1.0

        if down:
            self.left_paw.mode = 'mouse'
            self.left_paw.squash = 1.0
            self.left_paw.lift = 0.65
            self.left_paw.last_active_at = now
            self.bob_spring.kick(7.5)
            if self.show_key_bubbles and not self.privacy_mode:
                mx, my = self._mouse_world_pos()
                lbl = 'LMB' if button == 'left' else ('RMB' if button == 'right' else 'MMB')
                col = '#c6b4fa' if button == 'left' else ('#f4a6c2' if button == 'right' else '#dfd7f2')
                if len(self.bubbles) >= 9:
                    self.bubbles.pop(0)
                self.bubbles.append(
                    KeyBubble(
                        label=lbl,
                        is_space=False,
                        accent_color=col,
                        x=mx + (4.0 if button == 'left' else -4.0),
                        y=my - 18.0,
                        vx=random.uniform(-3.0, 3.0),
                        vy=-38.0,
                        rot=0.0,
                        born=now,
                        ttl=0.70,
                    )
                )

    def on_mouse_wheel(self, delta: int, now: float) -> None:
        self.last_input_at = now
        self.mouse_active_at = now
        self.wheel_pulse = 1.0 if delta > 0 else -1.0

    def is_animating_fast(self, now: float) -> bool:
        if self.pressed_keys or self.mouse_left or self.mouse_right or self.mouse_middle:
            return True
        if self.left_paw.mode in ('poke', 'curled') or self.right_paw.mode in ('poke', 'curled'):
            return True
        if self.left_paw.lift > 0.03 or self.right_paw.lift > 0.03:
            return True
        if self.bubbles or self.ripples or self.particles:
            return True
        if (
            now < self.celebrate_until
            or now < self.happy_until
            or now < self.wink_until
            or now < self.angry_until
            or now < self.starry_until
            or now < self.smug_until
            or now < self.flustered_until
            or now < self.manual_expr_until
            or self.pet_energy > 0.05
        ):
            return True
        if now - self.last_input_at < 2.2:
            return True
        if abs(self.shift_spring.v) > 0.25 or abs(self.bob_spring.v) > 0.25 or abs(self.eye_x_spring.v) > 0.2:
            return True
        return False

    def _update_paw(self, paw: PawState, dt: float, now: float) -> None:
        has_held = bool(paw.held_vks)
        if has_held:
            self._retarget_to_latest_held(paw)
            paw.mode = 'poke'
            paw.poke_time = min(paw.poke_time + dt, 0.10)
            paw.last_active_at = now
        elif paw.mode == 'poke':
            paw.poke_time += dt
            if paw.poke_time > 0.14:
                if paw.side == 'left' and (
                    self.mouse_left or self.mouse_right or (now - self.mouse_active_at < 0.85 and len(self.key_timestamps) < 3)
                ):
                    paw.mode = 'mouse'
                elif now - self.last_key_at < 0.65 or self.pressed_keys:
                    paw.mode = 'curled'
                else:
                    paw.mode = 'mouse' if paw.side == 'left' else 'idle'
        elif paw.mode == 'curled':
            if now - self.last_key_at > 0.68 and not self.pressed_keys:
                paw.mode = 'mouse' if paw.side == 'left' else 'idle'
        elif paw.side == 'left' and (self.mouse_left or self.mouse_right or now - self.mouse_active_at < 2.4):
            paw.mode = 'mouse'

        if paw.mode == 'mouse' and paw.side == 'left':
            tx, ty = self._mouse_grip_world()
            want_lift = 0.0
        elif paw.mode == 'poke':
            tx, ty = paw.tx, paw.ty
            want_lift = 0.0
        elif paw.mode == 'curled':
            if paw.side == 'left':
                tx, ty = self.deck.local_to_world_xy(-14.0, 9.0)
            else:
                tx, ty = self._right_rest_world()
            want_lift = 0.72
        else:
            if paw.side == 'left':
                tx, ty = self._mouse_grip_world()
            else:
                tx, ty = self._right_rest_world()
            want_lift = 0.0

        follow_rate = 38.0 if paw.mode == 'poke' else (30.0 if paw.mode == 'mouse' else 20.0)
        follow = 1.0 - math.exp(-follow_rate * dt)
        paw.x += (tx - paw.x) * follow
        paw.y += (ty - paw.y) * follow
        paw.lift += (want_lift - paw.lift) * (1.0 - math.exp(-24.0 * dt))
        paw.squash = max(0.0, paw.squash * math.exp(-12.0 * dt))

    def step(self, now: float, dt: float) -> None:
        dt = min(0.05, max(0.001, dt))

        if not self.privacy_mode and (now < self.celebrate_until or now < self.starry_until or now < self.angry_until or now < self.flustered_until):
            self.drowsy = False
            self.sleeping = False
        elif not self.privacy_mode and not self.pressed_keys:
            idle_for = now - self.last_input_at
            if idle_for > 95.0:
                if not self.sleeping:
                    self.sleeping = True
                    self.drowsy = False
                    self._next_zzz_at = now + 0.4
            elif idle_for > 38.0:
                if not self.drowsy and not self.sleeping:
                    self.drowsy = True
                    self._next_zzz_at = now + 0.5
            else:
                self.drowsy = False
                self.sleeping = False

        if (self.sleeping or self.drowsy) and now >= self._next_zzz_at:
            self._next_zzz_at = now + (2.1 if self.sleeping else 2.6)
            self._spawn_particle('zzz', 210.0, 56.0)

        cutoff = now - 2.5
        while self.key_timestamps and self.key_timestamps[0] < cutoff:
            self.key_timestamps.pop(0)
        kps = len(self.key_timestamps) / 2.5
        instant_heat = min(1.0, kps / 7.5)
        rate = 4.0 if instant_heat > self.typing_heat else 1.4
        self.typing_heat += (instant_heat - self.typing_heat) * (1.0 - math.exp(-rate * dt))

        dwell = now - self.mood_changed_at
        if self.mood_tier == 0 and self.typing_heat > 0.45 and dwell > 0.8:
            self.mood_tier = 1
            self.mood_changed_at = now
        elif self.mood_tier == 1:
            if self.typing_heat > 0.80 and dwell > 1.5:
                self.mood_tier = 2
                self.mood_changed_at = now
            elif self.typing_heat < 0.20 and dwell > 1.8:
                self.mood_tier = 0
                self.mood_changed_at = now
        elif self.mood_tier == 2 and self.typing_heat < 0.50 and dwell > 2.2:
            self.mood_tier = 1
            self.mood_changed_at = now

        if self.mood_tier == 2 and not self.privacy_mode and random.random() < 0.03:
            hx = random.uniform(44.0, 56.0) if random.random() < 0.5 else random.uniform(222.0, 236.0)
            self._spawn_particle('heart', hx, random.uniform(62.0, 78.0))

        self.pet_energy *= math.exp(-1.35 * dt)
        if self.pet_energy < 0.05:
            self.pet_dir_x *= math.exp(-4.0 * dt)
        self.wheel_pulse *= math.exp(-9.0 * dt)
        if not self.mouse_left:
            self.mouse_left_press *= math.exp(-14.0 * dt)
        if not self.mouse_right:
            self.mouse_right_press *= math.exp(-14.0 * dt)
        if not self.mouse_middle:
            self.mouse_middle_press *= math.exp(-14.0 * dt)

        if now >= self.next_blink_at:
            self.blink_until = now + 0.135
            self.next_blink_at = now + random.uniform(4.5, 7.2)

        if self.privacy_mode:
            shift_target = 0.0
            bob_target = 0.5 if (now - self.privacy_pulse_at < 0.16) else 0.0
            lean_target = 0.0
        elif self.sleeping:
            shift_target = 0.0
            bob_target = 1.2 + math.sin(now * 1.6) * 0.5
            lean_target = 0.0
        else:
            if now - self.last_key_at < 0.75:
                norm_k = max(-1.0, min(1.0, (self.last_key_x - self.deck.KB_CX) / 62.0))
                shift_target = norm_k * 11.5
                reach_lean = norm_k * 0.064
            else:
                shift_target = (self.mouse_nx - 0.5) * 5.0
                reach_lean = (self.mouse_nx - 0.5) * 0.022
            bob_target = math.sin(now * 2.1) * 0.45
            lean_target = self.pet_dir_x * 0.028 if self.pet_energy > 0.22 else reach_lean

        self.shift_spring.step(shift_target, dt)
        self.bob_spring.step(bob_target, dt)
        self.pet_lean_spring.step(lean_target, dt)

        self._update_paw(self.left_paw, dt, now)
        self._update_paw(self.right_paw, dt, now)

        # Real Global Desktop Mouse Cursor Eye Tracking
        gaze_tx, gaze_ty = 0.0, 0.0
        if self.choose_expression(now) == 'idle':
            if self.has_cursor_vec:
                dist = math.hypot(self.cursor_dx, self.cursor_dy)
                if dist > 4.0:
                    ux, uy = self.cursor_dx / dist, self.cursor_dy / dist
                    strength = min(1.0, dist / 280.0)
                    gaze_tx = ux * strength * 5.8
                    gaze_ty = uy * strength * 3.8
            else:
                gaze_tx = (self.mouse_nx - 0.5) * 2.0 * 5.5
                gaze_ty = (self.mouse_ny - 0.5) * 2.0 * 3.6

        self.eye_x_spring.step(gaze_tx, dt)
        self.eye_y_spring.step(gaze_ty, dt)

        self.bubbles = [b for b in self.bubbles if (now - b.born) < b.ttl]
        for b in self.bubbles:
            b.x += b.vx * dt
            b.y += b.vy * dt
            b.vy *= math.exp(-3.2 * dt)

        for rp in self.ripples:
            rp.r += 38.0 * dt
            rp.life -= 2.5 * dt
        self.ripples = [rp for rp in self.ripples if rp.life > 0.0]

        for pt in self.particles:
            pt.x += pt.vx * dt
            pt.y += pt.vy * dt
            pt.life -= 1.05 * dt
        self.particles = [pt for pt in self.particles if pt.life > 0.0]

    def _mouse_local_pos(self) -> tuple[float, float]:
        mx = self.deck.MOUSE_LOCAL_X + (self.mouse_nx - 0.5) * 18.0
        my = self.deck.MOUSE_LOCAL_Y + (self.mouse_ny - 0.5) * 12.0
        return mx, my

    def _mouse_world_pos(self) -> tuple[float, float]:
        lx, ly = self._mouse_local_pos()
        return self.deck.local_to_world_xy(lx, ly)

    def _mouse_grip_world(self) -> tuple[float, float]:
        lx, ly = self._mouse_local_pos()
        lr_bias = self.mouse_left_press - self.mouse_right_press
        any_click = max(self.mouse_left_press, self.mouse_right_press, self.mouse_middle_press)
        gx = lx + 2.5 + lr_bias * 4.8
        gy = ly - 5.0 + any_click * 4.6
        return self.deck.local_to_world_xy(gx, gy)

    def _clamped_bob(self) -> float:
        return max(-1.8, min(1.8, self.bob_spring.x))

    def _spawn_particle(self, kind: str, x: float, y: float) -> None:
        if len(self.particles) >= 10:
            return
        self.particles.append(
            FxParticle(
                kind=kind,
                x=x,
                y=y,
                vx=random.uniform(-6.0, 6.0) if kind != 'zzz' else 6.0,
                vy=-20.0 - random.uniform(0.0, 9.0),
                life=1.0,
            )
        )

    def hit_test(self, x: float, y: float, widget_w: float, widget_h: float) -> bool:
        if widget_w <= 0 or widget_h <= 0:
            return False
        lx = x * (COMPANION_W / widget_w)
        ly = y * (COMPANION_H / widget_h)
        dl = self.deck.world_to_local(QPointF(lx, ly))
        if QRectF(-100.0, -3.0, 220.0, 66.0).contains(dl):
            return True
        dx = (lx - 140.0) / 100.0
        dy = (ly - 96.0) / 86.0
        return (dx * dx + dy * dy) <= 1.0

    def is_head_point(self, x: float, y: float, widget_w: float, widget_h: float) -> bool:
        if widget_w <= 0 or widget_h <= 0:
            return False
        lx = x * (COMPANION_W / widget_w)
        ly = y * (COMPANION_H / widget_h)
        eye = self.eye_logical_pos()
        return math.hypot(lx - eye.x(), ly - (eye.y() - 10.0)) <= 60.0

    def paint(self, painter: QPainter, target_rect: QRectF, dpr: float, now: float) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        sx = target_rect.width() / COMPANION_W
        sy = target_rect.height() / COMPANION_H
        painter.translate(target_rect.left(), target_rect.top())
        painter.scale(sx, sy)

        body_pw = max(1, round(self.CHAR_DRAW_W * sx * dpr))
        body_ph = max(1, round(self.CHAR_DRAW_H * sy * dpr))
        scaled = self._scaled_sprites(body_pw, body_ph)
        expr = self.choose_expression(now)

        # Layer 1: Slim compact Q-version Bailong upper body behind the +9.65° BongoCat desk
        self._draw_bongo_body(painter, scaled, expr)

        # Layer 2: +9.65° BongoCat-tilted desk mat + live 180° 3D mechanical keyboard + live 3D downward mouse
        pw = max(1, round(target_rect.width() * dpr))
        ph = max(1, round(target_rect.height() * dpr))
        deck_pm = self.deck.static_deck_pixmap(pw, ph)
        painter.drawPixmap(QRectF(0.0, 0.0, COMPANION_W, COMPANION_H), deck_pm, QRectF(deck_pm.rect()))

        painter.save()
        painter.translate(self.deck.DESK_PIVOT_X, self.deck.DESK_PIVOT_Y)
        painter.rotate(self.deck.TILT_DEG)
        if not self.privacy_mode:
            for key_id, amount in self.pressed_keys.items():
                pk = self.deck.keys.get(key_id)
                if pk is not None:
                    self.deck._paint_keycap(
                        painter,
                        pk,
                        pressed=amount,
                        font_alpha=self._font_key_alpha,
                        font_mod=self._font_key_mod,
                    )
        else:
            self._draw_privacy_shield_local(painter)

        self._draw_3d_downward_mouse_local(painter)
        painter.restore()

        for rp in self.ripples:
            painter.save()
            painter.setPen(QPen(QColor(156, 128, 224, int(205 * max(0.0, rp.life))), 1.7))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(rp.x, rp.y), rp.r, rp.r * 0.56)
            painter.restore()

        # Layer 3: ONE single unbroken Live2D arm per side from real shoulder socket -> over desk -> into buttoned cuff!
        if expr != 'shy':
            self._draw_original_paw(painter, self._shoulder_world('left'), self.left_paw)
            self._draw_original_paw(painter, self._shoulder_world('right'), self.right_paw)
            # Layer 4: Live2D front overlay (lower chin, side-hair & center white halter bodice) covering shoulder sockets!
            self._draw_collar_front_overlay(painter, scaled)

        # Layer 5: Crisp 3D floating keycap / LMB / RMB bubbles + cel-shaded hearts / Zzz
        self._draw_key_bubbles(painter, now)
        self._draw_particles(painter)

        painter.restore()

    def _draw_bongo_body(self, painter: QPainter, scaled: dict[str, QPixmap], expr: str) -> None:
        bob = self._clamped_bob()
        lean_deg = math.degrees(self.pet_lean_spring.x)

        painter.save()
        if abs(lean_deg) >= 0.12:
            painter.translate(self.deck.DESK_PIVOT_X, self.deck.DESK_PIVOT_Y)
            painter.rotate(lean_deg)
            painter.translate(-self.deck.DESK_PIVOT_X, -self.deck.DESK_PIVOT_Y)

        dest = QRectF(
            self.CHAR_ORIGIN_X + self.shift_spring.x,
            self.CHAR_ORIGIN_Y + bob,
            self.CHAR_DRAW_W,
            self.CHAR_DRAW_H,
        )

        gx = self.eye_x_spring.x
        gy = self.eye_y_spring.x
        gaze_mag = math.hypot(gx, gy)
        has_live_layers = (
            not scaled.get('bongo_sclera', QPixmap()).isNull()
            and not scaled.get('bongo_irises', QPixmap()).isNull()
            and not scaled.get('bongo_eye_frame', QPixmap()).isNull()
        )

        if expr == 'idle' and has_live_layers and gaze_mag >= 0.18:
            pm_sclera = scaled['bongo_sclera']
            pm_iris = scaled['bongo_irises']
            pm_frame = scaled['bongo_eye_frame']
            painter.drawPixmap(dest, pm_sclera, QRectF(pm_sclera.rect()))
            shift_scale = self.CHAR_DRAW_W / self._body_size
            painter.drawPixmap(dest.translated(gx * shift_scale, gy * shift_scale), pm_iris, QRectF(pm_iris.rect()))
            painter.drawPixmap(dest, pm_frame, QRectF(pm_frame.rect()))
        else:
            pm = scaled.get(f'bongo_body_{expr}') or scaled.get('bongo_body_idle')
            if pm is not None and not pm.isNull():
                painter.drawPixmap(dest, pm, QRectF(pm.rect()))
        painter.restore()

    def _draw_collar_front_overlay(self, painter: QPainter, scaled: dict[str, QPixmap]) -> None:
        """Draw lower chin, front side-hair locks & center white halter bodice ON TOP of shoulder sockets (Live2D Layer 4)."""
        pm = scaled.get('bongo_collar_front')
        if pm is None or pm.isNull():
            return
        bob = self._clamped_bob()
        lean_deg = math.degrees(self.pet_lean_spring.x)
        painter.save()
        if abs(lean_deg) >= 0.12:
            painter.translate(self.deck.DESK_PIVOT_X, self.deck.DESK_PIVOT_Y)
            painter.rotate(lean_deg)
            painter.translate(-self.deck.DESK_PIVOT_X, -self.deck.DESK_PIVOT_Y)
        dest = QRectF(
            self.CHAR_ORIGIN_X + self.shift_spring.x,
            self.CHAR_ORIGIN_Y + bob,
            self.CHAR_DRAW_W,
            self.CHAR_DRAW_H,
        )
        painter.drawPixmap(dest, pm, QRectF(pm.rect()))
        painter.restore()

    def _draw_chibi_arm(
        self,
        painter: QPainter,
        sh: QPointF,
        ctrl: QPointF,
        cuff_join: QPointF,
        lift_px: float,
    ) -> None:
        """Draw ONE unbroken canon warm-cream chibi arm from shoulder socket (behind chin/hair) -> IN FRONT OF desk -> cuff."""
        dx = cuff_join.x() - sh.x()
        dy = cuff_join.y() - sh.y()
        dist = math.hypot(dx, dy)
        if dist <= 3.0:
            return

        def _norm(ax: float, ay: float) -> tuple[float, float]:
            l = max(1e-4, math.hypot(ax, ay))
            return -ay / l, ax / l

        nmx, nmy = _norm(dx, dy)
        n0x, n0y = _norm(ctrl.x() - sh.x(), ctrl.y() - sh.y())
        n2x, n2y = _norm(cuff_join.x() - ctrl.x(), cuff_join.y() - ctrl.y())
        # Prevent normal flip / bowtie pinch on short arms
        if n0x * nmx + n0y * nmy < 0.0:
            n0x, n0y = -n0x, -n0y
        if n2x * nmx + n2y * nmy < 0.0:
            n2x, n2y = -n2x, -n2y

        # Tuck start 5px inside shoulder socket (covered seamlessly by Layer 4 chin/hair/bodice overlay!)
        u0x, u0y = dx / dist, dy / dist
        sh_in = QPointF(sh.x() - u0x * 5.0, sh.y() - u0y * 5.0)

        # Plump canon warm-cream chibi arm proportions (6.8px shoulder -> 6.2px elbow -> 5.3px wrist inside 17px buttoned cuff)
        w0, wm, w2 = 6.8, 6.2, 5.3
        s_a = QPointF(sh_in.x() + n0x * w0, sh_in.y() + n0y * w0)
        m_a = QPointF(ctrl.x() + nmx * wm, ctrl.y() + nmy * wm)
        e_a = QPointF(cuff_join.x() + n2x * w2, cuff_join.y() + n2y * w2)

        s_b = QPointF(sh_in.x() - n0x * w0, sh_in.y() - n0y * w0)
        m_b = QPointF(ctrl.x() - nmx * wm, ctrl.y() - nmy * wm)
        e_b = QPointF(cuff_join.x() - n2x * w2, cuff_join.y() - n2y * w2)

        fill_path = QPainterPath()
        fill_path.moveTo(s_a)
        fill_path.quadTo(m_a, e_a)
        fill_path.lineTo(e_b)
        fill_path.quadTo(m_b, s_b)
        fill_path.closeSubpath()

        edge_a = QPainterPath()
        edge_a.moveTo(s_a)
        edge_a.quadTo(m_a, e_a)
        edge_b = QPainterPath()
        edge_b.moveTo(s_b)
        edge_b.quadTo(m_b, e_b)

        # Cast soft shadow ONLY on the desk surface (never on her dress!)
        desk_clip = QPainterPath()
        desk_clip.moveTo(self.deck.local_to_world(QPointF(-130.0, -0.5)))
        desk_clip.lineTo(self.deck.local_to_world(QPointF(145.0, -0.5)))
        desk_clip.lineTo(self.deck.local_to_world(QPointF(145.0, 95.0)))
        desk_clip.lineTo(self.deck.local_to_world(QPointF(-130.0, 95.0)))
        desk_clip.closeSubpath()
        painter.save()
        painter.setClipPath(desk_clip)
        shadow_path = fill_path.translated(0.0, 2.6 + lift_px * 0.36)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(46, 34, 78, int(34 * (1.0 - min(0.5, lift_px / 18.0)))))
        painter.drawPath(shadow_path)
        painter.restore()

        # Paint the unbroken arm completely UNCLIPPED IN FRONT OF the tilted desk border!
        painter.save()
        grad = QLinearGradient(m_a, m_b)
        grad.setColorAt(0.0, QColor('#fff6ee'))
        grad.setColorAt(0.58, QColor('#fcece2'))
        grad.setColorAt(1.0, QColor('#eed6d4'))
        painter.setBrush(grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(fill_path)

        # Soft cuff rim shadow where her white buttoned cuff wraps around the wrist
        cuff_shadow = QRadialGradient(cuff_join, 7.2)
        cuff_shadow.setColorAt(0.0, QColor(214, 178, 186, 125))
        cuff_shadow.setColorAt(1.0, QColor(214, 178, 186, 0))
        painter.setBrush(cuff_shadow)
        painter.drawPath(fill_path)

        pen = QPen(QColor(LINEART_HEX), 1.85, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(edge_a)
        painter.drawPath(edge_b)
        painter.restore()

    def _draw_original_paw(self, painter: QPainter, sh: QPointF, paw: PawState) -> None:
        """Draw ONE unbroken Live2D arm from her real shoulder socket -> over desk edge -> unstretched original-art buttoned cuff & paw."""
        sprites = self.ensure_sprites()
        pm = sprites.get('bongo_paw_l' if paw.side == 'left' else 'bongo_paw_r')
        if pm is None or pm.isNull():
            return
        meta = self._paw_meta[paw.side]
        piv = meta['pivot']
        tip = meta['tip']
        draw_scale = self.CHAR_DRAW_W / self._body_size

        rest_dx = (tip[0] - piv[0]) * draw_scale
        rest_dy = (tip[1] - piv[1]) * draw_scale
        rest_len = max(1.0, math.hypot(rest_dx, rest_dy))
        rest_ang = math.atan2(rest_dy, rest_dx)

        # Lift-and-slap perpendicular offset off the tilted desk
        rad = math.radians(DESK_TILT_DEG)
        up_nx, up_ny = math.sin(rad), -math.cos(rad)
        lift_px = paw.lift * 9.5
        tx = paw.x + up_nx * lift_px
        ty = paw.y + up_ny * lift_px

        # True shoulder socket tucked behind her cheek & white halter dress (covered by Layer 4 overlay!)
        sh_loc = self.deck.world_to_local(sh)
        t_loc = self.deck.world_to_local(QPointF(tx, ty))
        reach_dist = math.hypot(tx - sh.x(), ty - sh.y())

        # Organic outward chibi elbow curve flowing cleanly over the tilted desk edge
        out_dir = -1.0 if paw.side == 'left' else 1.0
        elbow_lx = sh_loc.x() * 0.45 + t_loc.x() * 0.55 + out_dir * min(9.5, reach_dist * 0.16)
        elbow_ly = sh_loc.y() * 0.35 + t_loc.y() * 0.65 - 1.0 - lift_px * 0.22
        ctrl = self.deck.local_to_world(QPointF(elbow_lx, elbow_ly))

        # Align buttoned cuff & paw seamlessly to forearm end-tangent (zero wrist kink!)
        tan_dx = tx - ctrl.x()
        tan_dy = ty - ctrl.y()
        if math.hypot(tan_dx, tan_dy) < 4.0:
            tan_dx, tan_dy = tx - sh.x(), ty - sh.y()
        want_ang = math.atan2(tan_dy, tan_dx)

        delta_deg = math.degrees(want_ang - rest_ang)
        while delta_deg > 180.0:
            delta_deg -= 360.0
        while delta_deg < -180.0:
            delta_deg += 360.0
        rot_deg = max(-68.0, min(68.0, delta_deg))

        final_ang = rest_ang + math.radians(rot_deg)
        cuff_x = tx - math.cos(final_ang) * rest_len
        cuff_y = ty - math.sin(final_ang) * rest_len

        hx = cuff_x + math.cos(final_ang) * rest_len
        hy = cuff_y + math.sin(final_ang) * rest_len

        # Layer 3a: Unbroken plump canon warm-cream chibi arm from shoulder socket into buttoned cuff
        cuff_join = QPointF(cuff_x + math.cos(final_ang) * 3.0, cuff_y + math.sin(final_ang) * 3.0)
        self._draw_chibi_arm(painter, sh, ctrl, cuff_join, lift_px)

        # Layer 3b: Soft contact shadow directly below paw tip on keycap/mouse
        painter.save()
        shadow_alpha = int(44 * (1.0 - paw.lift * 0.55))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(42, 30, 68, shadow_alpha))
        painter.drawEllipse(QPointF(hx - up_nx * lift_px * 0.85, hy - up_ny * lift_px * 0.85 + 2.4), 8.2 * (1.0 - paw.lift * 0.16), 3.6)

        # Layer 3c: 100% original-art unstretched buttoned cuff + 3-fingered paw ON TOP!
        painter.translate(cuff_x, cuff_y)
        painter.rotate(rot_deg)
        sx_scale = draw_scale * (1.0 + paw.squash * 0.06)
        sy_scale = draw_scale * (1.0 - paw.squash * 0.04)
        painter.scale(sx_scale, sy_scale)
        painter.drawPixmap(QPointF(-piv[0], -piv[1]), pm)
        painter.restore()

    def _draw_3d_downward_mouse_local(self, painter: QPainter) -> None:
        mx, my = self._mouse_local_pos()
        rx, ry = 13.5, 16.8
        l_press = self.mouse_left_press
        r_press = self.mouse_right_press
        m_press = max(self.mouse_middle_press, abs(self.wheel_pulse))
        line_col = QColor(LINEART_HEX)

        painter.save()
        painter.translate(mx, my)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(34, 24, 58, 52))
        painter.drawEllipse(QPointF(0.6, 3.6), rx + 0.3, ry)

        rim_grad = QLinearGradient(-rx, -ry * 0.5, rx, ry * 0.5)
        rim_grad.setColorAt(0.0, QColor('#b7aad4'))
        rim_grad.setColorAt(0.55, QColor('#a495c7'))
        rim_grad.setColorAt(1.0, QColor('#8c7cb4'))
        painter.setBrush(rim_grad)
        painter.setPen(QPen(line_col, 1.8))
        painter.drawEllipse(QPointF(0.0, 2.3), rx, ry)

        shell_path = QPainterPath()
        shell_path.addEllipse(QPointF(0.0, 0.0), rx, ry)

        dome_grad = QRadialGradient(QPointF(-3.5, -7.0), ry * 1.3)
        dome_grad.setColorAt(0.0, QColor('#ffffff'))
        dome_grad.setColorAt(0.58, QColor('#f6f2fc'))
        dome_grad.setColorAt(1.0, QColor('#e1d7f3'))
        painter.fillPath(shell_path, dome_grad)

        painter.save()
        painter.setClipPath(shell_path)

        if not self.privacy_mode and l_press > 0.06:
            l_plate = QPainterPath()
            l_plate.moveTo(0.0, 1.0)
            l_plate.quadTo(rx * 0.52, 0.8, rx + 2.0, -0.3)
            l_plate.lineTo(rx + 2.0, ry + 2.0)
            l_plate.lineTo(0.0, ry + 2.0)
            l_plate.closeSubpath()
            painter.fillPath(l_plate, QColor(174, 148, 238, int(230 * min(1.0, l_press + 0.25))))

        if not self.privacy_mode and r_press > 0.06:
            r_plate = QPainterPath()
            r_plate.moveTo(-rx - 2.0, -0.3)
            r_plate.quadTo(-rx * 0.52, 0.8, 0.0, 1.0)
            r_plate.lineTo(0.0, ry + 2.0)
            r_plate.lineTo(-rx - 2.0, ry + 2.0)
            r_plate.closeSubpath()
            painter.fillPath(r_plate, QColor(238, 146, 180, int(230 * min(1.0, r_press + 0.25))))

        seam = QPainterPath()
        seam.moveTo(-rx - 1.0, -0.3)
        seam.quadTo(0.0, 1.7, rx + 1.0, -0.3)
        painter.setPen(QPen(QColor('#65558a'), 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(seam)

        painter.setPen(QPen(QColor('#56467a'), 1.25))
        painter.drawLine(QPointF(0.0, 1.1), QPointF(0.0, ry + 1.0))

        housing = QRectF(-2.1, 3.3, 4.2, 7.4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(line_col)
        painter.drawRoundedRect(housing, 2.1, 2.1)

        wheel_shift = self.wheel_pulse * 1.2
        wheel = QRectF(-1.3, 4.1 + wheel_shift, 2.6, 5.6)
        painter.setBrush(QColor('#c2aefa' if m_press > 0.15 else '#a496c7'))
        painter.drawRoundedRect(wheel, 1.3, 1.3)

        f_lr = QFont('PingFang SC')
        f_lr.setPixelSize(6)
        f_lr.setWeight(QFont.Weight.Bold)
        painter.setFont(f_lr)
        painter.setPen(QColor('#3a2f52' if l_press > 0.1 else '#8a7ca8'))
        painter.drawText(QRectF(2.3, 8.0, 8.0, 6.5), Qt.AlignmentFlag.AlignCenter, 'L')
        painter.setPen(QColor('#ffffff' if r_press > 0.1 else '#8a7ca8'))
        painter.drawText(QRectF(-10.3, 8.0, 8.0, 6.5), Qt.AlignmentFlag.AlignCenter, 'R')

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 175))
        painter.drawEllipse(QPointF(-4.2, -8.0), 3.8, 2.2)

        painter.restore()

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(line_col, 1.8))
        painter.drawPath(shell_path)
        painter.restore()

    def _draw_privacy_shield_local(self, painter: QPainter) -> None:
        painter.save()
        poly = self.deck.bezel_polygon_local(expand_x=1.5, expand_top=1.5, expand_bot=1.8)
        painter.setBrush(QColor(238, 232, 250, 224))
        painter.setPen(QPen(QColor('#7d6aa8'), 1.3))
        painter.drawPolygon(poly)

        badge = QRectF(self.deck.KB_LOCAL_CX - 54.0, (self.deck.KB_TOP_Y + self.deck.KB_BOT_Y) * 0.5 - 9.5, 108.0, 19.0)
        painter.setBrush(QColor('#fcfbfe'))
        painter.setPen(QPen(QColor(LINEART_HEX), 1.4))
        painter.drawRoundedRect(badge, 9.5, 9.5)

        f = QFont('PingFang SC')
        f.setPixelSize(9)
        f.setWeight(QFont.Weight.DemiBold)
        painter.setFont(f)
        painter.setPen(QColor(LINEART_HEX))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, '隐私保护 · 白龙已闭眼')
        painter.restore()

    def _draw_key_bubbles(self, painter: QPainter, now: float) -> None:
        if not self.bubbles:
            return
        fm = QFontMetricsF(self._font_bubble)
        line_col = QColor(LINEART_HEX)
        for b in self.bubbles:
            age = max(0.0, now - b.born)
            t = min(1.0, age / b.ttl)
            if t < 0.12:
                scale = 0.65 + math.sin((t / 0.12) * math.pi * 0.65) * 0.40
            elif t > 0.75:
                scale = 1.0 - ((t - 0.75) / 0.25) * 0.12
            else:
                scale = 1.0
            alpha = 1.0 if t < 0.70 else max(0.0, 1.0 - (t - 0.70) / 0.30)

            is_badge = b.accent_color is not None
            tw = 30.0 if is_badge else (34.0 if b.is_space else max(21.0, fm.horizontalAdvance(b.label) + 11.0))
            th = 17.0 if is_badge else 20.0

            painter.save()
            painter.setOpacity(alpha)
            painter.translate(b.x, b.y)
            painter.rotate(b.rot)
            painter.scale(scale, scale)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(34, 24, 58, 48))
            painter.drawRoundedRect(QRectF(-tw * 0.5, -th * 0.5 + 2.8, tw, th), 5.0, 5.0)

            skirt = QRectF(-tw * 0.5, -th * 0.5 + 1.8, tw, th)
            painter.setBrush(QColor('#6c5c91' if is_badge else '#c7bcdf'))
            painter.setPen(QPen(line_col, 1.35))
            painter.drawRoundedRect(skirt, 4.8, 4.8)

            face = QRectF(-tw * 0.5, -th * 0.5, tw, th - 0.8)
            painter.setBrush(QColor(b.accent_color) if is_badge else QColor('#fcfbfe'))
            painter.setPen(QPen(line_col, 1.35))
            painter.drawRoundedRect(face, 4.8, 4.8)

            if b.is_space:
                painter.setPen(QPen(line_col, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.drawLine(QPointF(-7.5, -0.8), QPointF(-7.5, 1.8))
                painter.drawLine(QPointF(-7.5, 1.8), QPointF(7.5, 1.8))
                painter.drawLine(QPointF(7.5, 1.8), QPointF(7.5, -0.8))
            else:
                painter.setFont(self._font_badge if is_badge else self._font_bubble)
                painter.setPen(line_col)
                painter.drawText(face, Qt.AlignmentFlag.AlignCenter, b.label)
            painter.restore()

    def _draw_anime_heart(self, painter: QPainter, x: float, y: float, size: float = 5.6) -> None:
        path = QPainterPath()
        s = size
        path.moveTo(0.0, s * 0.58)
        path.cubicTo(-s * 1.15, -s * 0.12, -s * 0.92, -s * 0.98, 0.0, -s * 0.36)
        path.cubicTo(s * 0.92, -s * 0.98, s * 1.15, -s * 0.12, 0.0, s * 0.58)
        path.closeSubpath()

        painter.save()
        painter.translate(x, y)
        painter.setBrush(QColor('#f798bc'))
        painter.setPen(QPen(QColor(LINEART_HEX), 1.35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 210))
        painter.drawEllipse(QPointF(-s * 0.34, -s * 0.34), s * 0.18, s * 0.13)
        painter.restore()

    def _draw_particles(self, painter: QPainter) -> None:
        if not self.particles:
            return
        f = QFont('PingFang SC')
        f.setPixelSize(10)
        f.setWeight(QFont.Weight.Bold)
        painter.setFont(f)
        for pt in self.particles:
            painter.save()
            painter.setOpacity(max(0.0, min(1.0, pt.life)))
            if pt.kind == 'zzz':
                painter.setPen(QColor('#6c589c'))
                painter.drawText(QPointF(pt.x, pt.y), 'Zzz')
            elif pt.kind == 'heart':
                self._draw_anime_heart(painter, pt.x, pt.y, size=5.6)
            else:
                painter.setPen(QColor('#9c7fe6'))
                painter.drawText(QPointF(pt.x, pt.y), '✦')
            painter.restore()
