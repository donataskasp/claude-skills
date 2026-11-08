---
name: lego-set-finder
description: Find which LEGO or Duplo sets a mixed pile of pieces came from, using photos of the distinctive pieces, then build a tick-off checklist page and list the missing parts per set. Use when someone wants to sort mixed or second-hand LEGO back into its sets, identify sets from pieces or figures, or check what a set is missing.
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/lsf.py *)
---

# LEGO set finder

The user photographs the **distinctive pieces** of a pile: figures, animals, printed bricks, vehicles, odd shapes. Each piece is cut out of the photos, identified by Brickognize (a free image recognition service), and looked up offline in Rebrickable's public catalogue. Sets are ranked by how many independent distinctive pieces point to them. Plain bricks and plates tell sets apart poorly, so they stay out of the photos and out of the evidence.

All commands are one script. Every step reads and writes files in one run folder (`RUN` below, for example `./lego-run`):

```
python3 ${CLAUDE_SKILL_DIR}/scripts/lsf.py <step> RUN ...
```

Below, `lsf.py` stands for that full command. `lsf.py <step> -h` lists every option. Where `python3` is not found (often on Windows, which is untested), use `py` instead. File formats, the scoring and fixes for common problems are in [reference.md](reference.md); read it when a step goes wrong or before you edit a file by hand.

## 1. Agree the job with the user

Ask, in one message:

1. Duplo or regular LEGO? (This skill was tested on Duplo only. Say so if they answer LEGO.)
2. Where are the photos? If there are none yet, give them the photo tips below.
3. May the skill send a cut-out picture of each single piece to Brickognize, a free third-party recognition service? Only those cut-outs leave the computer, never the full photos, and they carry no metadata (no camera, time or location data). Brickognize's terms give it a permanent, royalty-free licence to use uploaded images, including to train machine learning models; the user can ask Brickognize to remove them. Without a yes, stop here.
4. Any sets they already suspect (boxes, manuals, order history)? These help you check the result, never replace the evidence.

**Photo tips** (give them as written):

- Pull out only the distinctive pieces: figures, animals, printed bricks, vehicles, odd shapes. Plain bricks and plates are useless for telling sets apart.
- Lay 20 to 30 per photo on a plain white sheet, about 2 cm apart, printed side up. White, grey and clear pieces stand out better on a dark sheet.
- Shoot from straight above in good light, with the whole sheet in the frame.
- For regular LEGO (smaller pieces), put fewer in each photo and shoot closer.
- Minifigures and printed parts carry most of the evidence.
- JPEG or PNG photos work as they are. HEIC photos (iPhone) need the pillow-heif package, or export them as JPEG.

Done when you know the kind of pieces, the photo files, and you have the yes for Brickognize.

## 2. Install check and catalogue

```
lsf.py selftest          # offline, a few seconds; must end with "0 failed"
lsf.py data --info       # exit 0: a catalogue is installed, go to step 3
```

If Pillow is missing, the script says how to install it.

The user downloads the Rebrickable catalogue by hand, once: Rebrickable's terms forbid automated downloads, so never fetch these files yourself, with any tool. Run `lsf.py data`: it prints the download page and the exact files (it exits 1 on purpose). Give the user that list, tell them to save the files into one folder (Downloads is fine), and wait until they say it is done. Then:

```
lsf.py data --from ~/Downloads     # or the folder they used; names any file still missing
```

Done when `lsf.py data --info` exits 0. The downloads are about 17 MB; the index takes about 115 MB in `~/.cache/lego-set-finder` (or `$LEGO_SET_FINDER_DATA`). To refresh it later, the user downloads the files again and you run `data --from` again.

## 3. Find the pieces

```
lsf.py detect RUN <photos or folder>
```

It writes `RUN/overview/<photo>.jpg`: the photo with numbered boxes and a grid labelled every 100 pixels. Open every overview and judge each box:

- keep a box that holds exactly one distinctive piece;
- drop boxes on plain bricks and plates, background, shadows, the sheet edge, and fragments of a piece;
- replace a box that holds two touching pieces with one box per piece;
- add a box for every distinctive piece that has none (pale pieces on a pale floor are often missed).

```
lsf.py boxes RUN <photo> --only 3,7,12                  # keep these automatic boxes, drop the rest
lsf.py boxes RUN <photo> --drop 9 --add 405,225,515,385:figure --add 395,380,500,500
```

Coordinates are overview pixels (x0,y0,x1,y1), read off the grid. Each call redraws the overview with the kept boxes only: automatic ones magenta, yours cyan. Look again after each fix. Where pieces are small or packed close, `lsf.py zoom RUN <photo> x0,y0,x1,y1` shows that region at twice the size with the same coordinates and a grid every 50 px; read the box corners off the zoom.

Done when, on every overview, every distinctive piece sits in exactly one kept box and every kept box holds only that piece.

## 4. Cut and check the crops

```
lsf.py crop RUN
```

Open every `RUN/sheets/crops_<photo>.jpg`. Each crop must show one whole piece. Fix boxes with `lsf.py boxes` and crop again until that holds for all of them.

## 5. Identify

```
lsf.py identify RUN
```

Sends each crop to Brickognize, 3 at a time by default (`--workers`, at most 4). Answers are cached by image and endpoint, so a rerun costs nothing for crops already sent to that endpoint. It stops at 300 new calls (exit code 2): ask the user before you rerun with a higher `--max-calls`.

Crops with no answer are listed. Brickognize often finds nothing on large or soft pieces (vehicle bodies, carriages, cloth). Try once with `--endpoint predict/parts/ --only <crops>`. For what still fails you name the piece yourself in step 6.

## 6. Map and review every piece

```
lsf.py match RUN --kind duplo        # or --kind lego
```

This writes `RUN/pieces.tsv`, one row per crop: Brickognize's answer, the Rebrickable part or figure it maps to (`rb`, several when unsure), the colour, and a confidence. It prints the rows to check first, every figure among them: figure matches keep close runners-up that can be plainly wrong (a girl for a boy). Open every `RUN/sheets/review_<photo>.jpg` (crop, confidence, Brickognize's name, Rebrickable number and colour) and compare each crop with its caption.

Fix what is wrong with the catalogue helpers:

```
lsf.py lookup yellow truck flatbed grille --duplo --colour Yellow   # search by name or part number
lsf.py fix RUN p10-m1 --rb 15454pr0006 --colour Yellow
lsf.py fix RUN p08-27 --colour "Neon Yellow"     # your eye beats Brickognize on colour
lsf.py fix RUN p08-m3 --use n                     # cannot be identified: leave it out
lsf.py set 10931                                  # a set's parts, to check a hunch
lsf.py sets-of 14094 --colour "Medium Azure"      # which sets hold a part in a colour
```

Name a piece Brickognize missed from what you see, then `lookup` it; pick the candidate whose name and colours fit. When two or three candidates fit equally, give them all (`--rb a;b;c`): the ranking copes with that better than with a wrong guess. A Duplo pile answered with a non-Duplo part is usually wrong.

Done when every row with `use` = y has an `rb` and a colour you believe from the crop, and every row you cannot vouch for has `use` = n.

## 7. Rank the sets

```
lsf.py rank RUN
```

Read `RUN/sets.md`. For each set it shows the tier (likely, possible, weak), the rank score, how many pieces point to it, how many of those no other set has, the share of the set's own distinctive items seen in the photos, and the evidence piece by piece.

Present the likely sets to the user as a table (set number, name, year, pieces, only-here pieces, seen share), then the possible ones in one line each. Point out:

- sets listed under "Shares evidence with": they compete for the same pieces, so ask the user which one they have;
- big sets that score on many shared parts but show few of their own (low seen share): these are the usual false positives;
- distinctive pieces that no listed set explains: another set may be in the pile.

Ask the user which sets they recognise or want on the checklist. The Rebrickable link in the checklist shows each set's box picture.

## 8. Build the checklist page

```
lsf.py page RUN --sets 10906,10931,10963 [--confirmed] [--title "Our Duplo"]
```

Without `--sets` it takes the likely tier. `--confirmed` labels every set as confirmed (use it after the user has checked them). The result is `RUN/checklist.html`, one file that works when opened from disk. Tell the user how to use it: pick a set or switch to "By colour pile", tap a part's count as each piece goes into its pile, "Hide found" to see what is left, "Missing parts list" to copy what is still missing, "Save or reset ticks" to keep the ticks in a file. Ticks save in that browser only. Part pictures load from Rebrickable's image server, so they need a connection.

## 9. Say what is missing

Before the user sorts, tell them which distinctive items of each chosen set did not show up in the photos:

```
lsf.py missing RUN --sets 10906,10931
```

Say clearly that "not in the photos" is not "missing": only distinctive pieces were photographed. When it names a crop you left out (`use` = n) that fits a missing part, look at that crop again and restore it with `lsf.py fix RUN <crop> --use y` if it fits; then rank again. After sorting, take the list the page copies, or the ticks file the page saves (`lsf.py missing RUN --sets ... --ticks <file>`), and give the missing parts per set.

## Limits to tell the user

- Tested on one mixed Duplo collection (12 sets), on macOS. Regular LEGO and Windows are untested.
- Brickognize is a free third-party service; the piece cut-outs are uploaded to it and it may keep and use them (step 1). Its predict endpoints are marked "legacy" in its API description, so they may change or stop.
- The Rebrickable catalogue is a one-time download by hand (11 files, about 17 MB; the index takes about 115 MB) and goes stale slowly; to refresh it, download the files again and run `lsf.py data --from` again.
- Rebrickable and BrickLink number some parts differently, Duplo figures most of all. Mapped figures and prints carry a confidence; check the doubtful ones by eye.
