"""Offline self-test: a tiny made-up catalogue and a drawn photo, no network.

Run: python3 lsf.py selftest   (or python3 selftest.py)
It checks the index build, the ID mapping routes, colour handling, ranking, the checklist page
and piece detection. Exit code 0 means every check passed.
"""
import csv
import gzip
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FILES = {
    "themes": [["id", "name", "parent_id"], [504, "Duplo", ""]],
    "colors": [["id", "name", "rgb", "is_trans", "num_parts", "num_sets", "y1", "y2"],
               [0, "Black", "05131D", "False", 1, 1, 2000, 2026], [1, "Blue", "0055BF", "False", 1, 1, 2000, 2026],
               [4, "Red", "C91A09", "False", 1, 1, 2000, 2026], [14, "Yellow", "F2CD37", "False", 1, 1, 2000, 2026],
               [15, "White", "FFFFFF", "False", 1, 1, 2000, 2026], [25, "Orange", "FE8A18", "False", 1, 1, 2000, 2026],
               [47, "Trans-Clear", "FCFCFC", "True", 1, 1, 2000, 2026], [322, "Medium Azure", "36AEBF", "False", 1, 1, 2000, 2026],
               [484, "Dark Orange", "A95500", "False", 1, 1, 2000, 2026], [1062, "Vibrant Yellow", "EBD800", "False", 1, 1, 2000, 2026]],
    "part_categories": [["id", "name"], [4, "Duplo, Quatro and Primo"], [57, "Non-Buildable Figures (Duplo, Fabuland, etc)"]],
    "parts": [["part_num", "name", "part_cat_id", "part_material"],
              ["3437", "Duplo Brick 2 x 2", 4, "Plastic"],
              ["14094", "Duplo Dump Truck Bed 4 x 4 x 2 (Tipper)", 4, "Plastic"],
              ["4066", "Duplo Brick 1 x 2 x 2", 4, "Plastic"],
              ["76371", "Duplo Brick 1 x 2 x 2 with Bottom Tube", 4, "Plastic"],
              ["76371pr0172", "Duplo Brick 1 x 2 x 2 with 'Pizza' and Slice of Pizza Print", 4, "Plastic"],
              ["76371pr0155", "Duplo Brick 1 x 2 x 2 with Windows and Snowflakes Print", 4, "Plastic"],
              ["10111pr0117", "Duplo Figure with Headset and Cap White, with Medium Azure Arms, Orange Safety Vest, and Black Legs", 57, "Plastic"],
              ["18823pr0006", "Duplo Figure Child, Hair Combed Forward with Curl Reddish Brown, Red Legs, White Sleeves and Red Car Print", 57, "Plastic"],
              ["18823pr0020", "Duplo Figure Child, Hair Combed Forward with Curl Reddish Brown, Red Top with Zipper Print, Blue Legs", 57, "Plastic"],
              ["88764pr0003", "Duplo Car Body 2 Top Studs Truck with Cars Tow Mater Print", 4, "Plastic"],
              ["100808", "Curtain, Carwash, Strips", 58, "Plastic"]],
    "part_relationships": [["rel_type", "child_part_num", "parent_part_num"], ["M", "76371", "4066"], ["P", "76371pr0172", "76371"],
                           ["P", "76371pr0155", "76371"]],
    "elements": [["element_id", "part_num", "color_id", "design_id"], ["6301361", "10111pr0117", 25, "35514"]],
    "sets": [["set_num", "name", "year", "theme_id", "num_parts", "img_url"],
             ["90001-1", "Test Truck", 2020, 504, 4, ""], ["90002-1", "Test Pizza Shop", 2020, 504, 4, ""],
             ["90003-1", "Test Ice Castle", 2019, 504, 7, ""], ["90004-1", "Test Car Wash", 2023, 504, 3, ""]],
    "minifigs": [["fig_num", "name", "num_parts", "img_url"], ["fig-900001", "Tow Mater, Duplo", 1, ""]],
    "inventories": [["id", "version", "set_num"], [1, 1, "90001-1"], [2, 1, "90002-1"], [3, 1, "90003-1"], [4, 1, "90004-1"],
                    [5, 1, "fig-900001"], [6, 2, "90001-1"]],
    "inventory_parts": [["inventory_id", "part_num", "color_id", "quantity", "is_spare", "img_url"],
                        [1, "14094", 322, 1, "False", ""], [1, "10111pr0117", 25, 1, "False", ""], [1, "3437", 14, 2, "False", ""],
                        [1, "3437", 0, 1, "True", ""], [6, "3437", 4, 5, "False", ""],
                        [2, "76371pr0172", 15, 1, "False", ""], [2, "18823pr0006", 1, 1, "False", ""], [2, "3437", 4, 2, "False", ""],
                        [3, "76371pr0155", 47, 4, "False", ""], [3, "3437", 15, 3, "False", ""],
                        [4, "3437", 322, 2, "False", ""], [4, "100808", 322, 1, "False", ""],
                        [5, "88764pr0003", 484, 1, "False", ""]],
    "inventory_minifigs": [["inventory_id", "fig_num", "quantity"], [4, "fig-900001", 1]],
}

checks = []


def check(name, ok, detail=""):
    checks.append((name, bool(ok)))
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  (%s)" % detail))


def write_catalogue(src):
    os.makedirs(src, exist_ok=True)
    for name, rows in FILES.items():
        with gzip.open(os.path.join(src, name + ".csv.gz"), "wt", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)


def draw_photo(path):
    from PIL import Image, ImageDraw  # photos.py has already checked that Pillow is there
    rnd = random.Random(1)
    im = Image.new("RGB", (1200, 900), (205, 203, 198))
    px = im.load()
    for _ in range(40000):  # floor-like noise
        x, y = rnd.randrange(1200), rnd.randrange(900)
        v = rnd.randint(-12, 12)
        r, g, b = px[x, y]
        px[x, y] = (r + v, g + v, b + v)
    d = ImageDraw.Draw(im)
    pieces = [((100, 100, 260, 220), (200, 30, 30)), ((420, 120, 520, 300), (240, 200, 40)),
              ((700, 90, 900, 200), (40, 160, 190)), ((150, 520, 330, 700), (30, 30, 30)),
              ((600, 560, 760, 760), (120, 200, 60))]
    for box, col in pieces:
        d.rounded_rectangle(box, radius=12, fill=col)
    # metadata a phone photo carries: camera, GPS position, an XMP packet and a JPEG comment
    from PIL import ExifTags
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = "TestCamMake", "TestCamModel"
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[1], gps[2], gps[3], gps[4] = "N", (54.0, 41.0, 12.5), "E", (25.0, 16.0, 47.0)
    xmp = b'<?xpacket begin=""?><x:xmpmeta xmlns:x="adobe:ns:meta/">TestXmpPacket</x:xmpmeta><?xpacket end="w"?>'
    im.save(path, quality=92, exif=exif, xmp=xmp, comment=b"TestJpegComment")
    return len(pieces)


META_MARKERS = (b"TestCamMake", b"TestCamModel", b"TestXmpPacket", b"TestJpegComment", b"ns.adobe.com/xap")


def main():
    tmp = tempfile.mkdtemp(prefix="lsf-selftest-")
    try:
        return run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run(tmp):
    import mapping
    import photos
    import rank
    import rbdata

    data = os.path.join(tmp, "data")
    src = os.path.join(tmp, "csv")
    write_catalogue(src)
    # hand-downloaded files: a missing one is named, and an unzipped .csv is accepted
    os.rename(os.path.join(src, "elements.csv.gz"), os.path.join(tmp, "elements.csv.gz"))
    try:
        rbdata.import_local(data, src, log=lambda *a: None)
        msg = ""
    except SystemExit as e:
        msg = str(e)
    check("data: a missing file is named", "elements.csv.gz" in msg and "minifigs" not in msg, msg)
    with gzip.open(os.path.join(tmp, "elements.csv.gz"), "rb") as a, open(os.path.join(src, "elements.csv"), "wb") as b:
        b.write(a.read())
    rbdata.import_local(data, src, log=lambda *a: None)
    rbdata.build_index(data, log=lambda *a: None)
    con = rbdata.connect(data)

    sets = [r[0] for r in con.execute("SELECT set_num FROM part_sets WHERE part_num='14094' AND color_id=322")]
    check("index: part in colour -> sets", sets == ["90001-1"], sets)
    spare = con.execute("SELECT COUNT(*) FROM part_sets WHERE part_num='3437' AND color_id=0").fetchone()[0]
    check("index: spare parts left out", spare == 0)
    v2 = con.execute("SELECT COUNT(*) FROM part_sets WHERE set_num='90001-1' AND color_id=4").fetchone()[0]
    check("index: lowest inventory version only", v2 == 0)
    via = [tuple(r) for r in con.execute("SELECT set_num FROM part_sets WHERE part_num='88764pr0003'")]
    check("index: parts inside minifigs reach the set", via == [("90004-1",)], via)

    m = mapping.Mapper(con)
    r = m.map_item({"id": "14094", "name": "Duplo Tipper Bucket Bed with Lip and Cutout", "type": "part"})
    check("map: same number", r["method"] == "same-number" and r["candidates"][0]["num"] == "14094", r)
    r = m.map_item({"id": "4066pb718", "name": "Duplo, Brick 1 x 2 x 2 with Pizza Pattern", "type": "part"})
    check("map: print via a related mould", r["candidates"] and r["candidates"][0]["num"] == "76371pr0172", r)
    r = m.map_item({"id": "47394pb288", "type": "fig",
                    "name": "Duplo Figure Lego Ville, Male, Black Legs, Orange Vest, White Cap with Headset (6301361)"})
    check("map: element number in the name", r["method"] == "element" and r["candidates"][0]["num"] == "10111pr0117", r)
    r = m.map_item({"id": "47205pb031", "type": "fig",
                    "name": "Duplo Figure Lego Ville, Child Boy, Red Legs, Blue Top with Red Car Pattern, Reddish Brown Hair"})
    check("map: Duplo figure by description", r["candidates"] and r["candidates"][0]["num"] == "18823pr0006", r)
    check("colour: BrickLink name", m.rb_color("Medium Azure") == 322)
    check("colour: alias (Neon Yellow)", m.rb_color("Neon Yellow") == 1062)
    cand = m._cand(("part", "14094"), "")
    cid, how = m.choose_color(cand, [{"name": "Red", "score": 0.6}, {"name": "Medium Azure", "score": 0.3}])
    check("colour: first guess the part exists in", cid == 322 and how == "brickognize-2", (cid, how))
    cid, how = m.choose_color(cand, [{"name": "Red", "score": 0.9}])
    check("colour: unknown for this part -> any", cid is None and how == "not-in-catalogue", (cid, how))

    rows = [{"crop": "a", "use": "y", "rb": "14094", "conf": "high", "colour": "Medium Azure"},
            {"crop": "b", "use": "y", "rb": "10111pr0117", "conf": "high", "colour": "figure"},
            {"crop": "c", "use": "y", "rb": "76371pr0172", "conf": "high", "colour": "White"},
            {"crop": "f", "use": "y", "rb": "18823pr0006", "conf": "medium", "colour": "figure"},
            {"crop": "d", "use": "n", "rb": "76371pr0155", "conf": "high", "colour": "Trans-Clear"},
            {"crop": "e", "use": "y", "rb": "3437", "conf": "high", "colour": "Medium Azure"}]
    ev, skipped = rank.build_evidence(con, m, rows)
    chosen, unexplained, common, nowhere = rank.rank(con, ev, m, top=5)
    order = [s["set_num"] for s in chosen]
    check("rank: best supported set first", order[:2] == ["90001-1", "90002-1"], order)
    check("rank: unused rows ignored", "90003-1" not in order, order)
    items = rank.inventory(con, m, "90004-1")
    check("inventory: minifig listed as one item", any(i["key"] == "fig|fig-900001" for i in items), items)

    photo = os.path.join(tmp, "photo.jpg")
    n = draw_photo(photo)
    boxes, info = photos.detect(photo)
    check("detect: one box per piece on a plain floor", len(boxes) == n, "%d boxes for %d pieces" % (len(boxes), n))
    crops = photos.crop(photo, [(str(i), b, False) for i, b in enumerate(boxes, 1)], os.path.join(tmp, "crops"), "p")
    check("crop: files written", len(crops) == len(boxes))
    from PIL import Image, ExifTags
    src_im = Image.open(photo)
    planted = (b"TestJpegComment" in open(photo, "rb").read() and "xmp" in src_im.info
               and src_im.getexif().get_ifd(ExifTags.IFD.GPSInfo))
    check("crop: the test photo carries EXIF with GPS, XMP and a comment", planted)
    leaks = []
    for c in crops:
        raw = open(c, "rb").read()
        ci = Image.open(c)
        if len(ci.getexif()) or any(k in ci.info for k in ("exif", "xmp", "comment", "icc_profile")) \
                or any(m in raw for m in META_MARKERS):
            leaks.append(os.path.basename(c))
    check("crop: no EXIF, GPS, XMP, colour profile or comment in any crop", crops and not leaks, leaks)
    refused = []
    whole = photos.crop(photo, [("big", [0, 0, 1200, 900], True)], os.path.join(tmp, "crops"), "q", refused=refused.append)
    check("crop: a box over most of the photo is refused", not whole and refused == ["big"], (whole, refused))

    # CLI end to end on the made-up run: rank, page, missing
    run_dir = os.path.join(tmp, "run")
    os.makedirs(run_dir)
    cols = ["crop", "use", "bl_id", "bl_name", "bk_score", "rb", "rb_name", "conf", "method", "colour", "colour_how",
            "alternatives", "note", "answer"]
    with open(os.path.join(run_dir, "pieces.tsv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t", extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    lsf = [sys.executable, os.path.join(HERE, "lsf.py"), "--data", data]
    ok = subprocess.run(lsf + ["data"], capture_output=True, text=True)
    check("cli: data without --from lists the files and exits 1",
          ok.returncode == 1 and "inventory_parts.csv.gz" in ok.stdout and rbdata.DOWNLOAD_PAGE in ok.stdout,
          (ok.returncode, ok.stdout[-200:]))
    ok = subprocess.run(lsf + ["rank", run_dir], capture_output=True, text=True)
    check("cli: rank", ok.returncode == 0 and os.path.exists(os.path.join(run_dir, "sets.json")), ok.stderr[-300:])
    ok = subprocess.run(lsf + ["page", run_dir, "--sets", "90001,90004"], capture_output=True, text=True)
    page = os.path.join(run_dir, "checklist.html")
    html_ok = os.path.exists(page) and '"no": "90001"' in open(page, encoding="utf-8").read()
    check("cli: page", ok.returncode == 0 and html_ok, ok.stderr[-300:])
    if html_ok:
        text = open(page, encoding="utf-8").read()
        blob = text.split('<script id="data" type="application/json">', 1)[1].split("</script>", 1)[0]
        keys = {it["k"] for it in json.loads(blob)["sets"][1]["items"]}
        data_ok = {"3437|322", "100808|322", "fig|fig-900001"} <= keys
        check("page: embedded data parses", data_ok)
    ticks = os.path.join(tmp, "ticks.json")
    with open(ticks, "w", encoding="utf-8") as f:
        json.dump({"found": {"90001": {"14094|322": 1}}}, f)
    ok = subprocess.run(lsf + ["missing", run_dir, "--sets", "90001", "--ticks", ticks], capture_output=True, text=True)
    check("cli: missing from saved ticks", ok.returncode == 0 and "3 of 4 pieces missing" in ok.stdout, ok.stdout + ok.stderr)

    ok = subprocess.run(lsf + ["missing", run_dir, "--sets", "90003"], capture_output=True, text=True)
    check("cli: missing points to a left-out crop that fits", ok.returncode == 0 and "You left out crop d" in ok.stdout,
          ok.stdout[-300:] + ok.stderr[-300:])
    ok = subprocess.run(lsf + ["lookup", "car", "wash", "--duplo"], capture_output=True, text=True)
    check("cli: lookup ignores spaces and finds parts of Duplo sets by membership",
          ok.returncode == 0 and "100808" in ok.stdout, ok.stdout[-300:] + ok.stderr[-300:])

    failed = [c for c, good in checks if not good]
    print("\n%d checks, %d failed" % (len(checks), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
