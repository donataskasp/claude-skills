"""Rebrickable catalogue data: index the public CSV files in SQLite.

The user downloads the files by hand from https://rebrickable.com/downloads/ (no account or key
needed). Rebrickable's terms forbid automated downloads, so this skill never fetches them itself.
Everything else in the skill reads the SQLite index built here, so lookups work offline.
"""
import csv
import datetime
import gzip
import json
import os
import shutil
import sqlite3
import sys
import time

DOWNLOAD_PAGE = "https://rebrickable.com/downloads/"
FILES = [
    "themes", "colors", "part_categories", "parts", "part_relationships", "elements",
    "sets", "minifigs", "inventories", "inventory_parts", "inventory_minifigs",
]
DB_NAME = "rebrickable.sqlite"
SCHEMA_VERSION = 1


def default_data_dir():
    env = os.environ.get("LEGO_SET_FINDER_DATA")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "lego-set-finder")


def csv_dir(data_dir):
    return os.path.join(data_dir, "rebrickable")


def db_path(data_dir):
    return os.path.join(data_dir, DB_NAME)


def instructions():
    """What the user has to download by hand, and how to hand it over."""
    lines = ["The Rebrickable catalogue is downloaded by hand (Rebrickable's terms forbid automated downloads).",
             "",
             "1. Open %s in a browser." % DOWNLOAD_PAGE,
             "2. Download these %d files (the .csv.gz versions, about 17 MB in all):" % len(FILES)]
    lines += ["     %s.csv.gz" % n for n in FILES]
    lines += ["3. Put them in one folder (your Downloads folder is fine). Unzipped .csv files work too.",
              "   If the browser renamed a file, for example \"sets (1).csv.gz\", rename it back.",
              "4. Run: lsf.py data --from <that folder>",
              "",
              "To refresh the catalogue later, download the files again and run the same command."]
    return "\n".join(lines)


def find_files(src_dir):
    """{name: path} for each catalogue file found in src_dir, and the list of missing names."""
    found, missing = {}, []
    for name in FILES:
        for fn in (name + ".csv.gz", name + ".csv"):
            p = os.path.join(src_dir, fn)
            if os.path.isfile(p):
                found[name] = p
                break
        else:
            missing.append(name)
    return found, missing


def import_local(data_dir, src_dir, log=print):
    """Copy the hand-downloaded files (name.csv.gz or name.csv) into <data_dir>/rebrickable/."""
    if not os.path.isdir(src_dir):
        raise SystemExit("No such folder: %s" % src_dir)
    found, missing = find_files(src_dir)
    if missing:
        raise SystemExit("Missing in %s: %s\nDownload them from %s (the .csv.gz files), then run this again."
                         % (src_dir, ", ".join(n + ".csv.gz" for n in missing), DOWNLOAD_PAGE))
    out = csv_dir(data_dir)
    os.makedirs(out, exist_ok=True)
    for name, p in found.items():
        dest = os.path.join(out, name + ".csv.gz")
        if p.endswith(".gz"):
            with gzip.open(p, "rb") as g:  # fail early on a broken download
                g.read(64)
            shutil.copy2(p, dest)
        else:
            with open(p, "rb") as a, gzip.open(dest, "wb") as b:
                shutil.copyfileobj(a, b)
            st = os.stat(p)
            os.utime(dest, (st.st_atime, st.st_mtime))
        log("imported %-20s from %s" % (name, os.path.basename(p)))


def _rows(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            yield row


def build_index(data_dir, log=print):
    """Build <data_dir>/rebrickable.sqlite from the CSV files."""
    src = csv_dir(data_dir)
    for name in FILES:
        if not os.path.exists(os.path.join(src, name + ".csv.gz")):
            raise SystemExit("Missing %s. Run: lsf.py data" % os.path.join(src, name + ".csv.gz"))
    t0 = time.time()
    tmp = db_path(data_dir) + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.executescript("""
    PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
    CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE themes(id INTEGER PRIMARY KEY, name TEXT, parent_id INTEGER);
    CREATE TABLE colors(id INTEGER PRIMARY KEY, name TEXT, rgb TEXT, is_trans INTEGER);
    CREATE TABLE part_categories(id INTEGER PRIMARY KEY, name TEXT);
    CREATE TABLE parts(part_num TEXT PRIMARY KEY, name TEXT, cat INTEGER);
    CREATE TABLE rels(rel TEXT, child TEXT, parent TEXT);
    CREATE TABLE elements(element_id TEXT PRIMARY KEY, part_num TEXT, color_id INTEGER);
    CREATE TABLE sets(set_num TEXT PRIMARY KEY, name TEXT, year INTEGER, theme_id INTEGER, num_parts INTEGER, img_url TEXT);
    CREATE TABLE minifigs(fig_num TEXT PRIMARY KEY, name TEXT, num_parts INTEGER, img_url TEXT);
    CREATE TABLE inv(id INTEGER PRIMARY KEY, version INTEGER, set_num TEXT);
    CREATE TABLE inv_parts(inv_id INTEGER, part_num TEXT, color_id INTEGER, qty INTEGER, spare INTEGER);
    CREATE TABLE inv_figs(inv_id INTEGER, fig_num TEXT, qty INTEGER);
    CREATE TABLE part_img(part_num TEXT, color_id INTEGER, url TEXT, PRIMARY KEY(part_num, color_id));
    """)

    def load(table, name, cols, conv):
        rows = (conv(r) for r in _rows(os.path.join(src, name + ".csv.gz")))
        con.executemany("INSERT OR IGNORE INTO %s VALUES (%s)" % (table, ",".join("?" * cols)), rows)

    def num(v):
        return int(v) if v not in (None, "") else None

    load("themes", "themes", 3, lambda r: (int(r["id"]), r["name"], num(r["parent_id"])))
    load("colors", "colors", 4, lambda r: (int(r["id"]), r["name"], r["rgb"], 1 if r["is_trans"] in ("True", "t", "1") else 0))
    load("part_categories", "part_categories", 2, lambda r: (int(r["id"]), r["name"]))
    load("parts", "parts", 3, lambda r: (r["part_num"], r["name"], num(r["part_cat_id"])))
    load("rels", "part_relationships", 3, lambda r: (r["rel_type"], r["child_part_num"], r["parent_part_num"]))
    load("elements", "elements", 3, lambda r: (r["element_id"], r["part_num"], num(r["color_id"])))
    load("sets", "sets", 6, lambda r: (r["set_num"], r["name"], num(r["year"]), num(r["theme_id"]), num(r["num_parts"]), r["img_url"]))
    load("minifigs", "minifigs", 4, lambda r: (r["fig_num"], r["name"], num(r["num_parts"]), r["img_url"]))
    load("inv", "inventories", 3, lambda r: (int(r["id"]), int(r["version"]), r["set_num"]))
    load("inv_figs", "inventory_minifigs", 3, lambda r: (int(r["inventory_id"]), r["fig_num"], int(r["quantity"])))

    imgs = {}

    def ip(r):
        key = (r["part_num"], int(r["color_id"]))
        if r.get("img_url") and key not in imgs:
            imgs[key] = r["img_url"]
        return (int(r["inventory_id"]), r["part_num"], int(r["color_id"]), int(r["quantity"]),
                1 if r["is_spare"] in ("True", "t", "1") else 0)
    load("inv_parts", "inventory_parts", 5, ip)
    con.executemany("INSERT INTO part_img VALUES (?,?,?)", ((k[0], k[1], v) for k, v in imgs.items()))

    con.executescript("""
    CREATE INDEX inv_parts_inv ON inv_parts(inv_id);
    CREATE INDEX inv_figs_inv ON inv_figs(inv_id);
    CREATE INDEX rels_child ON rels(child);
    CREATE INDEX rels_parent ON rels(parent);
    CREATE INDEX inv_set ON inv(set_num);
    -- One inventory per set or minifig: the lowest version.
    CREATE TABLE inv1 AS SELECT set_num, MIN(version) AS version, 0 AS id FROM inv GROUP BY set_num;
    UPDATE inv1 SET id = (SELECT inv.id FROM inv WHERE inv.set_num = inv1.set_num AND inv.version = inv1.version);
    CREATE INDEX inv1_id ON inv1(id);
    CREATE UNIQUE INDEX inv1_set ON inv1(set_num);
    -- Which minifigs each set holds.
    CREATE TABLE fig_sets AS
      SELECT f.fig_num AS fig_num, i.set_num AS set_num, SUM(f.qty) AS qty
      FROM inv_figs f JOIN inv1 i ON i.id = f.inv_id
      WHERE i.set_num NOT LIKE 'fig-%' GROUP BY f.fig_num, i.set_num;
    CREATE INDEX fig_sets_fig ON fig_sets(fig_num);
    CREATE INDEX fig_sets_set ON fig_sets(set_num);
    -- Every non-spare part of every set, including the parts inside its minifigs (via_fig = 1).
    CREATE TABLE part_sets AS
      SELECT p.part_num AS part_num, p.color_id AS color_id, i.set_num AS set_num, SUM(p.qty) AS qty, 0 AS via_fig
      FROM inv_parts p JOIN inv1 i ON i.id = p.inv_id
      WHERE p.spare = 0 AND i.set_num NOT LIKE 'fig-%'
      GROUP BY p.part_num, p.color_id, i.set_num;
    INSERT INTO part_sets
      SELECT p.part_num, p.color_id, fs.set_num, SUM(p.qty * fs.qty), 1
      FROM fig_sets fs JOIN inv1 fi ON fi.set_num = fs.fig_num JOIN inv_parts p ON p.inv_id = fi.id
      WHERE p.spare = 0 GROUP BY p.part_num, p.color_id, fs.set_num;
    CREATE INDEX part_sets_pc ON part_sets(part_num, color_id);
    CREATE INDEX part_sets_set ON part_sets(set_num);
    DROP TABLE inv_parts;
    """)
    stamp = {}
    for name in FILES:
        p = os.path.join(src, name + ".csv.gz")
        stamp[name] = datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d")
    con.executemany("INSERT INTO meta VALUES (?,?)", [
        ("schema", str(SCHEMA_VERSION)),
        ("built", datetime.date.today().isoformat()),
        ("files", json.dumps(stamp)),
    ])
    con.commit()
    con.execute("VACUUM")
    con.close()
    os.replace(tmp, db_path(data_dir))
    log("index built in %.0f s: %s (%.0f MB)" % (time.time() - t0, db_path(data_dir), os.path.getsize(db_path(data_dir)) / 1e6))


def connect(data_dir):
    p = db_path(data_dir)
    if not os.path.exists(p):
        raise SystemExit("No Rebrickable catalogue at %s yet. Run: lsf.py data (it says what to download)" % p)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    v = con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
    if not v or int(v[0]) != SCHEMA_VERSION:
        raise SystemExit("The Rebrickable index is from another version of this skill. Run: lsf.py data --rebuild")
    return con


def info(data_dir):
    con = connect(data_dir)
    meta = {r["k"]: r["v"] for r in con.execute("SELECT k, v FROM meta")}
    n_sets = con.execute("SELECT COUNT(*) FROM sets").fetchone()[0]
    n_parts = con.execute("SELECT COUNT(*) FROM parts").fetchone()[0]
    return {"built": meta.get("built"), "files": json.loads(meta.get("files", "{}")), "sets": n_sets, "parts": n_parts}


if __name__ == "__main__":
    sys.exit("Use lsf.py data")
