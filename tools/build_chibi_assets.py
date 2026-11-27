"""Build v6 Steam-BongoCat-style slim compact Q-version Bailong assets from the approved 2x2 concept sheet."""
from __future__ import annotations

import json
import math
from collections import deque
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFilter

BODY_SIZE = 512
DESK_TILT_DEG = 9.65


def desk_line_y(x: float) -> float:
    """Exact +9.65° BongoCat diagonal desk-edge line in 512x512 TL coordinate space."""
    return 320.5 + (402.5 - 320.5) * (x - 15.0) / (497.0 - 15.0)


def _shift_canvas(im: Image.Image, dx: int, dy: int, bg_rgb: tuple[int, int, int]) -> Image.Image:
    out = Image.new('RGB', im.size, bg_rgb)
    out.paste(im, (dx, dy))
    return out


def _remove_outer_lavender_bg(rgb: Image.Image, max_y_fn) -> Image.Image:
    """BFS flood-fill remove pale-lavender background above desk_line_y, keeping 100% of white hair & dark outlines."""
    w, h = rgb.size
    px = rgb.load()
    bg = rgb.getpixel((10, 10))
    bg_r, bg_g, bg_b = bg

    visited = bytearray(w * h)
    q: deque[tuple[int, int]] = deque()

    def is_bg_like(r: int, g: int, b: int) -> bool:
        # Pale lavender backdrop ~(237, 233, 250); dark #3a2f52 lineart stops flood cleanly!
        # Also allow soft anti-aliased lavender pixels near the backdrop while never crossing dark lineart (lum < 175)
        dist = abs(r - bg_r) + abs(g - bg_g) + abs(b - bg_b)
        return dist <= 34

    # Seed top border + left/right borders above desk line
    for x in range(w):
        q.append((x, 0))
        visited[x] = 1
    for y in range(h):
        if y < int(max_y_fn(0)) - 2:
            if not visited[y * w]:
                visited[y * w] = 1
                q.append((0, y))
        if y < int(max_y_fn(w - 1)) - 2:
            idx = y * w + (w - 1)
            if not visited[idx]:
                visited[idx] = 1
                q.append((w - 1, y))

    while q:
        x, y = q.popleft()
        lim_y = int(max_y_fn(x)) + 2
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < w and 0 <= ny < min(h, lim_y):
                idx = ny * w + nx
                if not visited[idx]:
                    r, g, b = px[nx, ny]
                    if is_bg_like(r, g, b):
                        visited[idx] = 1
                        q.append((nx, ny))

    # Build alpha mask: 0 for background, 255 for character, with 1px soft fringe decontamination
    rgba = rgb.convert('RGBA')
    out_px = rgba.load()
    for y in range(h):
        for x in range(w):
            if visited[y * w + x]:
                out_px[x, y] = (0, 0, 0, 0)

    # Also remove any disconnected floating islands (e.g. baked pink hearts in BL background!)
    # Keep only the largest connected opaque component (Bailong herself!)
    comp_id = [0] * (w * h)
    best_cid = 0
    best_count = 0
    cid = 0
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            idx = y * w + x
            if out_px[x, y][3] > 0 and comp_id[idx] == 0:
                cid += 1
                cq: deque[tuple[int, int]] = deque([(x, y)])
                comp_id[idx] = cid
                cnt = 0
                while cq:
                    cx, cy = cq.popleft()
                    cnt += 1
                    for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                        if 0 <= nx < w and 0 <= ny < h:
                            nidx = ny * w + nx
                            if comp_id[nidx] == 0 and out_px[nx, ny][3] > 0:
                                comp_id[nidx] = cid
                                cq.append((nx, ny))
                if cnt > best_count:
                    best_count = cnt
                    best_cid = cid

    for y in range(h):
        for x in range(w):
            if comp_id[y * w + x] != best_cid:
                out_px[x, y] = (0, 0, 0, 0)

    # Soften 1px alpha edge cleanly
    rc, gc, bc, ac = rgba.split()
    a_sm = ac.filter(ImageFilter.GaussianBlur(radius=0.55))
    a_b = bytearray(a_sm.tobytes())
    orig_b = ac.tobytes()
    for i in range(len(a_b)):
        if orig_b[i] == 255 and a_b[i] > 232:
            a_b[i] = 255
        elif a_b[i] < 18:
            a_b[i] = 0
    return Image.merge('RGBA', (rc, gc, bc, Image.frombytes('L', (w, h), bytes(a_b))))


def _clip_above_desk(rgba: Image.Image, extra_below: float = -1.5) -> Image.Image:
    """Clip character cleanly along the +9.65° BongoCat desk line and strip stray desk-edge tails beyond hair."""
    w, h = rgba.size
    out = rgba.copy()
    px = out.load()
    for y in range(h):
        for x in range(w):
            dline = desk_line_y(x)
            # Strip stray original dark desk line sticking out beyond her hair silhouette on left (x<68) & right (x>452)
            if (x < 68 or x > 452) and y > dline - 8.0:
                px[x, y] = (0, 0, 0, 0)
                continue
            dy = y - (dline + extra_below)
            if dy > 1.2:
                px[x, y] = (0, 0, 0, 0)
            elif dy > 0.0:
                r, g, b, a = px[x, y]
                px[x, y] = (r, g, b, int(round(a * (1.0 - dy / 1.2))))
    return out


def _extract_paw_polygon(rgb: Image.Image, poly: list[tuple[int, int]]) -> Image.Image:
    """Extract a dainty white-cuffed paw cleanly from the concept sheet with soft antialiased outline."""
    w, h = rgb.size
    mask = Image.new('L', (w, h), 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=0.8))
    rgba = rgb.convert('RGBA')
    empty = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    return Image.composite(rgba, empty, mask)


def _extract_paw_by_flood(rgb: Image.Image, roi: tuple[int, int, int, int], seed_out: list[tuple[int, int]]) -> Image.Image:
    """Extract a dainty cuffed paw inside ROI by removing baked white desk & lavender mouse/keyboard outside dark contour."""
    w, h = rgb.size
    x0, y0, x1, y1 = roi
    px = rgb.load()
    removed = bytearray(w * h)
    q: deque[tuple[int, int]] = deque()

    # Keep dark cartoon contour (lum < 120) as wall!
    # Also remove pixels strictly outside ROI.
    for sy_seed, sx_seed in [(s[1], s[0]) for s in seed_out]:
        if x0 <= sx_seed < x1 and y0 <= sy_seed < y1:
            idx = sy_seed * w + sx_seed
            if not removed[idx]:
                removed[idx] = 1
                q.append((sx_seed, sy_seed))

    # Also seed ROI bottom, left, right borders (below desk line) where mouse/keyboard/table live
    for x in range(x0, x1):
        for y in (y1 - 1,):
            r, g, b = px[x, y]
            if (r + g + b) / 3.0 >= 125:
                idx = y * w + x
                if not removed[idx]:
                    removed[idx] = 1
                    q.append((x, y))
    for y in range(y0, y1):
        for x in (x0, x1 - 1):
            r, g, b = px[x, y]
            if (r + g + b) / 3.0 >= 125:
                idx = y * w + x
                if not removed[idx]:
                    removed[idx] = 1
                    q.append((x, y))

    while q:
        x, y = q.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if x0 <= nx < x1 and y0 <= ny < y1:
                idx = ny * w + nx
                if not removed[idx]:
                    r, g, b = px[nx, ny]
                    lum = (r + g + b) / 3.0
                    # Stop at dark #3a2f52 cartoon contour (lum < 125)!
                    if lum >= 125:
                        removed[idx] = 1
                        q.append((nx, ny))

    # Anything inside ROI not reached by exterior flood (including dark paw outline & warm skin & white cuff!) is kept!
    mask = Image.new('L', (w, h), 0)
    mpx = mask.load()
    for y in range(y0, y1):
        for x in range(x0, x1):
            if not removed[y * w + x]:
                mpx[x, y] = 255
    # Gently smooth mask edge
    mask = mask.filter(ImageFilter.GaussianBlur(radius=0.6))
    rgba = rgb.convert('RGBA')
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    return Image.composite(rgba, out, mask)


def _build_bongo_live2d_eyes(body_idle: Image.Image) -> tuple[Image.Image, Image.Image, Image.Image]:
    """Split TL's cute ~5°-tilted flat-top purple eyes into (sclera, irises, eye_frame) using 100% pure original pixels."""
    w, h = body_idle.size
    # Left eye aperture (strictly below dark upper eyelid bar y=230..236 and above cheek skin y=274)
    left_ap = [
        (174, 236), (239, 239), (240, 256), (232, 269),
        (210, 274), (186, 270), (174, 257),
    ]
    # Right eye aperture (strictly below dark upper eyelid bar y=236..243 and above cheek skin y=281)
    right_ap = [
        (281, 243), (344, 246), (344, 263), (336, 276),
        (314, 281), (291, 277), (281, 264),
    ]

    ap_mask = Image.new('L', (w, h), 0)
    ad = ImageDraw.Draw(ap_mask)
    ad.polygon(left_ap, fill=255)
    ad.polygon(right_ap, fill=255)

    # Keep any thick dark upper-lid bar pixels (lum < 95 near top of aperture) in foreground frame
    src_px = body_idle.load()
    ap_px = ap_mask.load()
    for y in range(233, 250):
        for x in range(170, 348):
            if ap_px[x, y] > 0:
                r, g, b, _ = src_px[x, y]
                if (r + g + b) / 3.0 < 95:
                    ap_px[x, y] = 0

    ap_soft = ap_mask.filter(ImageFilter.GaussianBlur(radius=0.85))

    # 1) Clean sclera base matching Bailong's crisp white-lavender sclera
    sclera_fill = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    sf_px = sclera_fill.load()
    for y in range(232, 285):
        t = max(0.0, min(1.0, (y - 235.0) / 45.0))
        if t < 0.25:
            u = t / 0.25
            sr = int(round(226 + (248 - 226) * u))
            sg = int(round(218 + (245 - 218) * u))
            sb = int(round(235 + (250 - 235) * u))
        else:
            u = (t - 0.25) / 0.75
            sr = int(round(248 + (255 - 248) * u))
            sg = int(round(245 + (253 - 245) * u))
            sb = int(round(250 + (252 - 250) * u))
        for x in range(170, 348):
            sf_px[x, y] = (sr, sg, sb, 255)
    sclera = Image.composite(sclera_fill, body_idle, ap_soft)

    # 2) Pure original-art big round purple irises
    iris_mask = Image.new('L', (w, h), 0)
    idraw = ImageDraw.Draw(iris_mask)
    idraw.ellipse((207.5 - 23.0, 254.0 - 22.0, 207.5 + 23.0, 254.0 + 22.0), fill=255)
    idraw.ellipse((312.5 - 23.0, 260.0 - 22.0, 312.5 + 23.0, 260.0 + 22.0), fill=255)
    ap_exp = ap_mask.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(radius=0.9))
    iris_mask = ImageChops.darker(iris_mask.filter(ImageFilter.GaussianBlur(radius=0.9)), ap_exp)

    irises = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    irises = Image.composite(body_idle, irises, iris_mask)

    # 3) Foreground frame: body_idle with eye apertures punched out
    rc, gc, bc, ac = body_idle.split()
    frame_a = ImageChops.darker(ac, ImageChops.invert(ap_soft))
    eye_frame = Image.merge('RGBA', (rc, gc, bc, frame_a))

    return sclera, irises, eye_frame


def _build_calm_blink(body_idle: Image.Image, body_happy: Image.Image) -> Image.Image:
    """Create a super clean calm blink pose by taking happy's crisp curved dark lashes on idle's calm face."""
    w, h = body_idle.size
    out = body_idle.copy()
    eye_mask = Image.new('L', (w, h), 0)
    ed = ImageDraw.Draw(eye_mask)
    ed.ellipse((162, 218, 250, 284), fill=255)
    ed.ellipse((272, 222, 358, 290), fill=255)
    eye_mask = eye_mask.filter(ImageFilter.GaussianBlur(radius=2.5))
    return Image.composite(body_happy, out, eye_mask)


def _keep_largest_component(rgba: Image.Image, clip_poly: list[tuple[int, int]] | None = None) -> Image.Image:
    """Optionally clip to tight paw polygon and keep only the main connected paw component (zero stray desk stubs!)."""
    w, h = rgba.size
    out = rgba.copy()
    if clip_poly is not None:
        m = Image.new('L', (w, h), 0)
        ImageDraw.Draw(m).polygon(clip_poly, fill=255)
        m = m.filter(ImageFilter.GaussianBlur(radius=0.8))
        out = Image.composite(out, Image.new('RGBA', (w, h), (0, 0, 0, 0)), m)
    px = out.load()
    comp = [0] * (w * h)
    best_cid = 0
    best_cnt = 0
    cid = 0
    for y in range(h):
        for x in range(w):
            idx = y * w + x
            if px[x, y][3] > 20 and comp[idx] == 0:
                cid += 1
                q: deque[tuple[int, int]] = deque([(x, y)])
                comp[idx] = cid
                cnt = 0
                while q:
                    cx, cy = q.popleft()
                    cnt += 1
                    for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                        if 0 <= nx < w and 0 <= ny < h:
                            nidx = ny * w + nx
                            if comp[nidx] == 0 and px[nx, ny][3] > 20:
                                comp[nidx] = cid
                                q.append((nx, ny))
                if cnt > best_cnt:
                    best_cnt = cnt
                    best_cid = cid
    for y in range(h):
        for x in range(w):
            if comp[y * w + x] != best_cid:
                px[x, y] = (0, 0, 0, 0)
    return out


def _remove_static_desk_cuffs(body: Image.Image, shy_clean: Image.Image) -> Image.Image:
    """Erase static desk cuffs AND erase the two frozen vertical shy-pose arm stubs so zero ghost arms remain behind the desk!"""
    w, h = body.size
    out = body.copy()

    # 1) Patch out the baked-in static white wrist cuffs along desk_line_y using shy's real unoccluded hair/wings
    patch_mask = Image.new('L', (w, h), 0)
    md = ImageDraw.Draw(patch_mask)
    md.polygon([(98, 314), (178, 316), (182, 352), (96, 352)], fill=255)
    md.polygon([(322, 344), (390, 346), (392, 392), (320, 390)], fill=255)
    patch_mask = patch_mask.filter(ImageFilter.GaussianBlur(radius=2.0))
    out = Image.composite(shy_clean, out, patch_mask)
    out = _clip_above_desk(out, extra_below=-2.0)

    # 2) Erase the two frozen vertical shy-pose upper-arm stubs behind the desk (true Live2D armless torso!)
    px = out.load()

    def _erase_stub(poly: list[tuple[int, int]], fill_rgb: tuple[int, int, int]) -> None:
        m = Image.new('L', (w, h), 0)
        ImageDraw.Draw(m).polygon(poly, fill=255)
        m = m.filter(ImageFilter.GaussianBlur(radius=1.4))
        mpx = m.load()
        for y in range(305, 386):
            for x in range(150, 380):
                mv = mpx[x, y]
                if mv > 4 and px[x, y][3] > 20:
                    a = mv / 255.0
                    r, g, b, al = px[x, y]
                    px[x, y] = (
                        int(r * (1.0 - a) + fill_rgb[0] * a),
                        int(g * (1.0 - a) + fill_rgb[1] * a),
                        int(b * (1.0 - a) + fill_rgb[2] * a),
                        al,
                    )

    _erase_stub([(162, 315), (202, 318), (204, 348), (160, 342)], (225, 220, 242))
    _erase_stub([(314, 336), (358, 336), (356, 374), (308, 366)], (224, 218, 240))
    return out


def _build_collar_front_overlay(armless_idle: Image.Image) -> Image.Image:
    """Build Live2D Layer-4 front overlay: lower chin, cheeks, front side-hair & center white halter-dress bodice (strictly below eyes & mouth!)."""
    w, h = armless_idle.size
    mask = Image.new('L', (w, h), 0)
    md = ImageDraw.Draw(mask)
    # Strictly below y=300 (never touches eyes, Live2D moving pupils, blink lids, or happy ω mouth!)
    md.polygon(
        [
            (130, 300), (370, 300), (370, 330),
            (354, 330), (336, 326), (318, 330), (300, 332),
            (296, 366), (232, 356),
            (232, 324), (210, 320), (186, 314), (160, 312), (130, 312),
        ],
        fill=255,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(radius=1.2))
    # Preserve exact underlying RGB (never black 0,0,0!) so Qt SmoothTransformation never creates a dark alpha-halo seam!
    r_ch, g_ch, b_ch, a_ch = armless_idle.split()
    out_a = Image.new('L', (w, h), 0)
    apx, mpx, opx = a_ch.load(), mask.load(), out_a.load()
    for y in range(h):
        for x in range(w):
            mv = mpx[x, y]
            if mv > 0:
                opx[x, y] = (apx[x, y] * mv) // 255
    return Image.merge('RGBA', (r_ch, g_ch, b_ch, out_a))


def _extract_cropped_paw(
    rgb: Image.Image,
    idle_rgba: Image.Image,
    roi: tuple[int, int, int, int],
    seed_out: list[tuple[int, int]],
    poly: list[tuple[int, int]],
    pivot_src: tuple[float, float],
    tip_src: tuple[float, float],
) -> tuple[Image.Image, dict]:
    """Extract a tightly cropped original-art buttoned-cuff paw sprite with exact shoulder pivot & paw tip."""
    raw_paw = _extract_paw_by_flood(rgb, roi=roi, seed_out=seed_out)
    w, h = raw_paw.size
    ppx = raw_paw.load()
    bpx = idle_rgba.load()
    for y in range(h):
        for x in range(w):
            if y < desk_line_y(x) - 2.0 and bpx[x, y][3] == 0:
                ppx[x, y] = (0, 0, 0, 0)
    clean = _keep_largest_component(raw_paw, clip_poly=poly)
    bb = clean.getbbox()
    if bb is None:
        raise RuntimeError('Empty v8 paw extraction')
    pad = 4
    cl, ct = max(0, bb[0] - pad), max(0, bb[1] - pad)
    cr, cb = min(w, bb[2] + pad), min(h, bb[3] + pad)
    cropped = clean.crop((cl, ct, cr, cb))
    info = {
        'w': cr - cl,
        'h': cb - ct,
        'pivot': [round(pivot_src[0] - cl, 2), round(pivot_src[1] - ct, 2)],
        'tip': [round(tip_src[0] - cl, 2), round(tip_src[1] - ct, 2)],
    }
    return cropped, info


def _measure_face_box(rgb: Image.Image) -> tuple[float, float, float]:
    """Measure (face_cx, chin_y, face_w) of a portrait cell by scanning warm-cream cheek/chin skin pixels."""
    w, h = rgb.size
    px = rgb.load()
    xs: list[int] = []
    ys: list[int] = []
    # Restrict scan to central head area (exclude neck/shoulders below 78% height and ears outside 24%..76% width)
    for y in range(int(h * 0.40), int(h * 0.78)):
        for x in range(int(w * 0.24), int(w * 0.76)):
            r, g, b = px[x, y][:3]
            # Warm cream face skin or rosy pink blush on cheeks
            if (r > 232 and g > 205 and b > 185 and (r - b) > 10) or (r > 232 and g > 175 and b > 175 and (r - g) > 18):
                xs.append(x)
                ys.append(y)
    if not xs:
        return (w * 0.5, h * 0.72, w * 0.40)
    # Use 98th percentile of y to robustly pinpoint chin bottom without stray collar pixels
    ys.sort()
    xs.sort()
    x_lo = xs[max(0, int(len(xs) * 0.01))]
    x_hi = xs[min(len(xs) - 1, int(len(xs) * 0.99))]
    chin_y = ys[min(len(ys) - 1, int(len(ys) * 0.985))]
    return ((x_lo + x_hi) * 0.5, float(chin_y), float(max(20, x_hi - x_lo)))


def _transplant_expression_face(
    base_idle: Image.Image,
    cell_rgb: Image.Image,
    icon_kind: str | None = None,
    extra_dy: float = 0.0,
) -> Image.Image:
    """Align a portrait expression cell onto bongo_body_idle (+3.8° head tilt) and seamlessly transplant face + optional 💢/💧 icon."""
    w, h = base_idle.size  # 512x512
    # Exact target face landmarks on bongo_body_idle (head tilted +3.8° clockwise):
    # face_cx = 256.0, chin_y = 317.0, face_w = 174.0
    dst_cx, dst_chin, dst_w = 256.0, 316.0 + extra_dy, 173.0

    src_cx, src_chin, src_w = _measure_face_box(cell_rgb)
    scale = dst_w / max(40.0, src_w)

    # 1) Scale cell smoothly to match idle's exact face width
    nw = max(1, int(round(cell_rgb.width * scale)))
    nh = max(1, int(round(cell_rgb.height * scale)))
    scaled = cell_rgb.resize((nw, nh), Image.Resampling.LANCZOS).convert('RGBA')

    # 2) Place scaled cell on a 512x512 canvas aligning (src_cx, src_chin) -> (dst_cx, dst_chin)
    dx = int(round(dst_cx - src_cx * scale))
    dy = int(round(dst_chin - src_chin * scale))
    aligned = Image.new('RGBA', (w, h), (255, 245, 236, 0))
    aligned.paste(scaled, (dx, dy))

    # 3) Rotate -3.8° (clockwise) around face center (256, 260) to match bongo_body_idle's tilted head angle!
    aligned = aligned.rotate(-3.8, resample=Image.Resampling.BICUBIC, center=(256.0, 260.0), fillcolor=(255, 245, 236, 0))

    # 4) Build soft tilted face-interior mask (strictly INSIDE chin & cheek contours, covering brows + eyes + cheeks + mouth!)
    face_mask = Image.new('L', (w, h), 0)
    md = ImageDraw.Draw(face_mask)
    md.polygon(
        [
            (172, 212), (258, 215), (342, 222),
            (346, 258), (336, 294), (308, 313),
            (256, 317), (202, 310), (174, 286), (168, 250),
        ],
        fill=255,
    )
    # Keep bongo_body_idle's crisp dark chin/cheek outer ink untouched by shrinking mask slightly where idle has dark hair/chin borders
    face_mask = face_mask.filter(ImageFilter.GaussianBlur(radius=4.5))

    # Preserve idle's center white forehead bangs tips (y < 236 around x=240..274) gently only if they aren't eyebrows
    out = Image.composite(aligned, base_idle, face_mask)

    # 5) Optional iconic emotion marks from the artist's cell: red anger cross 💢 or blue sweatdrop 💧
    if icon_kind in ('anger', 'sweat'):
        apx = aligned.load()
        imask = Image.new('L', (w, h), 0)
        ipx = imask.load()
        if icon_kind == 'anger':
            # Red anger mark 💢 in upper-right forehead/horn region (x=285..365, y=125..215)
            for y in range(120, 220):
                for x in range(280, 370):
                    r, g, b, al = apx[x, y]
                    if al > 180 and r > 125 and (r - g) > 36 and (r - b) > 30:
                        ipx[x, y] = 255
        elif icon_kind == 'sweat':
            # Sky-blue sweatdrop 💧 near right temple (x=315..385, y=165..245)
            for y in range(160, 248):
                for x in range(310, 390):
                    r, g, b, al = apx[x, y]
                    if al > 180 and b > 165 and (b - r) > 16 and g > 140:
                        ipx[x, y] = 255
        # Dilate by 5px to include crisp dark outline around the icon, then feather softly
        imask = imask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(radius=0.8))
        out = Image.composite(aligned, out, imask)

    # Ensure outer transparency & tilted desk clip stay 100% locked to base_idle!
    r_o, g_o, b_o, _ = out.split()
    _, _, _, a_base = base_idle.split()
    return Image.merge('RGBA', (r_o, g_o, b_o, a_base))


def build_rig_assets(brain: Path, out_dir: Path, preview_path: Path | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    src_path = brain / 'bongo_q_redesign_concept_v2_1795681947503.jpg'
    with Image.open(src_path) as raw_im:
        sheet = raw_im.convert('RGB')

    hw, hh = 512, 512
    tl_rgb = sheet.crop((0, 0, hw, hh))
    bl_rgb = _shift_canvas(sheet.crop((0, hh, hw, 1024)), 4, 9, tl_rgb.getpixel((10, 10)))
    br_rgb = _shift_canvas(sheet.crop((hw, hh, 1024, 1024)), 4, 10, tl_rgb.getpixel((10, 10)))

    # 1) Remove outer pale-lavender backdrop & erase static desk cuffs + frozen shy arm stubs
    idle_rgba = _remove_outer_lavender_bg(tl_rgb, desk_line_y)
    happy_old_rgba = _remove_outer_lavender_bg(bl_rgb, desk_line_y)
    shy_rgba = _remove_outer_lavender_bg(br_rgb, desk_line_y)

    bongo_body_shy = _clip_above_desk(shy_rgba, extra_below=-3.0)
    bongo_body_idle = _remove_static_desk_cuffs(_clip_above_desk(idle_rgba, extra_below=-1.5), bongo_body_shy)
    bongo_body_happy_old = _remove_static_desk_cuffs(_clip_above_desk(happy_old_rgba, extra_below=-3.0), bongo_body_shy)
    bongo_body_blink = _build_calm_blink(bongo_body_idle, bongo_body_happy_old)

    # 1b) Extract all 7 user-approved KuroCat-style interactive expressions from v14 (3x2) & v15 (2x2: B=happy, D=wink)!
    v14_path = brain / 'bailong_expressions_v14_1795745043033.jpg'
    v15_path = brain / 'bailong_happy_variants_v15_1795745272832.jpg'
    with Image.open(v14_path) as im14, Image.open(v15_path) as im15:
        s14 = im14.convert('RGB')
        s15 = im15.convert('RGB')
        cw14, ch14 = s14.width // 3, s14.height // 2
        cw15, ch15 = s15.width // 2, s15.height // 2

        bongo_body_happy = _transplant_expression_face(
            bongo_body_idle, s15.crop((cw15, 0, cw15 * 2, ch15)), extra_dy=0.0
        )  # v15 B (弯眼灿烂大笑)
        bongo_body_wink = _transplant_expression_face(
            bongo_body_idle, s15.crop((cw15, ch15, cw15 * 2, ch15 * 2)), extra_dy=0.0
        )  # v15 D (俏皮 Wink 甜笑)
        bongo_body_angry = _transplant_expression_face(
            bongo_body_idle, s14.crop((cw14, 0, cw14 * 2, ch14)), icon_kind='anger', extra_dy=-1.0
        )  # v14 奶凶生气 💢
        bongo_body_sleepy = _transplant_expression_face(
            bongo_body_idle, s14.crop((cw14 * 2, 0, s14.width, ch14)), extra_dy=0.0
        )  # v14 迷糊瞌睡
        bongo_body_starry = _transplant_expression_face(
            bongo_body_idle, s14.crop((0, ch14, cw14, s14.height)), extra_dy=0.0
        )  # v14 星星眼兴奋
        bongo_body_smug = _transplant_expression_face(
            bongo_body_idle, s14.crop((cw14, ch14, cw14 * 2, s14.height)), extra_dy=0.0
        )  # v14 傲娇得意
        bongo_body_flustered = _transplant_expression_face(
            bongo_body_idle, s14.crop((cw14 * 2, ch14, s14.width, s14.height)), icon_kind='sweat', extra_dy=0.0
        )  # v14 委屈泪目 💧

    # 2) Pure original-art Live2D purple eye gaze layers + chin/collar front overlay
    bongo_sclera, bongo_irises, bongo_eye_frame = _build_bongo_live2d_eyes(bongo_body_idle)
    bongo_collar_front = _build_collar_front_overlay(bongo_body_idle)

    # 3) Tightly cropped original-art buttoned-cuff paws anchored cleanly with ZERO stray desk/wing lines!
    bongo_paw_l, paw_l_meta = _extract_cropped_paw(
        tl_rgb,
        idle_rgba,
        roi=(82, 320, 162, 390),
        seed_out=[(84, 388), (160, 388), (130, 388), (84, 344), (160, 322)],
        poly=[
            (121, 322), (135, 327), (147, 334), (155, 345), (158, 358),
            (156, 368), (146, 373), (137, 374), (134, 380), (126, 386),
            (108, 388), (91, 382), (84, 372), (85, 360), (94, 348),
            (106, 343), (111, 333), (118, 323),
        ],
        pivot_src=(142.0, 340.0),
        tip_src=(108.0, 366.0),
    )
    # Trim any tiny 2-3px pale-lavender exterior fringe on the upper-left corner of bongo_paw_l
    lpx = bongo_paw_l.load()
    for py in range(min(24, bongo_paw_l.height)):
        for px_i in range(min(56, bongo_paw_l.width)):
            r, g, b, al = lpx[px_i, py]
            if al > 0 and (b - r > 5 or (r < 220 and g < 210 and b > 195)) and (r + g + b > 420):
                lpx[px_i, py] = (0, 0, 0, 0)

    bongo_paw_r, paw_r_meta = _extract_cropped_paw(
        tl_rgb,
        idle_rgba,
        roi=(318, 354, 388, 418),
        seed_out=[(320, 416), (386, 416), (350, 416), (320, 360), (386, 360)],
        poly=[
            (338, 358), (352, 356), (367, 361), (377, 369), (384, 377),
            (385, 384), (379, 391), (374, 397), (371, 405), (362, 414),
            (350, 416), (336, 414), (325, 406), (321, 395), (323, 385),
            (330, 377), (332, 367), (336, 360),
        ],
        pivot_src=(354.0, 364.0),
        tip_src=(347.0, 395.0),
    )

    assets = {
        'bongo_body_idle': bongo_body_idle,
        'bongo_body_blink': bongo_body_blink,
        'bongo_body_happy': bongo_body_happy,
        'bongo_body_wink': bongo_body_wink,
        'bongo_body_angry': bongo_body_angry,
        'bongo_body_sleepy': bongo_body_sleepy,
        'bongo_body_starry': bongo_body_starry,
        'bongo_body_smug': bongo_body_smug,
        'bongo_body_flustered': bongo_body_flustered,
        'bongo_body_shy': bongo_body_shy,
        'bongo_sclera': bongo_sclera,
        'bongo_irises': bongo_irises,
        'bongo_eye_frame': bongo_eye_frame,
        'bongo_collar_front': bongo_collar_front,
        'bongo_paw_l': bongo_paw_l,
        'bongo_paw_r': bongo_paw_r,
    }
    for name, im in assets.items():
        im.save(out_dir / f'{name}.png', optimize=True)

    meta = {
        'version': 10,
        'body_size': BODY_SIZE,
        'desk_tilt_deg': DESK_TILT_DEG,
        'desk_y_center_norm': round(desk_line_y(256.0) / BODY_SIZE, 4),
        'eye_center_norm': [round(260.0 / BODY_SIZE, 4), round(257.0 / BODY_SIZE, 4)],
        'shoulder_l_norm': [round(212.0 / BODY_SIZE, 4), round(326.0 / BODY_SIZE, 4)],
        'shoulder_r_norm': [round(306.0 / BODY_SIZE, 4), round(336.0 / BODY_SIZE, 4)],
        'paw_l': paw_l_meta,
        'paw_r': paw_r_meta,
    }
    (out_dir / 'meta.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')

    if preview_path is not None:
        # 4x2 contact sheet of idle + all 7 new expressions so we can inspect pixel alignment!
        cells = [
            bongo_body_idle, bongo_body_happy, bongo_body_wink, bongo_body_angry,
            bongo_body_sleepy, bongo_body_starry, bongo_body_smug, bongo_body_flustered,
        ]
        tw = 280
        sheet_out = Image.new('RGBA', (tw * 4, tw * 2), (243, 239, 250, 255))
        for idx, cim in enumerate(cells):
            r_i, c_i = divmod(idx, 4)
            sheet_out.alpha_composite(cim.resize((tw, tw), Image.Resampling.LANCZOS), (c_i * tw, r_i * tw))
        sheet_out.save(preview_path)

    return meta


if __name__ == '__main__':
    brain = Path('/Users/apple/.gemini/antigravity/brain/c3e84c92-aa49-4cd2-b75f-d8778d42b893')
    project = Path(__file__).resolve().parents[1]
    out = project / 'app' / 'typing_companion' / 'assets'
    preview = brain / 'scratch' / 'bongo_v6_parts.png'
    preview.parent.mkdir(parents=True, exist_ok=True)
    info = build_rig_assets(brain, out, preview)
    print('Built v6 BongoCat Q-version assets:', json.dumps(info, indent=2))
