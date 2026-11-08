# claude-skills

Claude Code skills I use and share.

| Skill | What it does |
|---|---|
| [lego-set-finder](lego-set-finder/) | Finds which LEGO or Duplo sets a mixed pile of pieces came from, from photos, and builds a checklist page to sort them. |

## Install

As a plugin marketplace (tested with Claude Code 2.1.319):

```
/plugin marketplace add donataskasp/claude-skills
/plugin install lego-set-finder@donataskasp-skills
```

Or copy a skill folder by hand: put `lego-set-finder/` in `~/.claude/skills/` (for all your projects) or in a project's `.claude/skills/` (for that project only). Start a new session and the skill is there.

---

## lego-set-finder

You photograph the distinctive pieces of a mixed pile, and Claude works out which sets they came from, with the evidence for each. It then builds one local web page per set list where you tick off parts as you sort them, and tells you what each set is missing.

### What you need

- Claude Code
- Python 3.8 or later with [Pillow](https://pypi.org/project/Pillow/) (`python3 -m pip install --user Pillow`). Tested on macOS; Windows is untested, and there `py` may stand in for `python3`.
- Rebrickable's catalogue files, which you download by hand once from [rebrickable.com/downloads](https://rebrickable.com/downloads/) (11 files, about 17 MB; the index built from them takes about 115 MB). Claude tells you which ones; Rebrickable's terms forbid automated downloads, so the skill never fetches them itself.
- An internet connection for piece recognition and for part pictures on the checklist page

No account or API key is needed anywhere.

### Use it

Ask Claude something like "which Duplo sets are in these photos?" and point it at your photos. It asks a few questions first (Duplo or regular LEGO, where the photos are, whether it may send piece pictures to Brickognize), then runs the steps and checks each one by eye.

### Taking the photos

- Pull out only the distinctive pieces: figures, animals, printed bricks, vehicles, odd shapes. Plain bricks and plates are useless for telling sets apart.
- Spread as many as fit on a large plain white sheet with about 2 cm of space around each piece, printed side up. The gap matters more than the count: pieces that touch get cut out as one. Take as many photos as the pile needs; the skill asks before it goes past about 300 pieces in one run. White, grey and clear pieces stand out better on a dark sheet.
- Shoot from straight above in good light, with the whole sheet in the frame.
- For regular LEGO (smaller pieces), put fewer in each photo and shoot closer.
- Minifigures and printed parts carry most of the evidence.
- JPEG or PNG photos work as they are. HEIC photos (iPhone) need the pillow-heif package, or export them as JPEG.

### What data goes where

| Data | Where it goes |
|---|---|
| Your photos | Stay on your computer. They are never uploaded. |
| One cut-out picture per piece | Sent to [Brickognize](https://brickognize.com) to identify the piece, with no metadata (no camera, time or location data) and under a fixed file name. Under section 8 of its [terms](https://brickognize.com/terms-of-service/), you grant Brickognize a permanent, irrevocable, royalty-free licence to use uploaded images, including to train machine learning models; you can ask it to remove them. Answers are cached on your computer, so a cut-out is never sent twice to the same endpoint. |
| Rebrickable catalogue | You download the files by hand; the skill indexes them into `~/.cache/lego-set-finder` and works offline from there. To refresh, download them again and rerun `lsf.py data --from <folder>`. |
| Checklist page and your ticks | A local HTML file; ticks are saved in your browser. Part pictures load from Rebrickable's image server. |

### How it works

1. Pieces are found in each photo by comparing every pixel with an estimate of the bare background, then cut out one per image. Claude checks every box by eye and fixes merged or missed pieces.
2. Each cut-out (never the whole photo) goes to Brickognize, which answers with a BrickLink part number, a name and a colour guess.
3. The answer is translated to Rebrickable's numbering: by the LEGO element number in the name where there is one, by the shared number for plain parts, and by comparing names for prints, assemblies and Duplo figures. Claude reviews every doubtful match.
4. Each piece points to every set that holds that part in that colour. Pieces found in few sets weigh more. Sets are picked one at a time, each by the evidence it adds and by how many of its own distinctive parts the photos show.
5. The checklist page lists every part of the chosen sets with its picture and quantity.

Details: [lego-set-finder/reference.md](lego-set-finder/reference.md). Check an install offline with `python3 lego-set-finder/scripts/lsf.py selftest`.

### Limits

- Tested on one mixed Duplo collection of 12 sets, on macOS: from photos of all its distinctive pieces, all 12 ranked in the top 14, with 2 wrong sets among them. Regular LEGO and Windows are untested.
- Brickognize is a free service run by its authors. Its predict endpoints are marked "legacy" in its [API description](https://api.brickognize.com/openapi.json), so they may change or stop. It finds nothing on some large or soft pieces (vehicle bodies, cloth); Claude names those by eye.
- The ranking is evidence for you to judge, not proof. Your own recognition of a set decides.

### Credits and licences

- Piece recognition by [Brickognize](https://brickognize.com). See its [terms of service](https://brickognize.com/terms-of-service/).
- Data sourced from [Rebrickable](https://rebrickable.com): set, part and colour data from the files you download, and part pictures on the checklist page. The skill never contacts Rebrickable's website or API.
- LEGO® is a trademark of the LEGO Group, which does not sponsor, authorize or endorse this project.
- The code in this repository is under the [MIT licence](LICENSE).
