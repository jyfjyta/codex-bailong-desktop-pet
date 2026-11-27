"""v6 Compact Steam-BongoCat tilted (+9.65°) 180° character-facing 3D 65% mechanical keyboard & mouse layout."""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap, QPen, QPolygonF

UI_FONT_FAMILY = 'Microsoft YaHei UI' if sys.platform == 'win32' else 'PingFang SC'

# Compact Steam-BongoCat footprint: at 0.8x desktop scale = 224x189 (matching normal 205x205 Bailong pet!)
COMPANION_W = 280
COMPANION_H = 236

DESK_TILT_DEG = 9.65
TOTAL_UNITS = 15.0
TOTAL_ROWS = 5.0
VK_NUMPAD_ENTER = 0x10D
LINEART_HEX = '#3a2f52'


@dataclass(frozen=True)
class KeySpec:
    id: str
    label: str
    bubble: str
    row: int
    u_start: float
    u_width: float
    accent: str  # 'normal' | 'mod' | 'rose'
    vks: tuple[int, ...]


@dataclass(frozen=True)
class ProjectedKey:
    spec: KeySpec
    well_quad: QPolygonF
    skirt_quad: QPolygonF
    top_quad: QPolygonF
    center: QPointF        # canvas (rotated) center for bubbles & ripples
    local_center: QPointF  # unrotated deck-local center for crisp painting inside rotated painter
    label_rect: QRectF     # deck-local label rect


KEYBOARD_65_SPECS: tuple[KeySpec, ...] = (
    # Row 0 (projects 180° to BOTTOM row; rose Esc at bottom-right!)
    KeySpec('esc', 'Esc', 'Esc', 0, 0.0, 1.0, 'rose', (0x1B, 0xC0)),
    KeySpec('1', '1', '1', 0, 1.0, 1.0, 'normal', (0x31, 0x61, 0x70)),
    KeySpec('2', '2', '2', 0, 2.0, 1.0, 'normal', (0x32, 0x62, 0x71)),
    KeySpec('3', '3', '3', 0, 3.0, 1.0, 'normal', (0x33, 0x63, 0x72)),
    KeySpec('4', '4', '4', 0, 4.0, 1.0, 'normal', (0x34, 0x64, 0x73)),
    KeySpec('5', '5', '5', 0, 5.0, 1.0, 'normal', (0x35, 0x65, 0x74)),
    KeySpec('6', '6', '6', 0, 6.0, 1.0, 'normal', (0x36, 0x66, 0x75)),
    KeySpec('7', '7', '7', 0, 7.0, 1.0, 'normal', (0x37, 0x67, 0x76)),
    KeySpec('8', '8', '8', 0, 8.0, 1.0, 'normal', (0x38, 0x68, 0x77)),
    KeySpec('9', '9', '9', 0, 9.0, 1.0, 'normal', (0x39, 0x69, 0x78)),
    KeySpec('0', '0', '0', 0, 10.0, 1.0, 'normal', (0x30, 0x60, 0x79)),
    KeySpec('minus', '-', '-', 0, 11.0, 1.0, 'normal', (0xBD, 0x6D, 0x7A)),
    KeySpec('equal', '=', '=', 0, 12.0, 1.0, 'normal', (0xBB, 0x6B, 0x7B)),
    KeySpec('bs', '⌫', '⌫', 0, 13.0, 2.0, 'mod', (0x08, 0x2E)),
    # Row 1
    KeySpec('tab', '↹', 'Tab', 1, 0.0, 1.5, 'mod', (0x09,)),
    KeySpec('q', 'Q', 'Q', 1, 1.5, 1.0, 'normal', (0x51,)),
    KeySpec('w', 'W', 'W', 1, 2.5, 1.0, 'normal', (0x57,)),
    KeySpec('e', 'E', 'E', 1, 3.5, 1.0, 'normal', (0x45,)),
    KeySpec('r', 'R', 'R', 1, 4.5, 1.0, 'normal', (0x52,)),
    KeySpec('t', 'T', 'T', 1, 5.5, 1.0, 'normal', (0x54,)),
    KeySpec('y', 'Y', 'Y', 1, 6.5, 1.0, 'normal', (0x59,)),
    KeySpec('u', 'U', 'U', 1, 7.5, 1.0, 'normal', (0x55,)),
    KeySpec('i', 'I', 'I', 1, 8.5, 1.0, 'normal', (0x49,)),
    KeySpec('o', 'O', 'O', 1, 9.5, 1.0, 'normal', (0x4F,)),
    KeySpec('p', 'P', 'P', 1, 10.5, 1.0, 'normal', (0x50,)),
    KeySpec('lb', '[', '[', 1, 11.5, 1.0, 'normal', (0xDB,)),
    KeySpec('rb', ']', ']', 1, 12.5, 1.0, 'normal', (0xDD,)),
    KeySpec('bslash', '\\', '\\', 1, 13.5, 1.5, 'mod', (0xDC, 0x2D, 0x24)),
    # Row 2
    KeySpec('caps', '⇪', 'Caps', 2, 0.0, 1.75, 'mod', (0x14,)),
    KeySpec('a', 'A', 'A', 2, 1.75, 1.0, 'normal', (0x41,)),
    KeySpec('s', 'S', 'S', 2, 2.75, 1.0, 'normal', (0x53,)),
    KeySpec('d', 'D', 'D', 2, 3.75, 1.0, 'normal', (0x44,)),
    KeySpec('f', 'F', 'F', 2, 4.75, 1.0, 'normal', (0x46,)),
    KeySpec('g', 'G', 'G', 2, 5.75, 1.0, 'normal', (0x47,)),
    KeySpec('h', 'H', 'H', 2, 6.75, 1.0, 'normal', (0x48,)),
    KeySpec('j', 'J', 'J', 2, 7.75, 1.0, 'normal', (0x4A,)),
    KeySpec('k', 'K', 'K', 2, 8.75, 1.0, 'normal', (0x4B,)),
    KeySpec('l', 'L', 'L', 2, 9.75, 1.0, 'normal', (0x4C,)),
    KeySpec('semi', ';', ';', 2, 10.75, 1.0, 'normal', (0xBA,)),
    KeySpec('quote', "'", "'", 2, 11.75, 1.0, 'normal', (0xDE,)),
    KeySpec('enter', '↵', '↵', 2, 12.75, 2.25, 'rose', (0x0D, VK_NUMPAD_ENTER)),
    # Row 3
    KeySpec('lshift', '⇧', '⇧', 3, 0.0, 2.25, 'mod', (0xA0, 0x10)),
    KeySpec('z', 'Z', 'Z', 3, 2.25, 1.0, 'normal', (0x5A,)),
    KeySpec('x', 'X', 'X', 3, 3.25, 1.0, 'normal', (0x58,)),
    KeySpec('c', 'C', 'C', 3, 4.25, 1.0, 'normal', (0x43,)),
    KeySpec('v', 'V', 'V', 3, 5.25, 1.0, 'normal', (0x56,)),
    KeySpec('b', 'B', 'B', 3, 6.25, 1.0, 'normal', (0x42,)),
    KeySpec('n', 'N', 'N', 3, 7.25, 1.0, 'normal', (0x4E,)),
    KeySpec('m', 'M', 'M', 3, 8.25, 1.0, 'normal', (0x4D,)),
    KeySpec('comma', ',', ',', 3, 9.25, 1.0, 'normal', (0xBC,)),
    KeySpec('dot', '.', '.', 3, 10.25, 1.0, 'normal', (0xBE, 0x6E)),
    KeySpec('slash', '/', '/', 3, 11.25, 1.0, 'normal', (0xBF, 0x6F)),
    KeySpec('rshift', '⇧', '⇧', 3, 12.25, 2.75, 'rose', (0xA1,)),
    # Row 4 (projects 180° to TOP row right next to Bailong's tilted desk collar!)
    KeySpec('lctrl', '⌃', '⌃', 4, 0.0, 1.25, 'mod', (0xA2, 0x11)),
    KeySpec('lalt', '⌥', '⌥', 4, 1.25, 1.25, 'mod', (0xA4, 0x12)),
    KeySpec('lcmd', '⌘', '⌘', 4, 2.5, 1.25, 'mod', (0x5B,)),
    KeySpec('space', '', '␣', 4, 3.75, 6.25, 'normal', (0x20,)),
    KeySpec('rcmd', '⌘', '⌘', 4, 10.0, 1.0, 'mod', (0x5C, 0xA5, 0xA3)),
    KeySpec('left', '←', '←', 4, 11.0, 1.0, 'mod', (0x25, 0x23)),
    KeySpec('down', '↓', '↓', 4, 12.0, 1.0, 'mod', (0x28, 0x22)),
    KeySpec('up', '↑', '↑', 4, 13.0, 1.0, 'mod', (0x26, 0x21)),
    KeySpec('right', '→', '→', 4, 14.0, 1.0, 'mod', (0x27,)),
)

TAIL_KEY_IDS: frozenset[str] = frozenset({
    'esc', 'enter', 'bs', 'rshift', 'lshift', 'caps', 'tab', 'up', 'down', 'left', 'right',
})


class PerspectiveDeck:
    """Projects a compact +9.65° BongoCat-tilted 180° 3D 65% mechanical keyboard & downward mouse in 280x236."""

    # Pivot of the tilted BongoCat desk plane in 280x236 canvas space
    DESK_PIVOT_X = 140.0
    DESK_PIVOT_Y = 155.0
    TILT_DEG = DESK_TILT_DEG

    # Keyboard coordinates in deck-local space (snugged comfortably inside Bailong's cute chibi armspan!)
    KB_LOCAL_CX = 30.0
    KB_TOP_Y = 7.0
    KB_BOT_Y = 47.0
    KB_TOP_HW = 62.0
    KB_BOT_HW = 69.0

    # Canvas-space conveniences (snug tilted mouse pad right under her left shoulder!)
    KB_CX = DESK_PIVOT_X + KB_LOCAL_CX
    MOUSE_LOCAL_X = -63.0
    MOUSE_LOCAL_Y = 22.0

    def __init__(self) -> None:
        self._rad = math.radians(self.TILT_DEG)
        self._cos = math.cos(self._rad)
        self._sin = math.sin(self._rad)
        self.MOUSE_HOME_X, self.MOUSE_HOME_Y = self.local_to_world_xy(self.MOUSE_LOCAL_X, self.MOUSE_LOCAL_Y)
        self.keys: dict[str, ProjectedKey] = {}
        self.vk_to_key: dict[int, ProjectedKey] = {}
        self._deck_cache_key: tuple[int, int] | None = None
        self._deck_pixmap: QPixmap | None = None
        self._build_geometry()

    def local_to_world_xy(self, lx: float, ly: float) -> tuple[float, float]:
        wx = self.DESK_PIVOT_X + lx * self._cos - ly * self._sin
        wy = self.DESK_PIVOT_Y + lx * self._sin + ly * self._cos
        return wx, wy

    def local_to_world(self, pt: QPointF) -> QPointF:
        wx, wy = self.local_to_world_xy(pt.x(), pt.y())
        return QPointF(wx, wy)

    def world_to_local(self, pt: QPointF) -> QPointF:
        dx = pt.x() - self.DESK_PIVOT_X
        dy = pt.y() - self.DESK_PIVOT_Y
        return QPointF(dx * self._cos + dy * self._sin, -dx * self._sin + dy * self._cos)

    def project_local(self, u_norm: float, r_norm: float) -> QPointF:
        depth = r_norm * (0.93 + 0.07 * r_norm)
        y = self.KB_TOP_Y + (self.KB_BOT_Y - self.KB_TOP_Y) * depth
        hw = self.KB_TOP_HW + (self.KB_BOT_HW - self.KB_TOP_HW) * depth
        x = self.KB_LOCAL_CX + (u_norm * 2.0 - 1.0) * hw
        return QPointF(x, y)

    def bezel_polygon_local(self, expand_x: float = 5.5, expand_top: float = 4.2, expand_bot: float = 5.2) -> QPolygonF:
        return QPolygonF([
            QPointF(self.KB_LOCAL_CX - self.KB_TOP_HW - expand_x, self.KB_TOP_Y - expand_top),
            QPointF(self.KB_LOCAL_CX + self.KB_TOP_HW + expand_x, self.KB_TOP_Y - expand_top),
            QPointF(self.KB_LOCAL_CX + self.KB_BOT_HW + expand_x + 1.5, self.KB_BOT_Y + expand_bot),
            QPointF(self.KB_LOCAL_CX - self.KB_BOT_HW - expand_x - 1.5, self.KB_BOT_Y + expand_bot),
        ])

    def _build_geometry(self) -> None:
        gap_u = 0.07
        gap_r = 0.09
        ordered_specs = sorted(KEYBOARD_65_SPECS, key=lambda s: (-s.row, -s.u_start))
        for spec in ordered_specs:
            u0_std = (spec.u_start + gap_u) / TOTAL_UNITS
            u1_std = (spec.u_start + spec.u_width - gap_u) / TOTAL_UNITS
            r0_std = (spec.row + gap_r) / TOTAL_ROWS
            r1_std = (spec.row + 1.0 - gap_r) / TOTAL_ROWS

            # 180° character-facing layout: Spacebar row at TOP near Bailong, rose Esc at BOTTOM-RIGHT!
            u0 = 1.0 - u1_std
            u1 = 1.0 - u0_std
            r0 = 1.0 - r1_std
            r1 = 1.0 - r0_std

            tl = self.project_local(u0, r0)
            tr = self.project_local(u1, r0)
            br = self.project_local(u1, r1)
            bl = self.project_local(u0, r1)

            well_quad = QPolygonF([
                QPointF(tl.x() - 0.5, tl.y() - 1.8),
                QPointF(tr.x() + 0.5, tr.y() - 1.8),
                QPointF(br.x() + 0.5, br.y() + 0.9),
                QPointF(bl.x() - 0.5, bl.y() + 0.9),
            ])
            skirt_quad = QPolygonF([tl, tr, br, bl])
            lift = 1.9
            inset_x = 0.85
            top_quad = QPolygonF([
                QPointF(tl.x() + inset_x, tl.y() - lift),
                QPointF(tr.x() - inset_x, tr.y() - lift),
                QPointF(br.x() - inset_x * 0.85, br.y() - lift - 0.45),
                QPointF(bl.x() + inset_x * 0.85, bl.y() - lift - 0.45),
            ])
            lcx = (tl.x() + tr.x() + br.x() + bl.x()) * 0.25
            lcy = (tl.y() + tr.y() + br.y() + bl.y()) * 0.25 - lift * 0.6
            w = max(6.0, (tr.x() - tl.x() + br.x() - bl.x()) * 0.5)
            h = max(6.0, (bl.y() - tl.y() + br.y() - tr.y()) * 0.5)
            local_c = QPointF(lcx, lcy)
            pk = ProjectedKey(
                spec=spec,
                well_quad=well_quad,
                skirt_quad=skirt_quad,
                top_quad=top_quad,
                center=self.local_to_world(local_c),
                local_center=local_c,
                label_rect=QRectF(lcx - w * 0.5, lcy - h * 0.5, w, h),
            )
            self.keys[spec.id] = pk
            for vk in spec.vks:
                self.vk_to_key[vk] = pk

    def resolve_vk(self, vk: int) -> ProjectedKey | None:
        direct = self.vk_to_key.get(vk)
        if direct is not None:
            return direct
        if 0x70 <= vk <= 0x7B:
            idx = str(min(9, max(1, vk - 0x6F)))
            return self.keys.get(idx)
        return None

    def static_deck_pixmap(self, pixel_w: int, pixel_h: int) -> QPixmap:
        key = (pixel_w, pixel_h)
        if self._deck_pixmap is not None and self._deck_cache_key == key:
            return self._deck_pixmap

        pm = QPixmap(pixel_w, pixel_h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.scale(pixel_w / COMPANION_W, pixel_h / COMPANION_H)

        # Enter +9.65° BongoCat tilted desk plane!
        p.translate(self.DESK_PIVOT_X, self.DESK_PIVOT_Y)
        p.rotate(self.TILT_DEG)

        line_col = QColor(LINEART_HEX)

        # 1) Compact rounded tilted BongoCat desk mat (snugged to chibi armspan)
        desk_mat = QRectF(-94.0, -1.0, 204.0, 58.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(38, 28, 64, 34))
        p.drawRoundedRect(desk_mat.translated(0.5, 2.8), 14.0, 14.0)

        mat_grad = QLinearGradient(desk_mat.topLeft(), desk_mat.bottomRight())
        mat_grad.setColorAt(0.0, QColor('#fbf9fe'))
        mat_grad.setColorAt(1.0, QColor('#ebe4f7'))
        p.setBrush(mat_grad)
        p.setPen(QPen(line_col, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawRoundedRect(desk_mat, 14.0, 14.0)

        # 2) Cel-shaded 3D tilted 180° mechanical keyboard chassis
        top_bezel = self.bezel_polygon_local(expand_x=5.2, expand_top=3.8, expand_bot=4.4)
        bot_wall = QPolygonF([
            top_bezel[0] + QPointF(0.0, 2.6),
            top_bezel[1] + QPointF(0.0, 2.6),
            top_bezel[2] + QPointF(0.6, 5.6),
            top_bezel[3] + QPointF(-0.6, 5.6),
        ])

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(34, 24, 58, 44))
        p.drawPolygon(bot_wall.translated(0.6, 2.0))

        wall_grad = QLinearGradient(-40.0, self.KB_TOP_Y, 100.0, self.KB_BOT_Y + 8.0)
        wall_grad.setColorAt(0.0, QColor('#8272a8'))
        wall_grad.setColorAt(0.6, QColor('#66568c'))
        wall_grad.setColorAt(1.0, QColor('#4f4073'))
        p.setBrush(wall_grad)
        p.setPen(QPen(line_col, 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolygon(bot_wall)

        bezel_grad = QLinearGradient(-35.0, self.KB_TOP_Y - 4.0, 105.0, self.KB_BOT_Y + 4.0)
        bezel_grad.setColorAt(0.0, QColor('#f8f5fd'))
        bezel_grad.setColorAt(0.55, QColor('#eae2f7'))
        bezel_grad.setColorAt(1.0, QColor('#d8cdee'))
        p.setBrush(bezel_grad)
        p.setPen(QPen(line_col, 1.85, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolygon(top_bezel)

        well = self.bezel_polygon_local(expand_x=1.5, expand_top=1.4, expand_bot=1.5)
        p.setBrush(QColor('#77689c'))
        p.setPen(QPen(line_col, 1.1))
        p.drawPolygon(well)

        font_alpha = QFont(UI_FONT_FAMILY)
        font_alpha.setPixelSize(6)
        font_alpha.setWeight(QFont.Weight.Bold)

        font_mod = QFont(UI_FONT_FAMILY)
        font_mod.setPixelSize(5)
        font_mod.setWeight(QFont.Weight.DemiBold)

        for pk in self.keys.values():
            self._paint_keycap(p, pk, pressed=0.0, font_alpha=font_alpha, font_mod=font_mod)

        p.end()
        self._deck_cache_key = key
        self._deck_pixmap = pm
        return pm

    def _paint_keycap(
        self,
        p: QPainter,
        pk: ProjectedKey,
        pressed: float,
        font_alpha: QFont,
        font_mod: QFont,
    ) -> None:
        accent = pk.spec.accent
        sink = max(0.0, min(1.0, pressed)) * 1.6

        if sink > 0.05:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor('#77689c'))
            p.drawPolygon(pk.well_quad)

        if accent == 'rose':
            side_col = QColor('#ba708f') if sink < 0.4 else QColor('#9e5072')
            top_col = QColor('#f4aec8') if sink < 0.4 else QColor('#eb8ab0')
            txt_col = QColor('#ffffff') if sink >= 0.4 else QColor('#462437')
            border_col = QColor('#4a2d42')
        elif accent == 'mod':
            side_col = QColor('#b2a4d1') if sink < 0.4 else QColor('#7c68b8')
            top_col = QColor('#eae3f7') if sink < 0.4 else QColor('#beadf0')
            txt_col = QColor('#3a2f52') if sink < 0.4 else QColor('#23183b')
            border_col = QColor('#4d3f6b')
        else:
            side_col = QColor('#cabfe0') if sink < 0.4 else QColor('#846ec4')
            top_col = QColor('#fcfbfe') if sink < 0.4 else QColor('#c7b6f7')
            txt_col = QColor('#3a2f52') if sink < 0.4 else QColor('#22173a')
            border_col = QColor('#584978') if sink < 0.4 else QColor('#433273')

        top = pk.top_quad.translated(0.0, sink)
        wall = QPolygonF([top[0], top[1], pk.skirt_quad[2], pk.skirt_quad[3]])
        p.setBrush(side_col)
        p.setPen(QPen(border_col, 0.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolygon(pk.skirt_quad)
        p.drawPolygon(wall)

        p.setBrush(top_col)
        p.setPen(QPen(border_col, 0.78, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolygon(top)

        if pk.spec.label:
            p.setFont(font_mod if len(pk.spec.label) > 1 or accent != 'normal' else font_alpha)
            p.setPen(txt_col)
            p.drawText(pk.label_rect.translated(0.0, sink), Qt.AlignmentFlag.AlignCenter, pk.spec.label)
        elif pk.spec.id == 'space':
            c = pk.local_center + QPointF(0.0, sink + 0.3)
            p.setPen(QPen(QColor(112, 98, 146, 150), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(c.x() - 7.5, c.y()), QPointF(c.x() + 7.5, c.y()))
