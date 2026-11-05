"""Pull full team details (sets + rental codes) out of team-report pages.

Ported from the Kōchiku Scout companion project (see reports.py).

Three levels of confidence, recorded per team as `setsFrom`:
  "structured" — yakkun.com post pages and pokesol.app pokemon-cards (exact data)
  "parsed"     — free-form blog text (note, Hatena, …) matched against name dictionaries
  None         — nothing usable (sets are usually in images; open the article)
"""
from __future__ import annotations

import csv
import html
import io
import json
import re
import unicodedata
from collections import defaultdict

from .turbostream import decode_page

NFKC = lambda s: unicodedata.normalize("NFKC", s or "")  # noqa: E731

STATS = ["hp", "attack", "defense", "specialAttack", "specialDefense", "speed"]
STAT_JA = {"HP": 0, "H": 0, "攻撃": 1, "こうげき": 1, "A": 1, "防御": 2, "ぼうぎょ": 2, "B": 2,
           "特攻": 3, "とくこう": 3, "C": 3, "特防": 4, "とくぼう": 4, "D": 4, "素早": 5, "素早さ": 5, "すばやさ": 5, "S": 5}
# Japanese nature table order (pokesol nature ids 1–20, then neutral 21–25)
NATURE_ORDER = ["さみしがり", "いじっぱり", "やんちゃ", "ゆうかん", "ずぶとい", "わんぱく", "のうてんき", "のんき",
                "ひかえめ", "おっとり", "うっかりや", "れいせい", "おだやか", "おとなしい", "しんちょう", "なまいき",
                "おくびょう", "せっかち", "ようき", "むじゃき", "がんばりや", "すなお", "てれや", "きまぐれ", "まじめ"]

_KANA = re.compile(r"[ぁ-ゟァ-ヿー]")
_BULLETS = r"\s・●○■□◆◇★☆▶▷►▼◎※【】\[\]()（）《》〈〉「」#＃0-9０-９.．、,:：\-－—|/＞>"

RENTAL_CTX = re.compile(r"(レンタル|ﾚﾝﾀﾙ|rental|チームID|チームＩＤ|ID|コード|code)", re.I)
RENTAL_CODE = re.compile(r"(?<![0-9A-Za-z])([0-9A-Z]{10})(?![0-9A-Za-z])")


def rental_codes(text: str) -> list[str]:
    """10-character team codes that sit near a 'rental/ID/code' keyword."""
    text = NFKC(text)
    found = []
    for m in RENTAL_CODE.finditer(text):
        code = m.group(1)
        digits = sum(c.isdigit() for c in code)
        if not (2 <= digits <= 8):           # real codes mix letters and digits
            continue
        window = text[max(0, m.start() - 120): m.end() + 40]
        if RENTAL_CTX.search(window) and code not in found:
            found.append(code)
    return found[:4]


class Lexicon:
    """Japanese → id dictionaries for moves, abilities, items and natures (kana and kanji forms)."""

    def __init__(self, fetch_csv):
        self.en = {}
        self.ja = {}
        self.index = {}
        for kind, table, idcol in (("move", "move_names", "move_id"), ("ability", "ability_names", "ability_id"),
                                   ("item", "item_names", "item_id"), ("nature", "nature_names", "nature_id")):
            names = defaultdict(dict)
            for r in csv.DictReader(io.StringIO(fetch_csv(table))):
                names[r[idcol]][r["local_language_id"]] = r["name"]
            lookup = {}
            for i, v in names.items():
                key = (kind, int(i))
                if "9" in v:
                    self.en[key] = v["9"]
                self.ja[key] = v.get("1") or v.get("11") or v.get("9", "")
                for lang in ("1", "11"):
                    if lang in v:
                        lookup[NFKC(v[lang])] = key
            self.index[kind] = lookup
        self.nature_ja2en = {self.ja[k]: self.en.get(k, "") for k in self.ja if k[0] == "nature"}
        self._rx = {}

    def regex(self, kind: str) -> re.Pattern:
        if kind not in self._rx:
            words = sorted((w for w in self.index[kind] if len(w) >= 2), key=len, reverse=True)
            self._rx[kind] = re.compile("|".join(map(re.escape, words)))
        return self._rx[kind]

    def find(self, kind: str, text: str) -> list[tuple[int, tuple]]:
        """Dictionary hits, skipping ones glued to a longer kana word (なまけ inside なまける)."""
        out = []
        for m in self.regex(kind).finditer(text):
            nxt = text[m.end(): m.end() + 1]
            if nxt and _KANA.match(nxt) and _KANA.match(m.group(0)[-1]):
                continue
            out.append((m.start(), self.index[kind][m.group(0)]))
        return out

    def name(self, key, lang="ja") -> str:
        return (self.en if lang == "en" else self.ja).get(key, "")


def _sp_from_text(s: str) -> list[int] | None:
    """'HP:24 / 防御:11 / 素早:19' or 'H32 A2 S32' → [h,a,b,c,d,s] stat points (Champions: 0–32 each)."""
    s = NFKC(s)
    pts = [0] * 6
    hits = 0
    for m in re.finditer(r"(HP|攻撃|防御|特攻|特防|素早さ?|こうげき|ぼうぎょ|とくこう|とくぼう|すばやさ|(?<![A-Za-z])[HABCDS](?![A-Za-z]))\s*[:：]?\s*(\d{1,3})", s):
        idx = STAT_JA.get(m.group(1))
        val = int(m.group(2))
        if idx is not None and val <= 252:
            pts[idx] = val
            hits += 1
    if hits < 2:
        return None
    if max(pts) > 32:                      # written as old 252-style EVs; keep proportional points
        pts = [round(p / 8) if p else 0 for p in pts]
    return pts if sum(pts) <= 70 else None


# ───────────────────────── yakkun.com ─────────────────────────

def parse_yakkun(page: str, lex: Lexicon) -> dict:
    sets = []
    for block in page.split('<ul class="pokemon_list"')[1:]:
        block = block.split("</ul>")[0] + block.split("</ul>")[1].split('<ul class="pokemon_list"')[0][:4000]
        name = re.search(r'list_name"><strong><a[^>]*>(.*?)</a>', block)
        item = re.search(r'list_item">@<a[^>]*>(.*?)</a>', block)
        nature = re.search(r'list_nature">\(<a[^>]*>(.*?)</a>', block)
        ability = re.search(r'list_ability"><a[^>]*>(.*?)</a>', block)
        ev = re.search(r'list_ev">(.*?)<div', block, re.S)
        stats = re.search(r'list_stats">実数値:(.*?)</li>', block, re.S)
        moves = re.findall(r'<a href="/ch/zukan/search/\?move=(\d+)"[^>]*>(.*?)</a>', block)
        memo = re.search(r'read_more_item flat">(.*?)</div>', block, re.S)
        sets.append(dict(
            name=_t(name.group(1)) if name else "",
            item=_t(item.group(1)) if item else "",
            nature=_t(nature.group(1)) if nature else "",
            ability=_t(ability.group(1)) if ability else "",
            sp=_sp_from_text(_t(ev.group(1))) if ev else None,
            stats=re.sub(r"\s+", "", _t(stats.group(1))) if stats else "",
            moves=[_t(m[1]) for m in moves][:4],
            note=_t(memo.group(1).replace("<br />", " "))[:320] if memo else ""))
    msg = re.search(r'<div class="message">(.*?)</div></div>', page, re.S)
    body = _t(re.sub(r"<br ?/?>|</p>", "\n", msg.group(1))) if msg else ""
    rental = rental_codes(body)
    has_rental = "レンタルチーム有" in page
    return dict(sets=sets, rental=rental, rentalFlag=has_rental, setsFrom="structured" if sets else None,
                blurb=body[:600])


# ───────────────────────── pokesol.app ─────────────────────────

class PokesolMaster:
    def __init__(self, root: dict):
        md = next(v["masterData"] for v in root["loaderData"].values() if isinstance(v, dict) and "masterData" in v)
        self.pokemon = {p["id"]: p["name"] for p in md["pokemons"]}
        self.moves = {m["id"]: m["name"] for m in md["moves"]}
        self.items = {i["id"]: i["name"] for i in md["items"]}
        self.abilities = {a["id"]: a["name"] for a in md["abilities"]}
        self.natures = {n["id"]: n["name"] for n in md["natures"]}
        self.legal = {int(pid): [a["name"] for a in lst if isinstance(a, dict)]
                      for pid, lst in (md.get("abilitiesByPokemonId") or {}).items() if isinstance(lst, list)}

    FIELDS = ("pokemon", "moves", "items", "abilities", "natures", "legal")

    def to_json(self) -> dict:
        return {k: getattr(self, k, {}) for k in self.FIELDS}

    @classmethod
    def from_json(cls, d: dict) -> "PokesolMaster":
        o = cls.__new__(cls)
        for k in cls.FIELDS:
            setattr(o, k, {int(i): n for i, n in d.get(k, {}).items()})
        return o

    def legal_by_species(self, species_of) -> dict[int, set[str]]:
        """National dex number → every ability any of its forms can have (megas included).

        species_of(pokesol_pokemon_name) → national dex number or None.
        """
        out: dict[int, set[str]] = defaultdict(set)
        for pid, names in self.legal.items():
            sid = species_of(self.pokemon.get(pid, ""))
            if sid:
                out[sid].update(names)
        return out


def parse_pokesol(page: str, master: PokesolMaster | None) -> tuple[dict, PokesolMaster | None]:
    root = decode_page(page)
    if root and master is None:
        try:
            master = PokesolMaster(root)
        except Exception:
            master = None
    article = None
    if root:
        for v in root.get("loaderData", {}).values():
            if isinstance(v, dict) and isinstance(v.get("article"), dict):
                article = v["article"]
    body = article.get("body", "") if article else page
    sets = []
    for m in re.finditer(r'<div data-type="pokemon-card"([^>]*)>', body):
        attrs = {k: html.unescape(v) for k, v in re.findall(r'data-([\w-]+)="([^"]*)"', m.group(1))}
        if not master:
            break
        try:
            evs = json.loads(attrs.get("evs", "{}"))
            moves = json.loads(attrs.get("move-ids", "[]"))
            abil = json.loads(attrs.get("ability-ids", "[]"))
        except json.JSONDecodeError:
            continue
        nat = int(attrs["nature-id"]) if attrs.get("nature-id", "").isdigit() else None
        sets.append(dict(
            pid=int(attrs.get("pokemon-id", 0) or 0),
            name=master.pokemon.get(int(attrs.get("pokemon-id", 0) or 0), ""),
            item=master.items.get(int(attrs["item-id"]), "") if attrs.get("item-id", "").isdigit() else "",
            nature=master.natures.get(nat) or (NATURE_ORDER[nat - 1] if nat and 0 < nat <= 25 else ""),
            ability=master.abilities.get(abil[0], "") if abil else "",
            sp=[int(evs.get(k, 0) or 0) for k in STATS] if evs else None,
            stats="", moves=[master.moves.get(x, "") for x in moves if x][:4], note=""))
    text = _t(re.sub(r"<br ?/?>|</p>|</h\d>", "\n", body))
    return dict(sets=sets, rental=rental_codes(text), setsFrom="structured" if sets else None,
                blurb=""), master


# ───────────────────────── free-form blogs ─────────────────────────

def article_text(page: str, url: str) -> str:
    """Main text of a note.com API response or a blog page."""
    if page.lstrip().startswith("{"):
        try:
            body = json.loads(page)["data"]["body"]
        except Exception:
            body = ""
    else:
        body = page
        for pat in (r'<div class="entry-content[^"]*"[^>]*>(.*?)<div class="entry-footer',   # Hatena
                    r'<article[^>]*>(.*?)</article>', r'<main[^>]*>(.*?)</main>'):
            m = re.search(pat, page, re.S)
            if m and len(m.group(1)) > 500:
                body = m.group(1)
                break
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?i)<br ?/?>|</p>|</h\d>|</li>|</tr>|</div>", "\n", body)
    body = re.sub(r"(?i)</t[dh]>", " / ", body)
    text = NFKC(html.unescape(re.sub(r"<[^>]+>", " ", body)))
    text = re.sub(r"[ \t　]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text)


_SETLIKE = re.compile(r"@|＠|特性|性格|持ち物|持物|努力値|能力ポイント|ポイント|実数値|技構成|技:|技：|[HABCDS]\d|[HABCDS]\s*[:：]?\s*\d")


def _section_starts(text: str, aliases: list[str]) -> list[int]:
    """Candidate positions where this Pokémon's own section begins, best first.

    A heading-like mention (only bullets before it on its line, or a short line, or '@' after it)
    followed by set vocabulary beats a passing mention in running prose.
    """
    scored = []
    for alias in aliases:
        for m in re.finditer(re.escape(alias), text):
            ls = text.rfind("\n", 0, m.start()) + 1
            le = text.find("\n", m.end())
            le = len(text) if le < 0 else le
            before = text[ls:m.start()]
            line = text[ls:le]
            heading = (re.fullmatch(f"[{_BULLETS}]*", before) is not None) or len(line) <= 28 or "@" in line or "＠" in line
            follow = text[m.end(): m.end() + 400]
            setlike = len(_SETLIKE.findall(follow))
            if not setlike:
                continue
            scored.append((0 if heading else 1, -min(setlike, 6), m.start()))
    scored.sort()
    return [p for _, _, p in scored]


def _moves_in(sec: str, lex: "Lexicon") -> list[str]:
    lines = [l.strip() for l in sec.split("\n")][:45]
    # 1) one move per line, at the start of the line ("・アイススピナー(確定): …" / "- じしん")
    starts = []
    for l in lines:
        body = re.sub(f"^[{_BULLETS}]*", "", l)
        hit = lex.find("move", body[:16])
        if hit and hit[0][0] == 0:
            nm = lex.name(hit[0][1])
            if nm not in starts:
                starts.append(nm)
    # 2) a compact line listing several moves ("じしん / げきりん / つるぎのまい / みがわり")
    packed = []
    for l in lines:
        hits = []
        for _, k in lex.find("move", l):
            if lex.name(k) not in hits:
                hits.append(lex.name(k))
        if len(hits) >= 2 and len(l) < 120:
            for h in hits:
                if h not in packed:
                    packed.append(h)
            if len(packed) >= 4:
                break
    best = packed if len(packed) >= len(starts) else starts
    return best[:4] if len(best) >= 2 else []


def parse_freeform(text: str, team: list[tuple[str, list[str], set | None]], lex: Lexicon) -> dict:
    """Read sets out of a blog post.

    team: [(display_ja, aliases, legal_abilities_or_None)] in team order.
    """
    chosen: list[int | None] = []
    taken: list[int] = []
    for _, aliases, _ in team:
        pos = next((p for p in _section_starts(text, aliases) if all(abs(p - q) > 40 for q in taken)), None)
        chosen.append(pos)
        if pos is not None:
            taken.append(pos)
    ordered = sorted((p, i) for i, p in enumerate(chosen) if p is not None)
    sets: list[dict | None] = [None] * len(team)
    for n, (pos, i) in enumerate(ordered):
        end = ordered[n + 1][0] if n + 1 < len(ordered) else pos + 1600
        sec = text[pos: min(end, pos + 1600)]
        head = sec[:700]
        ja, _, legal = team[i]
        item = ""
        at = re.search(r"[@＠]\s*([^\s/\n(（]+)", head)
        if at:
            hit = lex.find("item", NFKC(at.group(1)))
            item = lex.name(hit[0][1]) if hit else ""
        if not item:
            m = re.search(r"(持ち物|持物|道具)\s*[:：]?\s*(\S+)", head)
            hit = lex.find("item", m.group(2)) if m else []
            item = lex.name(hit[0][1]) if hit else ""
        nature = next((lex.name(k) for _, k in lex.find("nature", head)), "")
        abil = ""
        for _, k in lex.find("ability", head):
            nm = lex.name(k)
            if (legal and nm in legal) or (not legal and re.search(r"特性", head)):
                abil = nm
                break
        moves = _moves_in(sec, lex)
        sp = None
        for line in head.split("\n"):
            sp = _sp_from_text(line)
            if sp:
                break
        stats = re.search(r"(\d{2,3}(?:\(\d+\))?(?:-|－|‐)\d{2,3}(?:\(\d+\))?(?:(?:-|－|‐)\d{2,3}(?:\(\d+\))?){4})", head)
        if not sp and stats and "(" in stats.group(1):   # "171(22)-94-118(11)-…": points in brackets
            parts = re.split(r"[-－‐]", stats.group(1))
            pts = [int(m.group(1)) if (m := re.search(r"\((\d+)\)", x)) else 0 for x in parts]
            if len(pts) == 6 and 0 < sum(pts) <= 66 and max(pts) <= 32:
                sp = pts
        filled = sum(bool(x) for x in (item, nature, abil, moves, sp))
        if filled >= 2 or len(moves) >= 3:
            sets[i] = dict(name=ja, item=item, nature=nature, ability=abil, sp=sp,
                           stats=stats.group(1) if stats else "", moves=moves, note="")
    got = sum(1 for s in sets if s)
    return dict(sets=sets if got >= 2 else [], rental=rental_codes(text), setsFrom="parsed" if got >= 2 else None)


def _t(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()
