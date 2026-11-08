#!/usr/bin/env python3
"""lego-set-finder: find which LEGO sets a pile of pieces came from.

Steps (each reads and writes files in one run folder):
  data       index the Rebrickable catalogue files the user downloaded by hand (about 17 MB)
  detect     find pieces in photos, write boxes.tsv and overview images with a grid
  boxes      keep, drop or add boxes by hand (when detection merges or misses pieces)
  zoom       a region of an overview at twice the size, to place boxes precisely
  overview   redraw the overviews after editing boxes.tsv
  crop       cut one image per kept box, write contact sheets
  identify   send each crop to Brickognize (cached, polite, capped)
  match      map Brickognize answers to Rebrickable parts and colours, write pieces.tsv
  rank       rank sets by independent distinctive pieces, write sets.md and sets.json
  page       build checklist.html for chosen sets
  missing    list missing parts per set (from saved ticks, or what the photos did not show)
Helpers: fix (correct one row of pieces.tsv), lookup (search the catalogue), sets-of (sets holding
a part), set (one set's parts), selftest (offline check of the install).

Run "python3 lsf.py <step> -h" for the options of a step.
"""
import argparse
import csv
import datetime
import hashlib
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import rbdata  # noqa: E402

BOX_COLS = ["photo", "box", "x0", "y0", "x1", "y1", "keep", "source", "note"]
PIECE_COLS = ["crop", "use", "bl_id", "bl_name", "bk_score", "rb", "rb_name", "conf", "method",
              "colour", "colour_how", "alternatives", "note", "answer"]


# ---- small file helpers ---------------------------------------------------------------------------
def read_tsv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f, delimiter="\t") if any((v or "").strip() for v in r.values())]


def write_tsv(path, cols, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t", extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    os.replace(tmp, path)


def load_run(run):
    p = os.path.join(run, "run.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"photos": {}, "created": datetime.date.today().isoformat()}


def save_run(run, meta):
    with open(os.path.join(run, "run.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)


def photo_id(path, taken):
    base = re.sub(r"[^A-Za-z0-9_]+", "_", os.path.splitext(os.path.basename(path))[0]).strip("_") or "photo"
    pid, n = base, 2
    while pid in taken and taken[pid] != os.path.abspath(path):
        pid = "%s_%d" % (base, n)
        n += 1
    return pid


def data_dir(args):
    return os.path.abspath(os.path.expanduser(args.data)) if args.data else rbdata.default_data_dir()


def open_db(args):
    return rbdata.connect(data_dir(args))


def keep(row):
    return (row.get("keep") or "y").strip().lower() in ("y", "yes", "1")


# ---- data ---------------------------------------------------------------------------------------
def cmd_data(args):
    d = data_dir(args)
    installed = os.path.exists(rbdata.db_path(d))
    if args.info:
        if not installed:
            sys.exit("No catalogue installed in %s. Run: lsf.py data" % d)
        print(json.dumps(rbdata.info(d), indent=1))
        return
    if args.from_dir:
        rbdata.import_local(d, os.path.abspath(os.path.expanduser(args.from_dir)))
    elif not args.rebuild:
        if installed:
            print("A catalogue is installed in %s (built %s).\n" % (d, rbdata.info(d)["built"]))
        print(rbdata.instructions())
        sys.exit(1)
    rbdata.build_index(d)
    print(json.dumps(rbdata.info(d), indent=1))


# ---- detect, overview, crop ---------------------------------------------------------------------
def cmd_detect(args):
    import photos
    os.makedirs(args.run, exist_ok=True)
    meta = load_run(args.run)
    paths = photos.list_photos(args.photos)
    if not paths:
        sys.exit("No photos found in: %s" % " ".join(args.photos))
    boxes = read_tsv(os.path.join(args.run, "boxes.tsv"))
    os.makedirs(os.path.join(args.run, "overview"), exist_ok=True)
    for p in paths:
        pid = photo_id(p, meta["photos"])
        meta["photos"][pid] = os.path.abspath(p)
        found, info = photos.detect(p, threshold=args.threshold, min_area=args.min_area)
        boxes = [b for b in boxes if not (b["photo"] == pid and b.get("source") == "auto")]
        for i, b in enumerate(found, 1):
            boxes.append({"photo": pid, "box": str(i), "x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3],
                          "keep": "y", "source": "auto", "note": ""})
        draw_overview(args.run, meta, boxes, pid)
        print("%-16s %3d pieces found (threshold %d)  overview: %s" % (
            pid, len(found), info["threshold"], os.path.join(args.run, "overview", pid + ".jpg")))
    write_tsv(os.path.join(args.run, "boxes.tsv"), BOX_COLS, boxes)
    save_run(args.run, meta)
    print("Boxes: %s. Coordinates are pixels of the overview images." % os.path.join(args.run, "boxes.tsv"))


def draw_overview(run, meta, boxes, pid):
    import photos
    rows = [b for b in boxes if b["photo"] == pid and keep(b)]
    coords = [[int(float(b[k])) for k in ("x0", "y0", "x1", "y1")] for b in rows]
    colors = [(0, 200, 255) if b.get("source") == "manual" else (255, 0, 200) for b in rows]
    photos.draw_overview(meta["photos"][pid], coords, os.path.join(run, "overview", pid + ".jpg"),
                         labels=[b["box"] for b in rows], colors=colors)


def cmd_zoom(args):
    import photos
    meta = load_run(args.run)
    if args.photo not in meta["photos"]:
        sys.exit("Unknown photo %r. Photos in this run: %s" % (args.photo, ", ".join(sorted(meta["photos"]))))
    try:
        region = [int(round(float(v))) for v in args.region.split(",")]
        assert len(region) == 4 and region[2] > region[0] and region[3] > region[1]
    except (ValueError, AssertionError):
        sys.exit("The region is x0,y0,x1,y1 in overview pixels, for example 300,200,600,500")
    rows = [b for b in read_tsv(os.path.join(args.run, "boxes.tsv")) if b["photo"] == args.photo and keep(b)]
    os.makedirs(os.path.join(args.run, "overview"), exist_ok=True)
    out = os.path.join(args.run, "overview", "%s-zoom.jpg" % args.photo)
    photos.draw_zoom(meta["photos"][args.photo], region, out, scale=args.scale,
                     boxes=[[int(float(b[k])) for k in ("x0", "y0", "x1", "y1")] for b in rows],
                     labels=[b["box"] for b in rows],
                     colors=[(0, 200, 255) if b.get("source") == "manual" else (255, 0, 200) for b in rows])
    print("Zoom: %s (overview coordinates, grid every 50 px)" % out)


def cmd_overview(args):
    meta = load_run(args.run)
    boxes = read_tsv(os.path.join(args.run, "boxes.tsv"))
    os.makedirs(os.path.join(args.run, "overview"), exist_ok=True)
    for pid in args.photo or sorted(meta["photos"]):
        draw_overview(args.run, meta, boxes, pid)
        print(os.path.join(args.run, "overview", pid + ".jpg"))


def cmd_boxes(args):
    meta = load_run(args.run)
    if args.photo not in meta["photos"]:
        sys.exit("Unknown photo %r. Photos in this run: %s" % (args.photo, ", ".join(sorted(meta["photos"]))))
    path = os.path.join(args.run, "boxes.tsv")
    boxes = read_tsv(path)
    mine = [b for b in boxes if b["photo"] == args.photo]
    ids = {b["box"] for b in mine}
    if args.clear_manual:
        boxes = [b for b in boxes if not (b["photo"] == args.photo and b.get("source") == "manual")]
    for flag, val in (("n", args.drop), ("y", args.keep)):
        for i in [x.strip() for x in (val or "").split(",") if x.strip()]:
            if i not in ids:
                sys.exit("No box %s in %s" % (i, args.photo))
            for b in boxes:
                if b["photo"] == args.photo and b["box"] == i:
                    b["keep"] = flag
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        for b in boxes:
            if b["photo"] == args.photo and b.get("source") == "auto":
                b["keep"] = "y" if b["box"] in want else "n"
    n = 1 + max([int(b["box"][1:]) for b in boxes if b["photo"] == args.photo and re.match(r"^m\d+$", b["box"])] or [0])
    for spec in args.add or []:
        coords, _, note = spec.partition(":")
        try:
            x0, y0, x1, y1 = [int(round(float(v))) for v in coords.split(",")]
        except ValueError:
            sys.exit("--add takes x0,y0,x1,y1[:note], got %r" % spec)
        if x1 <= x0 or y1 <= y0:
            sys.exit("--add box %r: x1 must be > x0 and y1 > y0" % spec)
        boxes.append({"photo": args.photo, "box": "m%d" % n, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                      "keep": "y", "source": "manual", "note": note})
        n += 1
    write_tsv(path, BOX_COLS, boxes)
    draw_overview(args.run, meta, boxes, args.photo)
    rows = [b for b in boxes if b["photo"] == args.photo]
    print("%s: %d boxes kept of %d. Overview: %s" % (args.photo, sum(1 for b in rows if keep(b)), len(rows),
                                                   os.path.join(args.run, "overview", args.photo + ".jpg")))


def cmd_crop(args):
    import photos
    meta = load_run(args.run)
    boxes = read_tsv(os.path.join(args.run, "boxes.tsv"))
    cdir = os.path.join(args.run, "crops")
    sdir = os.path.join(args.run, "sheets")
    os.makedirs(sdir, exist_ok=True)
    wanted = set()
    for pid in sorted(meta["photos"]):
        rows = [b for b in boxes if b["photo"] == pid and keep(b)]
        spec = [(b["box"], [float(b[k]) for k in ("x0", "y0", "x1", "y1")], b.get("source") == "manual") for b in rows]
        paths = photos.crop(meta["photos"][pid], spec, cdir, pid,
                            refused=lambda box: print("  %s box %s covers most of the photo: not cut (it would send the whole "
                                                      "photo to Brickognize). Draw a box around one piece." % (pid, box)))
        wanted |= {os.path.basename(p) for p in paths}
        sheet = os.path.join(sdir, "crops_%s.jpg" % pid)
        photos.contact_sheet([(p, [os.path.splitext(os.path.basename(p))[0]]) for p in paths], sheet, lines=1)
        print("%-16s %3d crops  sheet: %s" % (pid, len(paths), sheet))
    for n in os.listdir(cdir) if os.path.isdir(cdir) else []:
        if n.endswith(".jpg") and n not in wanted:
            os.remove(os.path.join(cdir, n))


# ---- identify ------------------------------------------------------------------------------------
def crop_list(run):
    cdir = os.path.join(run, "crops")
    if not os.path.isdir(cdir):
        sys.exit("No crops yet. Run: lsf.py crop %s" % run)
    return [os.path.join(cdir, n) for n in sorted(os.listdir(cdir)) if n.endswith(".jpg")]


def cmd_identify(args):
    import brickognize
    crops = crop_list(args.run)
    if args.only:
        only = set(args.only.split(","))
        crops = [c for c in crops if os.path.splitext(os.path.basename(c))[0] in only]
    path = os.path.join(args.run, "identify.json")
    results = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            results = json.load(f)
    cache = os.path.join(data_dir(args), "brickognize-cache")
    client = brickognize.Client(cache, max_calls=args.max_calls, workers=args.workers, endpoint=args.endpoint)
    names = {c: os.path.splitext(os.path.basename(c))[0] for c in crops}
    done = {} if args.again else {c: results[names[c]]["response"] for c in crops
                                   if names[c] in results and results[names[c]].get("endpoint") == args.endpoint}
    print("Sending %d crops to Brickognize (%s), %d already done" % (len(crops), args.endpoint, len(done)))
    out, stopped = client.predict_many(crops, done)
    for c, resp in out.items():
        results[names[c]] = {"file": os.path.relpath(c, args.run), "endpoint": args.endpoint, "response": resp}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=0)
    empty = sorted(n for n, r in results.items() if not r["response"].get("items"))
    print("New Brickognize calls: %d, from cache: %d. Results: %s" % (client.calls, client.cached, path))
    if empty:
        print("No match for %d crops: %s" % (len(empty), ", ".join(empty)))
    if stopped:
        print("STOPPED at the call limit (%d). Ask the user before going on, then rerun with a higher --max-calls."
              % args.max_calls)
        sys.exit(2)


# ---- match ---------------------------------------------------------------------------------------
def short(name, n=60):
    name = re.sub(r"\s*\([\d\s/]+\)", "", name or "")
    name = re.sub(r"^Duplo,?\s+(Figure Lego Ville,\s*)?", "", name)
    return name if len(name) <= n else name[:n - 1].rstrip(" ,") + "\u2026"


def answer_sig(result):
    """Short signature of a Brickognize answer, so match knows when a crop was identified again."""
    items = result["response"].get("items") or []
    return "%s:%s" % (result.get("endpoint", ""), items[0].get("id") if items else "-")


def is_duplo(item):
    text = ((item.get("name") or "") + " " + (item.get("category") or "")).lower()
    return "duplo" in text


DOWNGRADE = {"high": "medium", "medium": "low", "low": "low", "none": "none"}


def cmd_match(args):
    import mapping
    import photos
    con = open_db(args)
    mapper = mapping.Mapper(con)
    path = os.path.join(args.run, "identify.json")
    if not os.path.exists(path):
        sys.exit("No identify.json. Run: lsf.py identify %s" % args.run)
    with open(path, encoding="utf-8") as f:
        results = json.load(f)
    meta = load_run(args.run)
    if args.kind:
        meta["kind"] = args.kind
        save_run(args.run, meta)
    kind = meta.get("kind", "any")
    ppath = os.path.join(args.run, "pieces.tsv")
    old = {} if args.reset else {r["crop"]: r for r in read_tsv(ppath)}
    redo = set((args.redo or "").split(","))
    rows = []
    for crop in sorted(results):
        o = old.get(crop)
        if o and o.get("method") != "no-answer" and crop not in redo and o.get("answer") == answer_sig(results[crop]):
            rows.append(o)
            continue
        resp = results[crop]["response"]
        items = resp.get("items") or []
        row = {"crop": crop, "use": "y", "note": "", "answer": answer_sig(results[crop])}
        if not items:
            row.update({"use": "n", "conf": "none", "method": "no-answer",
                        "note": resp.get("error") or "Brickognize found nothing: recrop tighter or drop"})
            rows.append(row)
            continue
        top = items[0]
        if kind == "duplo" and not is_duplo(top):
            better = [i for i in items[1:] if is_duplo(i) and i.get("score", 0) >= top.get("score", 0) - 0.15]
            if better:
                row["note"] = "took Brickognize's best Duplo answer over %s" % top.get("id")
                top = better[0]
        row.update({"bl_id": top.get("id", ""), "bl_name": top.get("name", ""), "bk_score": "%.2f" % top.get("score", 0),
                    "alternatives": "; ".join("%s %.2f" % (i.get("id"), i.get("score", 0)) for i in items[1:4])})
        fill_mapping(row, mapper, top, resp.get("colors") or [])
        if kind == "duplo" and not is_duplo(top) and row.get("conf") in ("high", "medium"):
            row["conf"] = "low"
            row["note"] = (row.get("note", "") + " not a Duplo part: check by eye").strip()
        rows.append(row)
    write_tsv(ppath, PIECE_COLS, rows)
    # review sheets: crop + what it was taken for
    sdir = os.path.join(args.run, "sheets")
    os.makedirs(sdir, exist_ok=True)
    by_photo = {}
    for r in rows:
        by_photo.setdefault(r["crop"].rsplit("-", 1)[0], []).append(r)
    for pid, rs in sorted(by_photo.items()):
        items = []
        for r in rs:
            f = os.path.join(args.run, "crops", r["crop"] + ".jpg")
            items.append((f, ["%s %s %s" % (r["crop"], r.get("conf", ""), r.get("bk_score", "")),
                              short(r.get("bl_name", "") or r.get("note", ""), 34),
                              "%s %s" % ((r.get("rb") or "-").split(";")[0], r.get("colour", ""))]))
        photos.contact_sheet(items, os.path.join(sdir, "review_%s.jpg" % pid), lines=3)
    counts = {}
    for r in rows:
        counts[r.get("conf", "?")] = counts.get(r.get("conf", "?"), 0) + 1
    print("pieces.tsv: %d rows %s" % (len(rows), " ".join("%s=%d" % kv for kv in sorted(counts.items()))))
    print("Review sheets: %s/review_<photo>.jpg" % sdir)
    look = [r["crop"] for r in rows if r.get("conf") in ("low", "none") or r.get("colour_how") == "not-in-catalogue"
            or (r.get("colour") == "figure" and r.get("use") == "y")]
    if look:
        print("Check these by eye first (every figure is on this list): %s" % ", ".join(look))
    if old and not args.reset:
        print("Kept %d earlier rows as they were (use --reset to rebuild them all)." % sum(1 for r in rows if r["crop"] in old))


def fill_mapping(row, mapper, item, bk_colors):
    m = mapper.map_item(item)
    cands = mapper.narrow_by_color(m["candidates"], bk_colors)
    row["method"], row["conf"] = m["method"], m["confidence"]
    if len(cands) < len(m["candidates"]):
        row["method"] += "+colour"
    row["rb"] = ";".join(c["num"] for c in cands)
    row["rb_name"] = cands[0]["name"] if cands else ""
    if not cands:
        row["use"] = "n"
        row["note"] = "no Rebrickable match: fix rb by hand (lsf.py lookup) or drop"
        row["colour"], row["colour_how"] = "", ""
        return
    cid, how = mapper.choose_color(cands[0], bk_colors)
    if cid is not None and len(cands) > 1:
        # keep the colour only if every candidate exists in it
        if not all(c["figure"] or cid in mapper.colors_of(c["num"]) for c in cands if c["kind"] == "part"):
            how += "+not-all-candidates"
    row["colour"] = mapper.color_names.get(cid, "") if cid is not None else ("figure" if how == "figure" else "any")
    row["colour_how"] = how
    if how == "not-in-catalogue":  # Brickognize saw a colour this part never comes in: doubt the match
        row["conf"] = DOWNGRADE.get(row["conf"], row["conf"])
    if bk_colors:
        row["colour_how"] += " (bk: %s)" % ", ".join("%s %.2f" % (c.get("name"), c.get("score", 0)) for c in bk_colors[:2])


# ---- rank ----------------------------------------------------------------------------------------
def load_evidence(args, con, mapper):
    import rank
    rows = read_tsv(os.path.join(args.run, "pieces.tsv"))
    if not rows:
        sys.exit("No pieces.tsv. Run: lsf.py match %s" % args.run)
    for r in rows:  # rows the agent cleared get mapped again from bl_id
        if r.get("use", "y").lower().startswith("y") and not (r.get("rb") or "").strip() and r.get("bl_id"):
            kind = "fig" if "figure" in (r.get("bl_name") or "").lower() else "part"
            fill_mapping(r, mapper, {"id": r["bl_id"], "name": r.get("bl_name", ""), "type": kind}, [])
    return rank.build_evidence(con, mapper, rows)


def cmd_rank(args):
    import mapping
    import rank
    con = open_db(args)
    mapper = mapping.Mapper(con)
    evidence, skipped = load_evidence(args, con, mapper)
    chosen, unexplained, common, nowhere = rank.rank(con, evidence, mapper, top=args.top)
    out = {"made": datetime.date.today().isoformat(), "sets": chosen, "unexplained": unexplained,
           "common": common, "nowhere": nowhere, "skipped": [{"crop": r.get("crop"), "why": w} for r, w in skipped]}
    with open(os.path.join(args.run, "sets.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    md = sets_markdown(out, len(evidence))
    with open(os.path.join(args.run, "sets.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md.split("\n## Evidence")[0])
    print("Full evidence: %s" % os.path.join(args.run, "sets.md"))


def sets_markdown(out, n_pieces):
    L = ["# Likely sets", "",
         "From %d distinct photographed pieces. Evidence: weight of what this set adds on its own (a piece "
         "found in n sets weighs 1/sqrt(n)). Seen: the set's distinctive items found in the photos. Rank "
         "score: evidence weighed by that share. Pieces: how many point to the set; only here: pieces no "
         "other set has." % n_pieces, "",
         "| # | Set | Name | Year | Tier | Rank score | Evidence | Seen | Pieces | Only here | Shares evidence with |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, s in enumerate(out["sets"], 1):
        L.append("| %d | %s | %s | %s | %s | %.2f | %.2f | %d/%d | %d | %d | %s |" % (
            i, s["set_num"], s["name"], s["year"] or "", s["tier"], s["rank_score"], s["score"],
            s["seen_items"], s["distinctive_items"], s["pieces"], s["only_here"], ", ".join(s["shares"]) or ""))
    L += ["", "## Evidence", ""]
    for i, s in enumerate(out["sets"], 1):
        L.append("### %d. %s %s (%s)" % (i, s["set_num"], s["name"], s["tier"]))
        if s["shares"]:
            L.append("Shares its evidence with %s: check which one you have." % ", ".join(s["shares"]))
        for e in sorted(s["evidence"], key=lambda e: (not e["new"], e["n_sets"])):
            L.append("- %s: %s, %s, in %d set%s%s" % (
                e["crop"], short(e["label"], 80), e["colour"], e["n_sets"], "" if e["n_sets"] == 1 else "s",
                "" if e["new"] else " (also explained by a set above)"))
        L.append("")
    if out["unexplained"]:
        L += ["## Distinctive pieces no listed set explains", ""]
        L += ["- %s: %s, %s, in %d sets" % (e["crop"], short(e["label"], 80), e["colour"], e["n_sets"]) for e in out["unexplained"]]
        L.append("")
    if out["nowhere"]:
        L += ["## Pieces in no set of the catalogue (wrong colour or wrong match?)", ""]
        L += ["- %s: %s, %s" % (e["crop"], short(e["label"], 80), e["colour"]) for e in out["nowhere"]]
        L.append("")
    if out["common"]:
        L += ["## Common pieces (in more than 40 sets, not counted)", ""]
        L.append(", ".join("%s %s" % (e["crop"], short(e["label"], 40)) for e in out["common"]))
        L.append("")
    return "\n".join(L)


# ---- page and missing ----------------------------------------------------------------------------
def norm_set(s):
    s = s.strip()
    return s if "-" in s else s + "-1"


def chosen_sets(args):
    with open(os.path.join(args.run, "sets.json"), encoding="utf-8") as f:
        ranked = json.load(f)
    by = {s["set_num"]: s for s in ranked["sets"]}
    if args.sets:
        nums = [norm_set(s) for s in args.sets.split(",") if s.strip()]
    else:
        tiers = set(args.tiers.split(","))
        nums = [s["set_num"] for s in ranked["sets"] if s["tier"] in tiers]
    return nums, by, ranked


def cmd_page(args):
    import mapping
    import rank
    con = open_db(args)
    mapper = mapping.Mapper(con)
    nums, by, _ranked = chosen_sets(args)
    if not nums:
        sys.exit("No sets chosen. Pass --sets 10931,10906 or --tiers likely,possible,weak")
    evidence, _ = load_evidence(args, con, mapper)
    seen = rank.seen_keys(evidence)
    sets = []
    for num in nums:
        info = by.get(num) or rank.set_info(con, num)
        items = rank.inventory(con, mapper, num)
        if not items:
            print("No inventory for %s, skipped" % num)
            continue
        ev = sorted(info.get("evidence", []), key=lambda e: (not e["new"], e["n_sets"]))
        why = ""
        if ev:
            bits = ["%s (%s)" % (short(e["label"], 70), "only in this set" if e["n_sets"] == 1 else "in %d sets" % e["n_sets"])
                    for e in ev[:6]]
            why = "Seen in your photos: " + "; ".join(bits) + ("; and %d more." % (len(ev) - 6) if len(ev) > 6 else ".")
        tier = info.get("tier") or "confirmed"
        if args.confirmed:
            tier = "confirmed"
        base = num.split("-")[0]
        sets.append({
            "no": base if num.endswith("-1") else num, "name": info.get("name", "?"), "year": info.get("year"), "tier": tier,
            "why": why, "links": [["Rebrickable", "https://rebrickable.com/sets/%s/" % num],
                                  ["LEGO instructions", "https://www.lego.com/service/building-instructions/%s" % base]],
            "items": [{"k": it["key"], "num": it["num"], "name": it["name"], "colour": it["colour"], "rgb": it["rgb"],
                       "qty": it["qty"], "img": it["img"], "group": it["group"],
                       "seen": rank.is_seen(it, seen, mapper)} for it in items],
        })
    built = rbdata.info(data_dir(args))
    page_id = hashlib.sha1(",".join(sorted(s["no"] for s in sets)).encode()).hexdigest()[:10]
    total = sum(it["qty"] for s in sets for it in s["items"])
    data = {
        "id": page_id, "title": args.title,
        "intro": "%d sets, %d pieces. Tap a part's count each time you put one in its set's pile. "
                 "Ticks save in this browser. \"In photos\" marks parts that match a piece you photographed." % (len(sets), total),
        "foot": ["Parts lists and pictures come from Rebrickable (rebrickable.com), catalogue data of %s. "
                 "Pieces were recognised with Brickognize (brickognize.com)." % built.get("built"),
                 "Plain bricks and plates are interchangeable: take any of the right size and colour.",
                 "LEGO® is a trademark of the LEGO Group, which does not sponsor, authorize or endorse this page."],
        "sets": sets,
    }
    with open(os.path.join(HERE, "checklist_template.html"), encoding="utf-8") as f:
        tpl = f.read()
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = tpl.replace("__TITLE__", html.escape(args.title)).replace("__DATA__", blob)
    out = args.out or os.path.join(args.run, "checklist.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print("Checklist: %s (%d sets, %d pieces). Open it in a browser." % (out, len(sets), total))


def cmd_missing(args):
    import mapping
    import rank
    con = open_db(args)
    mapper = mapping.Mapper(con)
    nums, by, _ = chosen_sets(args)
    ticks = None
    if args.ticks:
        with open(args.ticks, encoding="utf-8") as f:
            j = json.load(f)
        ticks = j.get("found", j)
    evidence, _ = load_evidence(args, con, mapper) if not ticks else ([], [])
    seen = rank.seen_keys(evidence)
    left_out = [r for r in read_tsv(os.path.join(args.run, "pieces.tsv"))
                if r.get("use", "y").lower().startswith("n") and (r.get("rb") or "").strip()]
    nameless = [r["crop"] for r in read_tsv(os.path.join(args.run, "pieces.tsv"))
                if r.get("use", "y").lower().startswith("n") and not (r.get("rb") or "").strip()]

    def left_out_for(items):
        """Crops set to use = n whose Rebrickable part matches one of these items."""
        hits = []
        for r in left_out:
            cands = rank.split_list(r["rb"])
            fams = set()
            for c in cands:
                fams |= {c} if c.startswith("fig-") else mapper.family(c)
            cname = (r.get("colour") or "").strip().lower()
            rcid = None if cname in ("", "any", "figure", "-", "?") else mapper.rb_color(r["colour"])
            for it in items:
                if it["num"] not in fams:
                    continue
                icid = None if it["key"].startswith("fig|") else int(it["key"].split("|")[1])
                if rcid is None or icid is None or rcid == icid:
                    hits.append((r, it))
                    break
        return hits
    for num in nums:
        info = by.get(num) or rank.set_info(con, num)
        items = rank.inventory(con, mapper, num)
        no = num.split("-")[0] if num.endswith("-1") else num
        need = sum(it["qty"] for it in items)
        if ticks is not None:
            got = ticks.get(no, {})
            miss = [(it, it["qty"] - min(it["qty"], got.get(it["key"], 0))) for it in items]
            miss = [(it, n) for it, n in miss if n > 0]
            print("%s %s: %s" % (num, info.get("name"), "%d of %d pieces missing" % (sum(n for _, n in miss), need) if miss else "complete"))
            for it, n in miss:
                print("  %d x %s (%s) [%s]" % (n, it["name"], it["colour"] or "figure", it["num"]))
            gaps = [it for it, _ in miss]
        else:
            dist = [it for it in items if rank.distinctive(it)]
            not_seen = [it for it in dist if not rank.is_seen(it, seen, mapper)]
            print("%s %s: %d of %d distinctive items not in the photos (%d pieces in the set)" % (
                num, info.get("name"), len(not_seen), len(dist), need))
            for it in not_seen:
                print("  %d x %s (%s) [%s]" % (it["qty"], it["name"], it["colour"] or "figure", it["num"]))
            gaps = not_seen
        for r, it in left_out_for(gaps):
            print("  You left out crop %s (%s %s), which matches %s [%s]: look at it again, and if it fits, "
                  "lsf.py fix %s %s --use y" % (r["crop"], r["rb"], r.get("colour", ""), it["name"], it["num"],
                                                 args.run, r["crop"]))
        print()
    if nameless:
        print("Crops left out without any identification (%s) may also be missing parts: compare them by eye "
              "with the lists above." % ", ".join(nameless))
    if ticks is None:
        print("Not in the photos does not mean missing: only distinctive pieces were photographed. "
              "The checklist page gives the real missing list once the pieces are sorted.")


def cmd_fix(args):
    import mapping
    path = os.path.join(args.run, "pieces.tsv")
    rows = read_tsv(path)
    by = {r["crop"]: r for r in rows}
    if args.crop not in by:
        sys.exit("No row %s in %s" % (args.crop, path))
    r = by[args.crop]
    mapper = mapping.Mapper(open_db(args)) if (args.rb or args.colour) else None
    if args.rb:
        nums = [x.strip() for x in args.rb.replace(",", ";").split(";") if x.strip()]
        bad = [n for n in nums if n not in mapper.parts and n not in mapper.figs]
        if bad:
            sys.exit("Not in the Rebrickable catalogue: %s (try: lsf.py lookup ...)" % ", ".join(bad))
        r["rb"] = ";".join(nums)
        r["rb_name"] = mapper.name_of(("fig" if nums[0].startswith("fig-") else "part", nums[0]))
        r["method"], r["conf"], r["use"] = "manual", "manual", "y"
        if all(n.startswith("fig-") or mapper.is_figure(("part", n)) for n in nums):
            r["colour"], r["colour_how"] = "figure", "figure"
    if args.colour:
        if args.colour.lower() not in ("any", "figure") and mapper.rb_color(args.colour) is None:
            sys.exit("Unknown colour %r. Use a Rebrickable or BrickLink colour name, or any." % args.colour)
        r["colour"], r["colour_how"] = args.colour, "manual"
    if args.use:
        r["use"] = args.use
    if args.note is not None:
        r["note"] = args.note
    write_tsv(path, PIECE_COLS, rows)
    print("%s: use=%s rb=%s (%s) colour=%s" % (r["crop"], r["use"], r.get("rb"), r.get("rb_name", "")[:60], r.get("colour")))


# ---- helpers for the agent -----------------------------------------------------------------------
def squash(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def lookup_score(mapper, q, key):
    """Name similarity that ignores spaces and hyphens: "car wash" finds "Carwash"."""
    qt = mapping_tokens(q)
    s = mapper.sim(qt + [a + b for a, b in zip(qt, qt[1:])], key)
    name = squash(mapper.name_of(key))
    qs = squash(q)
    if len(qs) >= 4 and qs in name:
        s = max(s, 0.8)
    elif len(qt) > 1 and all(squash(t) in name for t in qt):
        s = max(s, 0.6)
    return s


def mapping_tokens(q):
    import mapping
    return mapping.tokens(q)


def duplo_parts(con):
    """Parts and minifigs that appear in sets of any Duplo theme (or sit in a Duplo part category)."""
    themes = {r[0]: (r[1], r[2]) for r in con.execute("SELECT id, name, parent_id FROM themes")}

    def is_duplo(t, depth=0):
        while t in themes and depth < 10:
            name, parent = themes[t]
            if "duplo" in name.lower():
                return True
            t, depth = parent, depth + 1
        return False
    duplo_themes = [t for t in themes if is_duplo(t)]
    marks = ",".join("?" * len(duplo_themes)) or "NULL"
    parts = {r[0] for r in con.execute(
        "SELECT DISTINCT ps.part_num FROM part_sets ps JOIN sets s ON s.set_num = ps.set_num "
        "WHERE s.theme_id IN (%s)" % marks, duplo_themes)}
    parts |= {r[0] for r in con.execute("SELECT part_num FROM parts WHERE cat IN (4, 57)")}
    figs = {r[0] for r in con.execute(
        "SELECT DISTINCT fs.fig_num FROM fig_sets fs JOIN sets s ON s.set_num = fs.set_num "
        "WHERE s.theme_id IN (%s)" % marks, duplo_themes)}
    return parts, figs


def cmd_lookup(args):
    import mapping
    con = open_db(args)
    mapper = mapping.Mapper(con)
    q = " ".join(args.query)
    cid = None
    if args.colour:
        cid = mapper.rb_color(args.colour)
        if cid is None:
            sys.exit("Unknown colour %r" % args.colour)
    if q in mapper.parts or q in mapper.figs:
        scored = [(1.0, ("fig" if q.startswith("fig-") else "part", q))]
        if q in mapper.parts:
            scored += [(0.99, k) for k in mapper.variants(q) if k[1] != q]
    else:
        pool = [("part", p) for p in mapper.used_parts] + [("fig", f) for f in mapper.used_figs]
        if args.duplo or args.system:
            dparts, dfigs = duplo_parts(con)
            want = bool(args.duplo)
            pool = [k for k in pool if ((k[1] in dparts) if k[0] == "part" else (k[1] in dfigs)) == want]
        scored = [(lookup_score(mapper, q, k), k) for k in pool]
        scored = sorted((x for x in scored if x[0] > 0), key=lambda x: (-x[0], x[1]))
    shown = 0
    for s, (kind, num) in scored:
        if shown >= args.limit:
            break
        if kind == "fig":
            if cid is not None:
                continue
            n = con.execute("SELECT COUNT(*) FROM fig_sets WHERE fig_num=?", (num,)).fetchone()[0]
            cols = "figure"
        else:
            have = mapper.colors_of(num)
            if cid is not None and cid not in have:
                continue
            names = sorted(mapper.color_names.get(c, "?") for c in have)
            cols = ", ".join(names[:8]) + (", and %d more" % (len(names) - 8) if len(names) > 8 else "")
            n = con.execute("SELECT COUNT(DISTINCT set_num) FROM part_sets WHERE part_num=?", (num,)).fetchone()[0]
        print("%.2f  %-16s %s\n      %d sets; %s" % (s, num, mapper.name_of((kind, num)), n, cols))
        shown += 1
    if not shown:
        print("Nothing found. Try fewer or other words, or drop --duplo/--system/--colour.")


def cmd_sets_of(args):
    import mapping
    import rank
    con = open_db(args)
    mapper = mapping.Mapper(con)
    cid = mapper.rb_color(args.colour) if args.colour else None
    if args.colour and cid is None:
        sys.exit("Unknown colour %r" % args.colour)
    sets = rank.sets_for(con, mapper, args.part, cid)
    for s in sorted(sets):
        i = rank.set_info(con, s)
        print("%-10s %-50s %s  x%d" % (s, i["name"][:50], i["year"], sets[s]))
    print("%d sets" % len(sets))


def cmd_set(args):
    import mapping
    import rank
    con = open_db(args)
    mapper = mapping.Mapper(con)
    num = norm_set(args.set_num)
    i = rank.set_info(con, num)
    print("%s %s (%s), %s pieces, theme %s" % (num, i["name"], i["year"], i["num_parts"], i["theme"]))
    for it in rank.inventory(con, mapper, num):
        print("  %2d x %-16s %-20s %s" % (it["qty"], it["num"], it["colour"] or "figure", it["name"]))


def cmd_selftest(args):
    import selftest
    sys.exit(selftest.main())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", help="catalogue and cache folder (default: ~/.cache/lego-set-finder or $LEGO_SET_FINDER_DATA)")
    sub = ap.add_subparsers(dest="cmd")

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_, description=help_)
        p.set_defaults(fn=fn)
        return p

    p = add("data", cmd_data, "index the Rebrickable catalogue files the user downloaded by hand "
                               "(without --from: say which files to download, exit 1)")
    p.add_argument("--from", dest="from_dir", help="folder with the files downloaded from rebrickable.com/downloads")
    p.add_argument("--rebuild", action="store_true", help="rebuild the index from the files already imported")
    p.add_argument("--info", action="store_true", help="show what is installed (exit 1 when nothing is)")

    p = add("detect", cmd_detect, "find pieces in photos (files or folders)")
    p.add_argument("run")
    p.add_argument("photos", nargs="+")
    p.add_argument("--threshold", type=int, help="difference threshold 10-80 (default: automatic)")
    p.add_argument("--min-area", type=float, default=0.0004, help="smallest piece as a share of the photo area")

    p = add("zoom", cmd_zoom, "show a region of a photo's overview at twice the size, same coordinates")
    p.add_argument("run")
    p.add_argument("photo")
    p.add_argument("region", help="x0,y0,x1,y1 in overview pixels")
    p.add_argument("--scale", type=float, default=2.0)

    p = add("overview", cmd_overview, "redraw overview images from boxes.tsv")
    p.add_argument("run")
    p.add_argument("photo", nargs="*")

    p = add("boxes", cmd_boxes, "keep, drop or add boxes on one photo, then redraw its overview")
    p.add_argument("run")
    p.add_argument("photo")
    p.add_argument("--drop", help="comma-separated box ids to drop")
    p.add_argument("--keep", help="comma-separated box ids to keep again")
    p.add_argument("--only", help="keep only these automatic boxes, drop the other automatic ones")
    p.add_argument("--add", action="append", help="new box x0,y0,x1,y1 in overview pixels, optional :note (repeatable)")
    p.add_argument("--clear-manual", action="store_true", help="remove the manual boxes of this photo first")

    p = add("crop", cmd_crop, "cut one image per kept box")
    p.add_argument("run")

    p = add("identify", cmd_identify, "send crops to Brickognize")
    p.add_argument("run")
    p.add_argument("--max-calls", type=int, default=300, help="stop after this many new calls (default 300)")
    p.add_argument("--workers", type=int, default=3, help="parallel requests, 1-4 (default 3)")
    p.add_argument("--endpoint", default="predict/", choices=["predict/", "predict/parts/", "predict/figs/"])
    p.add_argument("--only", help="comma-separated crop names to (re)send")
    p.add_argument("--again", action="store_true", help="ignore earlier answers in identify.json (the cache still applies)")

    p = add("match", cmd_match, "map answers to Rebrickable and write pieces.tsv")
    p.add_argument("run")
    p.add_argument("--reset", action="store_true", help="rebuild every row, dropping hand edits")
    p.add_argument("--redo", help="comma-separated crops to rebuild from their Brickognize answer")
    p.add_argument("--kind", choices=["duplo", "lego", "any"], help="what the pieces are (saved for the run; duplo prefers Duplo answers)")

    p = add("rank", cmd_rank, "rank sets from pieces.tsv")
    p.add_argument("run")
    p.add_argument("--top", type=int, default=25)

    p = add("page", cmd_page, "build the checklist page")
    p.add_argument("run")
    p.add_argument("--sets", help="comma-separated set numbers (default: sets in --tiers)")
    p.add_argument("--tiers", default="likely", help="tiers to include when --sets is not given (default likely)")
    p.add_argument("--title", default="LEGO set checklist")
    p.add_argument("--confirmed", action="store_true", help="label every set as confirmed (the user checked them)")
    p.add_argument("--out", help="output file (default: <run>/checklist.html)")

    p = add("missing", cmd_missing, "missing parts per set")
    p.add_argument("run")
    p.add_argument("--sets")
    p.add_argument("--tiers", default="likely")
    p.add_argument("--ticks", help="ticks file saved from the checklist page")

    p = add("fix", cmd_fix, "correct one row of pieces.tsv by hand (after checking it by eye)")
    p.add_argument("run")
    p.add_argument("crop")
    p.add_argument("--rb", help="Rebrickable part or minifig number(s), ; separated when unsure")
    p.add_argument("--colour", help="colour name (Rebrickable or BrickLink spelling), or any")
    p.add_argument("--use", choices=["y", "n"], help="y to count this piece, n to leave it out")
    p.add_argument("--note")

    p = add("lookup", cmd_lookup, "search Rebrickable parts and figures by name or number")
    p.add_argument("query", nargs="+")
    p.add_argument("--duplo", action="store_true", help="only parts and figures that appear in Duplo sets")
    p.add_argument("--system", action="store_true", help="only parts and minifigs that appear in no Duplo set")
    p.add_argument("--colour", help="only parts that come in this colour")
    p.add_argument("--limit", type=int, default=12)

    p = add("sets-of", cmd_sets_of, "sets that hold a Rebrickable part or minifig")
    p.add_argument("part")
    p.add_argument("--colour")

    p = add("set", cmd_set, "show a set's parts")
    p.add_argument("set_num")

    add("selftest", cmd_selftest, "offline check of the scripts")

    args = ap.parse_args()
    if not getattr(args, "fn", None):
        ap.print_help()
        return
    args.fn(args)


if __name__ == "__main__":
    main()
