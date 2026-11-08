"""Translate Brickognize answers (BrickLink numbers and names) into Rebrickable parts, minifigs and colours.

Brickognize names items by their BrickLink number. Rebrickable mostly uses the same number for
plain moulds, but printed parts, assemblies and Duplo figures are numbered differently. The routes,
tried in order, all work offline on the Rebrickable files:

1. element: a LEGO element number in the BrickLink name, e.g. "(6301361)", looked up in elements.csv.
2. same-number: the BrickLink number exists in Rebrickable and the names agree.
3. print: "<mould>pb<n>" -> the best name match among Rebrickable prints of that mould
   (and of moulds Rebrickable marks as variants of it). "pr" + 4 digits is only a hint.
4. assembly: "<mould>c01" -> the best name match among the mould and its assemblies.
5. name: the best name match among all parts and minifigs of the same family (Duplo or not).

Every result carries its route and a confidence, so the agent can check the doubtful ones by eye.
"""
import math
import re

STOP = {
    "duplo", "with", "and", "the", "of", "a", "an", "on", "in", "for", "to", "at", "or", "x",
    "lego", "ville", "pattern", "print", "printed", "utensil", "furniture", "wear", "type",
    "sticker", "molded", "moulded", "assembly", "set", "sets",
}
SYNONYM = {"grey": "gray", "colour": "color", "minifigure": "figure", "minifig": "figure"}
DUPLO_CATS = (4, 57)
FIGURE_CAT = 57

# BrickLink colour names that Rebrickable spells differently.
COLOR_ALIAS = {
    "neon yellow": "vibrant yellow",
    "trans neon yellow": "trans neon yellow",
    "light gray": "light gray",
    "milky white": "glow in dark white",
}

_tok_re = re.compile(r"[a-z0-9]+")
# What may follow a mould number: a letter variant, an assembly suffix, a print suffix.
_VARIANT = re.compile(r"^[a-z]?(c\d+[a-z]?)?(pr\d+[a-z]?)?$")


def tokens(text):
    text = text.lower().replace("'s ", " ").replace("’s ", " ")
    out = []
    for t in _tok_re.findall(text):
        t = SYNONYM.get(t, t)
        if t in STOP:
            continue
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


def norm_color(name):
    n = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return COLOR_ALIAS.get(n, n)


class Mapper:
    def __init__(self, con):
        self.con = con
        self.parts = {}
        for r in con.execute("SELECT part_num, name, cat FROM parts"):
            self.parts[r[0]] = (r[1], r[2])
        self.figs = {r[0]: r[1] for r in con.execute("SELECT fig_num, name FROM minifigs")}
        self.colors = {}
        self.color_names = {}
        for r in con.execute("SELECT id, name FROM colors"):
            self.colors[norm_color(r[1])] = r[0]
            self.color_names[r[0]] = r[1]
        self.used_parts = {r[0] for r in con.execute("SELECT DISTINCT part_num FROM part_sets")}
        self.used_figs = {r[0] for r in con.execute("SELECT DISTINCT fig_num FROM fig_sets")}
        self.prints = {}
        self.related = {}
        for rel, child, parent in con.execute("SELECT rel, child, parent FROM rels"):
            if rel == "P":
                self.prints.setdefault(parent, set()).add(child)
            elif rel in ("M", "A"):
                self.related.setdefault(parent, set()).add(child)
                self.related.setdefault(child, set()).add(parent)
        # Rebrickable files a Duplo figure under its torso colour, which BrickLink names in words.
        fig_colors = {}
        for p, cid in con.execute(
                "SELECT DISTINCT ps.part_num, ps.color_id FROM part_sets ps JOIN parts p ON p.part_num = ps.part_num "
                "WHERE p.cat = ?", (FIGURE_CAT,)):
            fig_colors.setdefault(p, []).append(self.color_names.get(cid, ""))
        # Token weights over every name we can match against.
        self._tok = {}
        df = {}
        for key, name in self._all_names():
            extra = " ".join(fig_colors.get(key[1], [])) if key[0] == "part" else ""
            ts = frozenset(tokens(name + " " + extra))
            self._tok[key] = ts
            for t in ts:
                df[t] = df.get(t, 0) + 1
        n = len(self._tok) or 1
        self.idf = {t: math.log(1 + n / c) for t, c in df.items()}
        self._pools = {}
        self.fig_colors = fig_colors
        names = sorted({c for c in self.color_names.values() if c and not c.startswith("[")}, key=len, reverse=True)
        self._color_re = "(" + "|".join(re.escape(c) for c in names) + ")"

    def _all_names(self):
        for p, (name, _cat) in self.parts.items():
            yield ("part", p), name
        for f, name in self.figs.items():
            yield ("fig", f), name

    # ---- similarity -------------------------------------------------------------------------
    def sim(self, query_tokens, key):
        q = {t for t in query_tokens if t in self.idf}
        c = self._tok.get(key, frozenset())
        if not q or not c:
            return 0.0
        common = sum(self.idf[t] for t in q & c)
        if not common:
            return 0.0
        return common / math.sqrt(sum(self.idf[t] for t in q) * sum(self.idf[t] for t in c))

    def _pool(self, family, mode):
        """Candidate keys for a name search. mode: parts, figures or figures+parts."""
        k = (family, mode)
        if k in self._pools:
            return self._pools[k]
        keys = []
        for p, (name, cat) in self.parts.items():
            if p not in self.used_parts:
                continue
            duplo = cat in DUPLO_CATS
            if (family == "duplo") != duplo:
                continue
            is_fig = cat == FIGURE_CAT or (not duplo and "minifig" in name.lower())
            if mode == "figures" and not is_fig:
                continue
            keys.append(("part", p))
        if mode != "parts":
            for f, name in self.figs.items():
                if f in self.used_figs and ("duplo" in name.lower()) == (family == "duplo"):
                    keys.append(("fig", f))
        self._pools[k] = keys
        return keys

    def _rank(self, query, keys, bonus=None):
        qt = tokens(query)
        scored = []
        for key in keys:
            s = self.sim(qt, key)
            if bonus and key in bonus:
                s += bonus[key]
            if s > 0:
                scored.append((s, key))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored

    def name_of(self, key):
        kind, num = key
        if kind == "part":
            return self.parts.get(num, ("?", None))[0]
        return self.figs.get(num, "?")

    def is_figure(self, key):
        kind, num = key
        return kind == "fig" or self.parts.get(num, ("", None))[1] == FIGURE_CAT

    # ---- the routes -------------------------------------------------------------------------
    def variants(self, mould, prints_only=False):
        """Rebrickable parts that are this mould, a letter variant, an assembly or a print of it."""
        out = set()
        for mo in {mould} | self.related.get(mould, set()):
            out |= {p for p in self.prints.get(mo, ())}
            for p in self.parts:
                if p.startswith(mo) and _VARIANT.match(p[len(mo):]):
                    out.add(p)
        out = {p for p in out if p in self.used_parts}
        if prints_only:
            out = {p for p in out if "pr" in p[len(mould):] or p in self.prints.get(mould, ())}
        return [("part", p) for p in sorted(out)]

    def map_item(self, item):
        """item: one Brickognize candidate (id, name, type, category). Returns a mapping dict."""
        bl_id = item.get("id", "")
        name = item.get("name", "")
        kind = item.get("type", "part")
        duplo = name.lower().startswith("duplo") or "duplo" in (item.get("category") or "").lower()
        family = "duplo" if duplo else "system"

        # 1. element numbers written into the BrickLink name
        found = []
        for group in re.findall(r"\(([\d\s/]+)\)", name):
            for eid in re.findall(r"\d{6,8}", group):
                r = self.con.execute("SELECT part_num, color_id FROM elements WHERE element_id=?", (eid,)).fetchone()
                if r and r[0] in self.parts and r[0] not in [f[0] for f in found]:
                    found.append((r[0], r[1]))
        if found:
            cands = [self._cand(("part", p), name, color_id=c) for p, c in found]
            return self._result(bl_id, "element", "high", cands)

        if kind == "fig":
            figure_only = "figure" in name.lower()
            pool = self._pool(family, "figures" if figure_only else "figures+parts")
            if family == "duplo" and figure_only:
                return self._pick_figure(bl_id, name, pool)
            ranked = self._rank(name, pool)
            return self._pick(bl_id, "name", ranked, strict=False)

        m_print = re.match(r"^(.+?)(?:pb|px|pat)(\d+)[a-z]?$", bl_id)
        m_asm = re.match(r"^(.+?)c(\d+)[a-z]?$", bl_id)

        # 2. same number: plain mould numbers agree between BrickLink and Rebrickable
        if bl_id in self.used_parts and not m_print:
            c = self._cand(("part", bl_id), name)
            return self._result(bl_id, "same-number", "high" if c["sim"] >= 0.2 else "medium", [c])

        # 3. printed part
        if m_print:
            mould, n = m_print.group(1), int(m_print.group(2))
            # Prints first; Rebrickable files some BrickLink prints as assemblies (mould + c01).
            keys = self.variants(mould, prints_only=True) or [k for k in self.variants(mould) if k[1] != mould]
            hint = {("part", mo + "pr%04d" % n): 0.08 for mo in {mould} | self.related.get(mould, set())}
            ranked = self._rank(name, keys, bonus=hint)
            if ranked and ranked[0][0] >= 0.3:
                return self._pick(bl_id, "print", ranked, strict=True)
            # Print not found under the mould: search by name, keep the plain mould as a weak extra.
            plain = [k for k in self.variants(mould) if "pr" not in k[1][len(mould):]]
            plain.sort(key=lambda k: (k[1] == mould, k[1]))  # assemblies before the bare mould
            ranked2 = self._rank(name, self._pool(family, "parts"))
            res = self._pick(bl_id, "name", ranked2, strict=True)
            if res["confidence"] in ("high", "medium"):
                return res
            extra = [self._cand(k, name) for k in plain[:4]]
            return self._result(bl_id, "name+plain-mould", "low", (res["candidates"][:2] + extra)[:6])

        # 4. assembly or a plain number that Rebrickable only has as variants
        mould = m_asm.group(1) if (m_asm and bl_id not in self.parts) else bl_id
        keys = self.variants(mould)
        if keys:
            ranked = self._rank(name, keys, bonus={("part", mould): 0.05})
            if ranked and ranked[0][0] >= 0.25:
                return self._pick(bl_id, "variant", ranked, strict=False)

        # 5. name search in the family
        ranked = self._rank(name, self._pool(family, "parts"))
        return self._pick(bl_id, "name", ranked, strict=True)

    # ---- Duplo figures by description ------------------------------------------------------
    def _bl_figure_features(self, name):
        """Legs colour, torso colour and child flag from a BrickLink Duplo figure name."""
        legs = torso = None
        for seg in [s.strip() for s in name.split(",")]:
            m = re.match(self._color_re + r" Legs\b", seg)
            if m and not legs:
                legs = m.group(1)
                continue
            m = re.match(self._color_re + r" (?:[A-Z][a-z]+ )?(?:Top|Shirt|Jacket|Vest|Sweater|Dress|Suit|Overalls?|"
                         r"Hoodie|Coat|Blouse|Outfit|Tunic|Body|Uniform)\b", seg)
            if m and not torso:
                torso = m.group(1)
        return legs, torso, "child" in name.lower()

    def _rb_figure_features(self, key):
        name = self.name_of(key)
        m = re.search(self._color_re + r" Legs\b", name)
        torso = self.fig_colors.get(key[1], []) if key[0] == "part" else []
        return (m.group(1) if m else None), torso, "child" in name.lower()

    def _pick_figure(self, bl_id, name, pool):
        legs, torso, child = self._bl_figure_features(name)
        qt = tokens(name)
        scored = []
        for key in pool:
            s = self.sim(qt, key)
            if s <= 0:
                continue
            r_legs, r_torso, r_child = self._rb_figure_features(key)
            if legs and r_legs:
                s += 0.2 if legs == r_legs else -0.3
            if torso and r_torso:
                s += 0.3 if torso in r_torso else -0.3
            if child != r_child:
                s -= 0.2
            scored.append((s, key))
        scored.sort(key=lambda x: (-x[0], x[1]))
        if not scored or scored[0][0] < 0.35:
            return self._pick(bl_id, "figure-name", scored, strict=False)
        best = scored[0][0]
        # Rebrickable splits some figures that BrickLink treats as one (other hair moulds), so keep
        # every close runner-up; the set ranking then decides which one the other pieces support.
        close = [r for r in scored[:6] if r[0] >= best - 0.25]
        if best >= 0.9 and len(close) == 1:
            conf = "high"
        else:
            conf = "medium" if len(close) <= 4 else "low"
        return self._result(bl_id, "figure-name", conf, [self._cand(k, "", sim=s) for s, k in close[:4]])

    def _cand(self, key, query, color_id=None, sim=None):
        if sim is None:
            sim = self.sim(tokens(query), key)
        return {"kind": key[0], "num": key[1], "name": self.name_of(key), "sim": round(sim, 3),
                "figure": self.is_figure(key), "color_id": color_id}

    def _pick(self, bl_id, method, ranked, strict):
        if not ranked:
            return self._result(bl_id, method, "none", [])
        best = ranked[0][0]
        close = [r for r in ranked[:5] if r[0] >= best - 0.06]
        if best >= 0.55 and len(close) == 1:
            conf = "high"
        elif best >= (0.4 if strict else 0.3):
            conf = "medium" if len(close) <= 3 else "low"
        else:
            conf = "low"
        if best < 0.2:
            conf = "none"
        keep = close[:3] if conf != "high" else ranked[:1]
        return self._result(bl_id, method, conf, [self._cand(k, "", sim=s) for s, k in keep])

    @staticmethod
    def _result(bl_id, method, conf, cands):
        return {"bl_id": bl_id, "method": method, "confidence": conf, "candidates": cands}

    # ---- colours ----------------------------------------------------------------------------
    def rb_color(self, bl_color_name):
        n = norm_color(bl_color_name)
        if n in self.colors:
            return self.colors[n]
        if n.startswith("satin "):
            return self.colors.get("opal " + n[6:])
        return None

    def family(self, part_num):
        """The part plus moulds Rebrickable marks as variants or alternates of it."""
        return {part_num} | self.related.get(part_num, set())

    def colors_of(self, part_num):
        fam = tuple(self.family(part_num))
        q = "SELECT DISTINCT color_id FROM part_sets WHERE part_num IN (%s)" % ",".join("?" * len(fam))
        return {r[0] for r in self.con.execute(q, fam)}

    def choose_color(self, cand, bk_colors):
        """Pick the Rebrickable colour id for a part candidate.

        Brickognize's colour guesses are tried best first; the first one that the part is known
        to exist in wins. Returns (color_id or None, how) where how is element, brickognize,
        brickognize-2nd, not-in-catalogue or none.
        """
        if cand["figure"] or cand["kind"] == "fig":
            return None, "figure"
        if cand.get("color_id") is not None:
            return cand["color_id"], "element"
        known = self.colors_of(cand["num"])
        tried = False
        for i, c in enumerate(bk_colors or []):
            cid = self.rb_color(c.get("name", ""))
            if cid is None:
                continue
            tried = True
            if cid in known:
                return cid, "brickognize" if i == 0 else "brickognize-%d" % (i + 1)
        if len(known) == 1 and not bk_colors:
            return next(iter(known)), "only-colour"
        return None, "not-in-catalogue" if tried else "none"

    def narrow_by_color(self, cands, bk_colors):
        """Keep the candidates that exist in Brickognize's best colour, when that leaves any."""
        if len(cands) < 2 or not bk_colors:
            return cands
        cid = self.rb_color(bk_colors[0].get("name", ""))
        if cid is None:
            return cands
        keep = [c for c in cands if c["kind"] == "part" and not c["figure"] and cid in self.colors_of(c["num"])]
        return keep or cands
