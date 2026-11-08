"""Rank sets by the independent distinctive pieces that point to them.

Each photographed piece (one row of pieces.tsv with use = y) points to every set whose
Rebrickable inventory holds that part in that colour (or that figure). A piece found in few sets
weighs more: weight = trust / sqrt(number of sets). Pieces found in more than COMMON sets are
treated as common and do not count.

Sets are then picked greedily, like a person sorting a pile: the set that explains the most
evidence first, then the set that explains most of what is left, and so on. A piece that an
earlier set already explains still counts, but only a quarter, so a set needs its own pieces to
rank high. This keeps a set from riding on figures that belong to a better-supported set.

Each round also weighs a set by coverage: the share of its own distinctive items (figures,
prints, parts in few sets) that the photos show. A big set that shares common parts with the
pile but shows few of its own drops back behind a smaller set whose pieces are nearly all there.
"""
import math
import re

COMMON = 40
TRUST = {"high": 1.0, "manual": 1.0, "medium": 0.8, "low": 0.5, "none": 0.0}
SHARED_FACTOR = 0.25


def split_list(v):
    return [x.strip() for x in (v or "").replace(",", ";").split(";") if x.strip()]


class Evidence:
    """One distinctive piece: what it maps to and which sets hold it."""

    def __init__(self, crop, label, keys, color_id, color_name, trust, sets, figure):
        self.crop = crop
        self.label = label
        self.keys = keys
        self.color_id = color_id
        self.color_name = color_name
        self.trust = trust
        self.sets = sets  # {set_num: qty}
        self.figure = figure
        self.copies = 1
        n = len(sets)
        self.common = n > COMMON
        any_colour = color_id is None and not figure
        self.weight = 0.0 if (n == 0 or self.common) else trust * (0.7 if any_colour else 1.0) / math.sqrt(n)


def sets_for(con, mapper, key, color_id):
    """{set_num: qty} for a Rebrickable part (in a colour, or any colour) or a minifig."""
    out = {}
    if key.startswith("fig-"):
        for s, q in con.execute("SELECT set_num, qty FROM fig_sets WHERE fig_num=?", (key,)):
            out[s] = out.get(s, 0) + q
        return out
    fam = tuple(mapper.family(key))
    marks = ",".join("?" * len(fam))
    if color_id is None or mapper.is_figure(("part", key)):
        q = "SELECT set_num, SUM(qty) FROM part_sets WHERE part_num IN (%s) GROUP BY set_num" % marks
        rows = con.execute(q, fam)
    else:
        q = "SELECT set_num, SUM(qty) FROM part_sets WHERE part_num IN (%s) AND color_id=? GROUP BY set_num" % marks
        rows = con.execute(q, fam + (color_id,))
    for s, n in rows:
        out[s] = out.get(s, 0) + n
    return out


def build_evidence(con, mapper, rows):
    """rows: dicts from pieces.tsv. Returns (evidence list, skipped rows with reasons)."""
    seen = {}
    skipped = []
    for r in rows:
        if r.get("use", "y").strip().lower() not in ("y", "yes", "1"):
            continue
        keys = split_list(r.get("rb"))
        if not keys:
            skipped.append((r, "no Rebrickable match"))
            continue
        cname = (r.get("colour") or "").strip()
        cid = None
        if cname and cname.lower() not in ("any", "-", "?", "figure"):
            cid = mapper.rb_color(cname)
            if cid is None:
                skipped.append((r, "unknown colour %r, matched in any colour" % cname))
        figure = all(k.startswith("fig-") or mapper.is_figure(("part", k)) for k in keys)
        sig = (tuple(sorted(keys)), None if figure else cid)
        if sig in seen:
            seen[sig].copies += 1
            continue
        sets = {}
        for k in keys:
            for s, q in sets_for(con, mapper, k, cid).items():
                sets[s] = max(sets.get(s, 0), q)
        trust = TRUST.get((r.get("conf") or "medium").strip().lower(), 0.8)
        label = r.get("rb_name") or r.get("bl_name") or keys[0]
        ev = Evidence(r.get("crop", "?"), label, keys, cid, mapper.color_names.get(cid) if cid is not None else None,
                      trust, sets, figure)
        seen[sig] = ev
    return list(seen.values()), skipped


def set_info(con, set_num):
    r = con.execute("SELECT s.set_num, s.name, s.year, s.num_parts, s.img_url, t.name FROM sets s "
                    "LEFT JOIN themes t ON t.id = s.theme_id WHERE s.set_num=?", (set_num,)).fetchone()
    if not r:
        return {"set_num": set_num, "name": "?", "year": None, "num_parts": None, "img_url": None, "theme": None}
    return {"set_num": r[0], "name": r[1], "year": r[2], "num_parts": r[3], "img_url": r[4], "theme": r[5]}


COV_BASE = 0.3


def coverage(con, mapper, set_num, seen, cache):
    """(seen, total) distinctive items of a set that the photographed pieces account for."""
    if set_num not in cache:
        items = [it for it in inventory(con, mapper, set_num) if distinctive(it)]
        cache[set_num] = (sum(1 for it in items if is_seen(it, seen, mapper)), len(items))
    return cache[set_num]


def rank(con, evidence, mapper, top=25, min_score=0.25, shortlist=40):
    """Greedy ranking. Each round scores every set by the evidence it adds, then weighs the best
    candidates by coverage: the share of the set's own distinctive items found in the photos.
    A big set that shares many common parts with the pile, but shows few of its own, drops back."""
    seen = seen_keys(evidence)
    cov_cache = {}
    by_set = {}
    for i, ev in enumerate(evidence):
        if ev.weight <= 0:
            continue
        for s in ev.sets:
            by_set.setdefault(s, []).append(i)
    explained = [False] * len(evidence)
    chosen = []
    remaining = set(by_set)
    while remaining and len(chosen) < top:
        scores = {s: sum(evidence[i].weight * (SHARED_FACTOR if explained[i] else 1.0) for i in by_set[s])
                  for s in remaining}
        short = sorted(remaining, key=lambda s: (-scores[s], s))[:shortlist]
        adj = {}
        for s in short:
            got, total = coverage(con, mapper, s, seen, cov_cache)
            adj[s] = scores[s] * (COV_BASE + (1 - COV_BASE) * (got / total if total else 0))
        best = min(short, key=lambda s: (-adj[s], len(s), s)) if short else None
        if best is None or scores[best] < min_score:
            break
        cov = {s: (lambda g, t: g / t if t else 0.0)(*cov_cache[s]) for s in short}
        new_w = {s: sum(evidence[i].weight for i in by_set[s] if not explained[i]) for s in short}

        def held(a, b):
            """Weight of a's unexplained pieces that b holds too."""
            return sum(evidence[i].weight for i in by_set[a] if not explained[i] and b in evidence[i].sets)

        # Two sets competing for the same pieces: take the one that shows more of its own items.
        for _ in range(3):
            rivals = [c for c in short if c != best and new_w[c] > 0 and cov[c] >= 1.5 * cov[best]
                      and adj[c] >= 0.4 * adj[best] and held(c, best) >= 0.5 * new_w[c]
                      and held(best, c) >= 0.3 * new_w[best]]
            if not rivals:
                break
            best = max(rivals, key=lambda c: (cov[c], adj[c]))
        best_score = scores[best]
        idx = by_set[best]
        new = [i for i in idx if not explained[i]]
        total_w = sum(evidence[i].weight for i in idx) or 1.0
        shares = {c for c in short if c != best and new and adj[c] >= 0.3 * adj[best]
                  and held(best, c) >= 0.5 * new_w[best]}
        shares |= {e["set_num"] for e in chosen
                   if sum(evidence[i].weight for i in idx if e["set_num"] in evidence[i].sets) >= 0.5 * total_w}
        info = set_info(con, best)
        got, total = cov_cache[best]
        info.update({
            "score": round(best_score, 2),
            "rank_score": round(adj[best], 2),
            "seen_items": got, "distinctive_items": total,
            "total": round(sum(evidence[i].weight for i in idx), 2),
            "pieces": len(idx),
            "new_pieces": len(new),
            "only_here": sum(1 for i in idx if len(evidence[i].sets) == 1),
            "strong_single": any(len(evidence[i].sets) == 1 and evidence[i].trust >= 1.0
                                 and (evidence[i].figure or any(re.search(r"pr\d", k) for k in evidence[i].keys))
                                 for i in new),
            "evidence": [ev_dict(evidence[i], best, not explained[i]) for i in idx],
            "shares": sorted(shares, key=lambda s: (-adj.get(s, 0), s))[:5],
        })
        info["tier"] = tier(info)
        chosen.append(info)
        for i in idx:
            explained[i] = True
        remaining.discard(best)
    unexplained = [ev_dict(evidence[i], None, True) for i, ev in enumerate(evidence)
                   if ev.weight > 0 and not explained[i]]
    common = [ev_dict(ev, None, False) for ev in evidence if ev.common]
    nowhere = [ev_dict(ev, None, False) for ev in evidence if not ev.sets]
    return chosen, unexplained, common, nowhere


def tier(info):
    """likely / possible / weak, from the evidence a set adds on its own and its coverage.

    Both upper tiers need at least two independent new pieces; the one exception for possible is
    a single figure or print that no other set has, matched with high confidence."""
    s, new = info["rank_score"], info["new_pieces"]
    if s >= 1.0 and new >= 2:
        return "likely"
    if (s >= 0.35 and new >= 2) or info["strong_single"]:
        return "possible"
    return "weak"


def ev_dict(ev, set_num, new):
    d = {"crop": ev.crop, "label": ev.label, "keys": ev.keys, "colour": ev.color_name or ("figure" if ev.figure else "any"),
         "n_sets": len(ev.sets), "weight": round(ev.weight, 3), "new": new, "copies": ev.copies}
    if set_num:
        d["qty_in_set"] = ev.sets.get(set_num)
    return d


# ---- inventories for the checklist and the missing list -----------------------------------------
def inventory(con, mapper, set_num):
    """Items of a set: its direct parts and its minifigs (a Duplo figure or car is one item)."""
    items = []
    for p, cid, qty, pname, cname, rgb, url in con.execute(
            "SELECT ps.part_num, ps.color_id, ps.qty, p.name, c.name, c.rgb, i.url FROM part_sets ps "
            "JOIN parts p ON p.part_num = ps.part_num JOIN colors c ON c.id = ps.color_id "
            "LEFT JOIN part_img i ON i.part_num = ps.part_num AND i.color_id = ps.color_id "
            "WHERE ps.set_num=? AND ps.via_fig = 0 ORDER BY c.name, p.name", (set_num,)):
        figure = mapper.is_figure(("part", p))
        items.append({"key": "%s|%s" % (p, cid), "num": p, "name": pname, "colour": cname, "rgb": rgb,
                      "qty": qty, "img": url, "group": "Figures" if figure else cname, "figure": figure,
                      "n_sets": n_sets(con, p, cid)})
    for f, qty, fname, url in con.execute(
            "SELECT fs.fig_num, fs.qty, m.name, m.img_url FROM fig_sets fs JOIN minifigs m ON m.fig_num = fs.fig_num "
            "WHERE fs.set_num=?", (set_num,)):
        items.append({"key": "fig|%s" % f, "num": f, "name": fname, "colour": None, "rgb": None, "qty": qty,
                      "img": url, "group": "Figures", "figure": True,
                      "n_sets": con.execute("SELECT COUNT(*) FROM fig_sets WHERE fig_num=?", (f,)).fetchone()[0]})
    return items


_nsets_cache = {}


def n_sets(con, part, cid):
    k = (part, cid)
    if k not in _nsets_cache:
        _nsets_cache[k] = con.execute("SELECT COUNT(*) FROM part_sets WHERE part_num=? AND color_id=?", k).fetchone()[0]
    return _nsets_cache[k]


def seen_keys(evidence):
    """Inventory keys that the photographed pieces account for."""
    keys = set()
    for ev in evidence:
        for k in ev.keys:
            if k.startswith("fig-"):
                keys.add("fig|" + k)
            elif ev.color_id is not None:
                keys.add("%s|%s" % (k, ev.color_id))
            else:
                keys.add(k + "|*")
    return keys


def is_seen(item, keys, mapper):
    if item["key"] in keys:
        return True
    num = item["num"]
    for fam in mapper.family(num) if not item["key"].startswith("fig|") else []:
        if fam + "|*" in keys or "%s|%s" % (fam, item["key"].split("|")[1]) in keys:
            return True
    return False


def distinctive(item):
    return item["figure"] or "pr" in item["num"] or item["n_sets"] <= 10
