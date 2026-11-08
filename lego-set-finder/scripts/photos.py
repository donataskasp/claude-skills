"""Find pieces in photos, cut one piece per image, and draw sheets the agent can check by eye.

Detection assumes pieces lie apart on a plain background (a sheet, a table, a floor):
1. estimate the bare background from tile medians, skipping tiles a piece covers,
2. mark pixels whose colour or brightness differs from it (LAB, Otsu threshold); being only
   darker counts less, so soft shadows stay background,
3. close pin holes, drop specks, label connected blobs, try to split blobs that are far bigger
   than the rest (touching pieces), merge fragments, pad the boxes.

Boxes are stored in "sheet coordinates": pixels of the overview image, whose longest side is
SHEET_LONG. The overview has a labelled grid, so the agent can read coordinates off it and add or
fix boxes by hand when detection merges or misses pieces.
"""
import os
import re

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat
except ImportError:  # pragma: no cover
    raise SystemExit("This step needs Pillow. Install it with: python3 -m pip install --user Pillow")

SHEET_LONG = 1200
WORK_LONG = 1000
CROP_LONG = 1024
PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp")
HEIC_EXT = (".heic", ".heif")
HEIC_HELP = ("HEIC photos need the pillow-heif package: python3 -m pip install --user pillow-heif\n"
             "Or export them as JPEG first (on an iPhone: Settings > Camera > Formats > Most Compatible, "
             "or share the photos to a Mac or PC and export as JPEG).")


def heic_support():
    """Register pillow-heif with Pillow when it is installed. Returns True when HEIC can be read."""
    try:
        from pillow_heif import register_heif_opener
    except ImportError:
        return False
    register_heif_opener()
    return True


def font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def open_photo(path):
    if path.lower().endswith(HEIC_EXT) and not heic_support():
        raise SystemExit(HEIC_HELP)
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    return im.convert("RGB")


def scale_to(im, longest):
    w, h = im.size
    f = longest / float(max(w, h))
    return im.resize((max(1, round(w * f)), max(1, round(h * f))), Image.LANCZOS), f


def otsu(hist):
    total = sum(hist)
    if not total:
        return 128
    sum_all = sum(i * h for i, h in enumerate(hist))
    w_b = sum_b = 0
    best, best_t = -1.0, 0
    for t in range(256):
        w_b += hist[t]
        if not w_b:
            continue
        w_f = total - w_b
        if not w_f:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_all - sum_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best:
            best, best_t = between, t
    return best_t


def label_blobs(mask):
    """Connected components (8-connected) of a mode "L" mask. Returns [x0, y0, x1, y1, area]."""
    w, h = mask.size
    data = mask.tobytes()
    parent = []

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    runs = []
    prev = []
    on = re.compile(rb"[^\x00]+")
    for y in range(h):
        row = data[y * w:(y + 1) * w]
        cur = []
        j = 0
        for m in on.finditer(row):
            x0, x1 = m.start(), m.end()
            lab = None
            while j < len(prev) and prev[j][1] < x0:
                j += 1
            k = j
            while k < len(prev) and prev[k][0] <= x1:
                pl = find(prev[k][2])
                if lab is None:
                    lab = pl
                elif pl != lab:
                    parent[max(pl, lab)] = min(pl, lab)
                    lab = min(pl, lab)
                k += 1
            if lab is None:
                lab = len(parent)
                parent.append(lab)
            cur.append((x0, x1, lab))
            runs.append((y, x0, x1, lab))
        prev = cur
    boxes = {}
    for y, x0, x1, lab in runs:
        r = find(lab)
        b = boxes.get(r)
        if b is None:
            boxes[r] = [x0, y, x1, y + 1, x1 - x0]
        else:
            b[0] = min(b[0], x0)
            b[1] = min(b[1], y)
            b[2] = max(b[2], x1)
            b[3] = max(b[3], y + 1)
            b[4] += x1 - x0
    return list(boxes.values())


_LAB = None


def _to_lab(im):
    """RGB -> LAB with Pillow's LittleCMS binding, or None when it is not available."""
    global _LAB
    if _LAB is None:
        try:
            from PIL import ImageCms
            _LAB = ImageCms.buildTransformFromOpenProfiles(
                ImageCms.createProfile("sRGB"), ImageCms.createProfile("LAB"), "RGB", "LAB")
        except Exception:  # Pillow built without LittleCMS
            _LAB = False
    if not _LAB:
        return None
    from PIL import ImageCms
    return ImageCms.applyTransform(im, _LAB)


def background(work):
    """Smooth estimate of the bare background, robust to large pieces and dense piles.

    The photo is cut into tiles; each tile's median colour is a background sample unless it is far
    from the photo's overall median (then a piece covers most of it). Rejected tiles are filled in
    from their neighbours, and the tile grid is scaled up smoothly.
    """
    w, h = work.size
    small = work.resize((max(16, w // 8), max(16, h // 8)), Image.BOX)
    sw, sh = small.size
    t = max(4, min(sw, sh) // 10)
    cols, rows = (sw + t - 1) // t, (sh + t - 1) // t
    glob = ImageStat.Stat(small).median
    grid, dist = {}, {}
    for j in range(rows):
        for i in range(cols):
            tile = small.crop((i * t, j * t, min(sw, (i + 1) * t), min(sh, (j + 1) * t)))
            m = ImageStat.Stat(tile).median
            grid[i, j] = m
            dist[i, j] = max(abs(a - b) for a, b in zip(m, glob))
    ds = sorted(dist.values())
    limit = max(18, ds[int(len(ds) * 0.4)] * 2.5)
    good = {k: v for k, v in grid.items() if dist[k] <= limit}
    if not good:
        good = {k: tuple(glob) for k in grid}
    for _ in range(cols + rows):  # fill rejected tiles from good neighbours
        missing = [k for k in grid if k not in good]
        if not missing:
            break
        add = {}
        for (i, j) in missing:
            nb = [good[n] for n in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)) if n in good]
            if nb:
                add[i, j] = tuple(sum(c[x] for c in nb) / len(nb) for x in range(3))
        if not add:
            break
        good.update(add)
    g = Image.new("RGB", (cols, rows))
    g.putdata([tuple(int(round(c)) for c in good.get((i, j), glob)) for j in range(rows) for i in range(cols)])
    g = g.resize((cols * 4, rows * 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(2))
    return g.resize(work.size, Image.BILINEAR).filter(ImageFilter.GaussianBlur(8))


def difference_score(work, bg):
    """0-255 per pixel: how unlike the background it is. Shadows (only darker) count less."""
    a, b = _to_lab(work), _to_lab(bg)
    if a is not None:
        L1, a1, b1 = a.split()
        L2, a2, b2 = b.split()
        chroma = ImageChops.lighter(ImageChops.difference(a1, a2), ImageChops.difference(b1, b2))
    else:
        L1, L2 = work.convert("L"), bg.convert("L")
        chroma = ImageChops.difference(work.convert("HSV").split()[1], bg.convert("HSV").split()[1])
    chroma = chroma.point(lambda v: min(255, v * 3))
    bright = ImageChops.subtract(L1, L2)
    dark = ImageChops.subtract(L2, L1).point(lambda v: int(v * 0.6))
    return ImageChops.lighter(ImageChops.lighter(chroma, bright), dark)


def foreground(work, threshold=None):
    """Mask of pixels that differ from the background. Returns (mask, threshold used)."""
    score = difference_score(work, background(work))
    if threshold is None:
        threshold = min(60, max(15, otsu(score.histogram())))
    mask = score.point(lambda v: 255 if v > threshold else 0)
    mask = mask.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))  # close pin holes
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))  # drop specks
    return mask, threshold


def merge_nested(boxes, frac=0.5):
    """Merge boxes that mostly overlap (fragments of one piece)."""
    boxes = [list(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        out = []
        while boxes:
            a = boxes.pop()
            for b in boxes:
                ix = min(a[2], b[2]) - max(a[0], b[0])
                iy = min(a[3], b[3]) - max(a[1], b[1])
                if ix > 0 and iy > 0:
                    inter = ix * iy
                    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
                    if inter >= frac * small:
                        b[0], b[1] = min(a[0], b[0]), min(a[1], b[1])
                        b[2], b[3] = max(a[2], b[2]), max(a[3], b[3])
                        b[4] += a[4]
                        changed = True
                        break
            else:
                out.append(a)
        boxes = out
    return boxes


def split_blob(mask, blob, min_area):
    """Try to split touching pieces: erode the blob harder until it falls apart."""
    x0, y0, x1, y1, _a = blob
    region = mask.crop((x0, y0, x1, y1))
    for k in (5, 9, 13, 17):
        er = region.filter(ImageFilter.MinFilter(k))
        r = (k - 1) // 2
        parts = [p for p in label_blobs(er) if p[4] >= min_area * 0.5]
        if len(parts) >= 2:
            return [[max(x0, x0 + p[0] - r), max(y0, y0 + p[1] - r), min(x1, x0 + p[2] + r), min(y1, y0 + p[3] + r), p[4]]
                    for p in parts]
    return []


def detect(path, threshold=None, min_area=0.0004, max_side=0.5, pad=0.12):
    """Return (boxes in sheet coordinates, info). Each box: [x0, y0, x1, y1]."""
    im = open_photo(path)
    work, fw = scale_to(im, WORK_LONG)
    mask, used = foreground(work, threshold)
    w, h = work.size
    raw = label_blobs(mask)
    areas = sorted(b[4] for b in raw if b[4] >= min_area * w * h)
    typical = areas[len(areas) // 2] if areas else 0
    blobs = []
    for b in raw:
        x0, y0, x1, y1, area = b
        if area < min_area * w * h or min(x1 - x0, y1 - y0) < 4:
            continue
        huge = (x1 - x0) > max_side * w and (y1 - y0) > max_side * h
        if huge or (typical and area > 6 * typical and area > 0.02 * w * h):
            parts = split_blob(mask, b, min_area * w * h)
            if len(parts) >= 2:
                blobs.extend(parts)
                continue
            if huge:
                continue  # one big region that will not split: background, not a piece
        blobs.append(b)
    blobs = merge_nested(blobs)
    f = SHEET_LONG / float(WORK_LONG)
    sw, sh = round(w * f), round(h * f)
    out = []
    for x0, y0, x1, y1, _a in blobs:
        p = max(6, pad * max(x1 - x0, y1 - y0))
        out.append([max(0, round((x0 - p) * f)), max(0, round((y0 - p) * f)),
                    min(sw, round((x1 + p) * f)), min(sh, round((y1 + p) * f))])
    out = reading_order(out)
    return out, {"threshold": used, "size": im.size, "blobs": len(out)}


def reading_order(boxes):
    if not boxes:
        return boxes
    hs = sorted(b[3] - b[1] for b in boxes)
    band = max(20, hs[len(hs) // 2] * 0.7)
    return sorted(boxes, key=lambda b: (int(((b[1] + b[3]) / 2) // band), b[0]))


def draw_overview(path, boxes, out_path, labels=None, colors=None):
    """Overview image with a labelled 100 px grid and numbered boxes, in sheet coordinates."""
    im, _f = scale_to(open_photo(path), SHEET_LONG)
    w, h = im.size
    d = ImageDraw.Draw(im, "RGBA")
    small = font(13)
    for x in range(100, w, 100):
        d.line([(x, 0), (x, h)], fill=(255, 255, 255, 70), width=1)
        d.text((x + 2, 2), str(x), fill=(255, 255, 0, 255), font=small, stroke_width=2, stroke_fill=(0, 0, 0, 255))
    for y in range(100, h, 100):
        d.line([(0, y), (w, y)], fill=(255, 255, 255, 70), width=1)
        d.text((2, y + 2), str(y), fill=(255, 255, 0, 255), font=small, stroke_width=2, stroke_fill=(0, 0, 0, 255))
    big = font(20)
    for i, b in enumerate(boxes):
        lab = str(labels[i]) if labels else str(i + 1)
        col = (colors[i] if colors else (255, 0, 200)) + (255,)
        d.rectangle(b[:4], outline=col, width=3)
        d.text((b[0] + 4, b[1] + 2), lab, fill=(255, 255, 255, 255), font=big, stroke_width=3, stroke_fill=(0, 0, 0, 255))
    im.save(out_path, quality=85)


MAX_CROP_SHARE = 0.4  # a box over this share of the photo is not one piece


def draw_zoom(path, region, out_path, scale=2.0, boxes=(), labels=(), colors=()):
    """A region of the overview (sheet coordinates) at `scale` times the size, cut from the full
    photo, with a grid every 50 px labelled in the same sheet coordinates and the kept boxes."""
    im = open_photo(path)
    W, H = im.size
    f = max(W, H) / float(SHEET_LONG)
    x0, y0, x1, y1 = region
    part = im.crop((max(0, int(x0 * f)), max(0, int(y0 * f)), min(W, int(x1 * f)), min(H, int(y1 * f))))
    zw, zh = max(1, int((x1 - x0) * scale)), max(1, int((y1 - y0) * scale))
    z = part.resize((zw, zh), Image.LANCZOS)
    d = ImageDraw.Draw(z, "RGBA")
    small = font(13)
    for gx in range((x0 // 50 + 1) * 50, x1, 50):
        X = (gx - x0) * scale
        d.line([(X, 0), (X, zh)], fill=(255, 255, 255, 90), width=1)
        d.text((X + 2, 2), str(gx), fill=(255, 255, 0, 255), font=small, stroke_width=2, stroke_fill=(0, 0, 0, 255))
    for gy in range((y0 // 50 + 1) * 50, y1, 50):
        Y = (gy - y0) * scale
        d.line([(0, Y), (zw, Y)], fill=(255, 255, 255, 90), width=1)
        d.text((2, Y + 2), str(gy), fill=(255, 255, 0, 255), font=small, stroke_width=2, stroke_fill=(0, 0, 0, 255))
    big = font(20)
    for b, lab, col in zip(boxes, labels, colors):
        if b[2] < x0 or b[0] > x1 or b[3] < y0 or b[1] > y1:
            continue
        r = [(b[0] - x0) * scale, (b[1] - y0) * scale, (b[2] - x0) * scale, (b[3] - y0) * scale]
        d.rectangle(r, outline=tuple(col) + (255,), width=3)
        d.text((r[0] + 4, r[1] + 2), str(lab), fill=(255, 255, 255, 255), font=big, stroke_width=3,
               stroke_fill=(0, 0, 0, 255))
    z.save(out_path, quality=88)


def crop(path, boxes_sheet, out_dir, prefix, manual_pad=0.04, refused=None):
    """Cut each box (sheet coordinates) from the full photo. Returns the crop paths.

    Only these crops are ever sent to Brickognize, never the photo itself. A box that covers most
    of the photo is refused, so a careless box cannot send the whole photo. Crops are saved with
    no metadata at all: no EXIF (camera, time, location), no XMP, no colour profile, no comment."""
    im = open_photo(path)
    W, H = im.size
    f = max(W, H) / float(SHEET_LONG)
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for box_id, b, manual in boxes_sheet:
        x0, y0, x1, y1 = [v * f for v in b]
        if manual:
            p = manual_pad * max(x1 - x0, y1 - y0)
            x0, y0, x1, y1 = x0 - p, y0 - p, x1 + p, y1 + p
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(W, int(x1)), min(H, int(y1))
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue
        if (x1 - x0) * (y1 - y0) > MAX_CROP_SHARE * W * H:
            if refused:
                refused(box_id)
            continue
        c = im.crop((x0, y0, x1, y1))
        if max(c.size) > CROP_LONG:
            c, _ = scale_to(c, CROP_LONG)
        p = os.path.join(out_dir, "%s-%s.jpg" % (prefix, box_id))
        c.info = {}  # no EXIF, XMP, ICC or JPEG comment from the photo goes into the crop
        c.save(p, quality=90)
        paths.append(p)
    return paths


def contact_sheet(items, out_path, cell=220, cols=6, lines=2):
    """items: [(image path, [text lines])]. Writes a grid of thumbnails with captions."""
    if not items:
        return
    f = font(13)
    line_h = 16
    cap = line_h * lines + 6
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + cap)), "white")
    d = ImageDraw.Draw(sheet)
    for i, (p, text) in enumerate(items):
        x, y = (i % cols) * cell, (i // cols) * (cell + cap)
        try:
            th = Image.open(p).convert("RGB")
            th.thumbnail((cell - 8, cell - 8))
            sheet.paste(th, (x + (cell - th.size[0]) // 2, y + 4 + (cell - 8 - th.size[1]) // 2))
        except OSError:
            d.text((x + 8, y + 8), "unreadable", fill="red", font=f)
        for j, t in enumerate(text[:lines]):
            d.text((x + 4, y + cell + 2 + j * line_h), t[:34], fill="black", font=f)
        d.rectangle([x, y, x + cell - 1, y + cell + cap - 1], outline=(200, 200, 200))
    sheet.save(out_path, quality=85)


def list_photos(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for n in sorted(os.listdir(p)):
                if n.lower().endswith(PHOTO_EXT + HEIC_EXT) and not n.startswith("."):
                    out.append(os.path.join(p, n))
        elif os.path.isfile(p):
            out.append(p)
    if any(p.lower().endswith(HEIC_EXT) for p in out) and not heic_support():
        raise SystemExit(HEIC_HELP)
    return out
