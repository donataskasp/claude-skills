# lego-set-finder reference

Read this when a step goes wrong, or before you edit a run file by hand. The steps themselves are in [SKILL.md](SKILL.md).

## Run folder

| File | Written by | What it holds |
|---|---|---|
| `run.json` | detect, match | photo ids and paths; the kind of pieces (`duplo`, `lego`, `any`) |
| `boxes.tsv` | detect, boxes | one row per box: `photo box x0 y0 x1 y1 keep source note` |
| `overview/<photo>.jpg` | detect, boxes, overview | the photo with its kept boxes, numbered, and a 100 px grid |
| `overview/<photo>-zoom.jpg` | zoom | a region at twice the size, same coordinates, grid every 50 px |
| `crops/<photo>-<box>.jpg` | crop | one piece per image, longest side at most 1024 px, no metadata; uploaded as `piece.jpg` |
| `sheets/crops_<photo>.jpg` | crop | contact sheet of the crops |
| `identify.json` | identify | Brickognize's answer per crop |
| `pieces.tsv` | match, fix | the evidence table, see below |
| `sheets/review_<photo>.jpg` | match | each crop with its confidence, name, Rebrickable number and colour |
| `sets.md`, `sets.json` | rank | the ranked sets with their evidence |
| `checklist.html` | page | the tick-off page |

Box coordinates are pixels of the overview image (longest side 1200 px). Automatic boxes are numbered `1, 2, ...` and replaced on each `detect`; boxes added by hand are `m1, m2, ...` and survive a new `detect`. Photo ids come from the file names.

### pieces.tsv columns

| Column | Meaning | Edit? |
|---|---|---|
| `crop` | `<photo>-<box>` | no |
| `use` | `y` counts the piece as evidence, `n` leaves it out | yes |
| `bl_id`, `bl_name`, `bk_score` | Brickognize's best answer (BrickLink number and name) and its score | `bl_id` only; then clear `rb` and the rank step maps it again |
| `rb` | Rebrickable part or minifig number(s), `;` separated | yes, with `lsf.py fix` |
| `rb_name` | name of the first `rb` | filled by `fix` |
| `conf` | `high`, `medium`, `low`, `none`, or `manual` after a fix | weights the evidence: 1.0, 0.8, 0.5, 0, 1.0 |
| `method` | how the number was mapped (see below); `+colour` when the colour narrowed the candidates | no |
| `colour` | Rebrickable or BrickLink colour name, `any`, or `figure` | yes |
| `colour_how` | where the colour came from, and Brickognize's top colour guesses | no |
| `alternatives` | Brickognize's next answers with scores | no |
| `note`, `answer` | free text; signature of the Brickognize answer | `note` only |

`match` keeps rows you edited when you run it again; it rebuilds a row only when that crop got a new Brickognize answer, when you name it in `--redo`, or with `--reset` (which drops every edit).

## How numbers are mapped

Brickognize answers with BrickLink numbers; the catalogue is Rebrickable's. Routes, tried in order:

| Method | When | Reliability |
|---|---|---|
| `element` | the BrickLink name carries a LEGO element number, as many Duplo figure names do: "(6301361)" | exact |
| `same-number` | plain moulds, which mostly share numbers | high |
| `print` | `<mould>pb<n>`: best name match among Rebrickable prints of the mould and of moulds marked as its variants (BrickLink 4066 is Rebrickable 4066 and 76371) | good; the `pr` + 4 digits pattern is only a weak hint and is often wrong |
| `variant` | assemblies such as `<mould>c01`, or plain numbers that Rebrickable only has as prints | good |
| `figure-name` | Duplo figures without an element number: name words plus legs colour, torso colour and child or adult | fair; keeps close runners-up, the ranking picks between them |
| `name` | anything else: best name match in the same family (Duplo or not) | fair |

Colour: Brickognize's colour guesses are tried best first; the first colour the part is known to come in wins. If the part never comes in the colours Brickognize saw, the colour becomes `any` and the confidence drops one level, because the match itself is in doubt. Duplo figures ignore colour.

## How sets are scored

A piece points to every set whose inventory holds that part in that colour (or that figure). Its weight is `trust / sqrt(number of sets)`, and 0.7 of that when the colour is `any`. A piece found in more than 40 sets counts as common and adds nothing. Identical rows (same part, same colour) count once.

Sets are picked greedily. Each round, every set gets the weight of the pieces that point to it, with pieces already explained by an earlier set counted at a quarter. The best candidates are then weighed by coverage, the share of the set's own distinctive items (figures, prints, parts in 10 sets or fewer) that the photos show: `rank score = score x (0.3 + 0.7 x coverage)`. The highest rank score wins the round, unless a rival competes for the same pieces (each holds a good part of the other's new evidence) and shows at least 1.5 times its coverage: then the rival wins. Each set lists the sets it shares most of its evidence with.

Tiers: `likely` at a rank score of 1.0 or more with at least two new independent pieces; `possible` from 0.35 with at least two new pieces, or with one figure or print that no other set has, matched with high confidence; `weak` below.

## When something goes wrong

| Symptom | What to do |
|---|---|
| Detection finds one big box over many pieces | The pieces touch or the background is busy. Drop it and add one box per piece with `lsf.py boxes`; `lsf.py zoom` helps to place them. Ask the user to space pieces wider next time. |
| `crop` says a box covers most of the photo | Such a box would send the whole photo. Draw one box per piece instead. |
| HEIC photos stop the run | Install pillow-heif (`python3 -m pip install --user pillow-heif`) or export the photos as JPEG. |
| Detection misses pale pieces | White or grey on a pale floor. Add boxes by hand, or try `detect --threshold 25` (lower finds more, and more shadows). |
| Brickognize answers nothing | Common for large or soft pieces. Try `identify --endpoint predict/parts/ --only <crop>`, then name the piece yourself with `lookup` and `fix`. |
| Brickognize answers a regular LEGO part for a Duplo piece | Usually wrong. With `match --kind duplo` it takes a close Duplo answer when there is one, else marks the row `low`. |
| `rb` is a plausible part but the colour is `any` | Brickognize saw a colour the part never comes in. Check the crop: fix the colour, or find the right part with `lookup`. |
| A set ranks high on many common parts and few of its own | Typical false positive (low seen share in `sets.md`). Say so to the user; their recognition decides. |
| A set "Shares evidence with" another | They compete for the same pieces. Ask the user which one they have, or look for a piece only one of them has (`lsf.py set <num>`, `lsf.py sets-of <part>`). |
| `identify` exits with code 2 | The 300-call limit. Ask the user before rerunning with `--max-calls`. |
| `data --from` says files are missing | The user downloads exactly those from rebrickable.com/downloads. A browser may have renamed a repeat download, for example `sets (1).csv.gz`: rename it back. |
| "No Rebrickable catalogue ... yet" | Step 2 of SKILL.md: the user downloads the files by hand, then `lsf.py data --from <folder>`. |
| "The Rebrickable index is from another version" | Run `lsf.py data --rebuild`. |
