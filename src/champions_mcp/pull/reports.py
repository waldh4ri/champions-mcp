"""Complete BSS teams from players' published team reports (構築記事).

pokedb.tokyo's open data gives the top ladder teams as species + item only.
The *full* sets (moves, ability, nature, Stat Point spread) exist only in the
players' own team reports, which several Japanese sites index:

  pokedb.tokyo    構築記事検索 — the largest index, with final rank and rating
  yakkun.com      team board (structured set pages)
  damekei.com     build-article digest
  pokemon-teams   rental teams shared on X

Each linked article is read once (cached) and its sets extracted: pokesol.app
team pages and yakkun posts are structured; blog text (note, Hatena...) is
parsed against PokeAPI's Japanese name tables (reports_extract.py).

Ported from the Kōchiku Scout companion project, trimmed to what the
simulator needs and restricted to the regulations being ingested, so a pull
only reads the current regulation's reports. Results land in ``full_teams``
(only teams that resolve completely against the Champions data).

The scrape is synchronous (threads, one polite lane per host) and runs in a
worker thread from the async pull. Pages are cached under
``<data_dir>/reports-cache``: listings refresh after LISTING_TTL, articles and
name tables are kept.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..champions_stats import validate_spread
from ..db import Db
from ..names import try_resolve
from . import reports_extract as extract
from .builds import POKEDB_SEASON_REGULATION

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
POKEAPI_CSV = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/{}.csv"
LISTING_TTL = 12 * 3600          # re-read index/listing pages twice a day
NAME_TABLE_TTL = 30 * 86400      # PokeAPI name tables
HOST_DELAY = 0.6                 # seconds between requests on one lane to a host
HOST_PARALLEL = {"pokesol.app": 4, "raw.githubusercontent.com": 8}


# ───────────────────────── fetching + cache ─────────────────────────

class Fetcher:
    """GET with a browser UA (several sites 403 default clients), cached on disk.

    Thread-safe; requests to one host are serialised per lane and spaced by
    HOST_DELAY. ``max_age=None`` keeps a cached page forever.
    """

    def __init__(self, cache_dir: Path) -> None:
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._slots: dict[str, threading.Semaphore] = {}
        self._lock = threading.Lock()
        self.errors: Counter = Counter()

    def _slot(self, host: str) -> threading.Semaphore:
        with self._lock:
            if host not in self._slots:
                self._slots[host] = threading.Semaphore(HOST_PARALLEL.get(host, 1))
            return self._slots[host]

    def get(self, url: str, *, max_age: float | None = None, encoding: str | None = "utf-8") -> str | None:
        path = self.dir / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".html")
        fresh = path.exists() and (max_age is None or time.time() - path.stat().st_mtime < max_age)
        if fresh:
            data = path.read_bytes()
        else:
            host = urllib.parse.urlsplit(url).netloc
            with self._slot(host):
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
                try:
                    with urllib.request.urlopen(req, timeout=20) as r:
                        data = r.read()
                except urllib.error.HTTPError as e:
                    if e.code in (404, 410, 403):
                        path.write_bytes(b"")  # remember misses so later pulls stay fast
                        return None
                    self.errors[f"HTTP {e.code}"] += 1
                    return self._stale(path, encoding)
                except Exception as e:  # network hiccup: keep going with the other pages
                    self.errors[type(e).__name__] += 1
                    return self._stale(path, encoding)
                finally:
                    time.sleep(HOST_DELAY)
            path.write_bytes(data)
        return self._decode(data, encoding)

    def _stale(self, path: Path, encoding: str | None) -> str | None:
        """A failed refresh falls back to the last cached copy, if any."""
        return self._decode(path.read_bytes(), encoding) if path.exists() else None

    @staticmethod
    def _decode(data: bytes, encoding: str | None) -> str | None:
        if not data:
            return None
        if encoding is None:
            m = re.search(rb'charset=["\']?([\w-]+)', data[:4000])
            encoding = m.group(1).decode() if m else "utf-8"
        try:
            return data.decode(encoding, "replace")
        except LookupError:
            return data.decode("utf-8", "replace")


class ReplayFetcher(Fetcher):
    """Serves recorded pages ({url: text}) and never touches the network.

    Used by the tests and CI (tests/fixtures/corpus), so the scrapers and
    article parsers run for real on recorded pages without a long scrape.
    """

    def __init__(self, pages: dict[str, str], cache_dir: Path) -> None:
        super().__init__(cache_dir)
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url: str, *, max_age: float | None = None, encoding: str | None = "utf-8") -> str | None:
        self.requested.append(url)
        return self.pages.get(url) or None


def clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


# ───────────────────────── names & species ─────────────────────────

class Dex:
    """Maps the many ways Japanese sites write a Pokémon to one canonical record."""

    FORM_JA = {"ヒスイ": "Hisui", "ガラル": "Galar", "アローラ": "Alola", "パルデア": "Paldea",
               "オス": "M", "メス": "F", "えいえん": "Eternal", "永遠": "Eternal",
               "炎": "Blaze", "ほのお": "Blaze", "水": "Aqua", "みず": "Aqua", "格闘": "Combat", "かくとう": "Combat"}
    REGION_EN = {"Hisui": "Hisuian", "Galar": "Galarian", "Alola": "Alolan", "Paldea": "Paldean"}
    ROTOM = {"ウォッシュ": "Wash", "ヒート": "Heat", "スピン": "Fan", "フロスト": "Frost", "カット": "Mow"}

    def __init__(self, csv_text):
        names = csv.DictReader(io.StringIO(csv_text("pokemon_species_names")))
        self.ja2id, self.id2en, self.id2ja = {}, {}, {}
        for r in names:
            sid, lang, name = int(r["pokemon_species_id"]), r["local_language_id"], r["name"]
            if lang in ("1", "11"):
                self.ja2id[unicodedata.normalize("NFKC", name)] = sid
                if lang == "11" or sid not in self.id2ja:
                    self.id2ja[sid] = name
            elif lang == "9":
                self.id2en[sid] = name
        self.pid2species = {}
        self.slug2pid = {}
        for r in csv.DictReader(io.StringIO(csv_text("pokemon"))):
            self.slug2pid[r["identifier"]] = int(r["id"])
            self.pid2species[int(r["id"])] = int(r["species_id"])
        self.mons: dict[str, dict] = {}

    def resolve(self, raw: str, *, dex_hint: int | None = None) -> dict | None:
        """Canonical record for a Japanese name such as 'メガリザードンＹ' or 'イエッサン♂'."""
        s = unicodedata.normalize("NFKC", raw or "").strip()
        if not s or s in ("不明", "?"):
            return None
        mega = s.startswith("メガ") and s not in self.ja2id
        if mega:
            s = s[2:]
        form = None
        if "♂" in s or "♀" in s:
            form = "F" if "♀" in s else "M"
            s = s.replace("♂", "").replace("♀", "")
        m = re.search(r"[(:：](.+?)\)?$", s)
        if m:
            label = re.sub(r"(のすがた|のはな|のフォルム|フォルム|種)", "", m.group(1)).strip("・ ")
            for k, v in self.FORM_JA.items():
                if k in label:
                    form = v
                    break
            s = s[:m.start()].strip()
        for k, v in self.ROTOM.items():
            if s == k + "ロトム":
                s, form = "ロトム", v
        suffix = ""
        m = re.match(r"(.+?)([XYZ])$", s)
        if m and m.group(1) in self.ja2id:
            s, suffix = m.group(1), m.group(2)
        sid = self.ja2id.get(s) or dex_hint
        if not sid:
            return None
        if form == "M":
            form = None  # male is the default form everywhere we care about
        key = f"{sid}{'-' + form if form else ''}{'-mega' + suffix if mega else ''}"
        if key not in self.mons:
            en = self.id2en.get(sid, s)
            if form in self.REGION_EN:
                en = f"{self.REGION_EN[form]} {en}"
            elif form:
                en = f"{en} ({form})"
            self.mons[key] = dict(key=key, dex=sid, form=form, mega=mega,
                                  en=("Mega " if mega else "") + en + (f" {suffix}" if suffix else ""),
                                  ja=("メガ" if mega else "") + self.id2ja.get(sid, s) + suffix)
        return self.mons[key]


# ───────────────────────── seasons ─────────────────────────

def _seasons_for(regulations: tuple[str, ...]) -> list[int]:
    return sorted(n for n, reg in POKEDB_SEASON_REGULATION.items() if reg in regulations)


def _season_of_month(y: int, mth: int, last: int) -> int:
    """Approximate ladder season for a tournament month (M-1 started April 2026)."""
    return max(1, min(last, (y - 2026) * 12 + mth - 4))


def _event_from_tags(text: str, last: int) -> tuple[str, int | None]:
    """-> (event kind, season played)."""
    text = unicodedata.normalize("NFKC", text)
    m = re.search(r"シーズン\s*M-?(\d+)", text)
    if m:
        return "Ladder", int(m.group(1))
    m = re.search(r"(20\d\d)\.(\d\d)", text)
    if "マンスリー" in text or "MCS" in text or "グローバル" in text:
        return "Online challenge", _season_of_month(int(m.group(1)), int(m.group(2)), last) if m else None
    by_reg: dict[str, int] = {}
    for n, reg in POKEDB_SEASON_REGULATION.items():
        by_reg[reg] = max(n, by_reg.get(reg, 0))
    for reg, season in by_reg.items():
        if reg in text:
            return ("Official tournament" if "公式" in text else "Community tournament"), season
    return "Other", None


_RE_FINAL_RANK = re.compile(r"(?:最終|最終順位|final)\s*[:：]?\s*(?:世界)?\s*(\d{1,5})\s*位", re.I)
_RE_ANY_RANK = re.compile(r"(?<![\d.])(\d{1,5})\s*位")
_RE_RATE = re.compile(r"(?:レート|R|最終|最高|rate)\s*[:：]?\s*(2[0-9]{3}(?:\.\d+)?|1[5-9]\d{2}(?:\.\d+)?)", re.I)


def _rank_rate_from_title(t: str) -> tuple[int | None, float | None]:
    t = unicodedata.normalize("NFKC", t)
    m = _RE_FINAL_RANK.search(t) or _RE_ANY_RANK.search(t)
    rank = int(m.group(1)) if m else None
    m = _RE_RATE.search(t)
    return rank, (float(m.group(1)) if m else None)


# ───────────────────────── listing sources ─────────────────────────

def src_pokedb(f: Fetcher, dex: Dex, seasons: list[int]) -> list[dict]:
    """バトルデータベース チャンピオンズ: the largest index of 構築記事 (singles)."""
    out, page = [], 1
    while True:
        url = (f"https://champs.pokedb.tokyo/article/search?rule=0&season_start={seasons[0]}"
               f"&season_end={seasons[-1]}&per_page=100&page={page}")
        t = f.get(url, max_age=LISTING_TTL)
        cards = t.split('<article class="card article-card">')[1:] if t else []
        if not cards:
            break
        for card in cards:
            tags = " ".join(clean(x) for x in re.findall(r'<span class="tag[^"]*">(.*?)</span>', card))
            link = re.search(r'<footer.*?href="([^"]+)".*?<span>(.*?)</span>', card, re.S)
            if not link:
                continue
            mons = []
            for m in re.finditer(r'dex-(\d{4})-(\d\d)-96"\s*title="([^"]+)".*?article-card-pokemon__item-icon">(.*?)</div>', card, re.S):
                item = re.search(r'title="([^"]+)"', m.group(4))
                mons.append((dex.resolve(html.unescape(m.group(3)), dex_hint=int(m.group(1))),
                             html.unescape(item.group(1)) if item else ""))
            rank = re.search(r"<span>(\d+)位</span>", card)
            ri, rd = re.search(r'rating-integer">(\d+)<', card), re.search(r'rating-decimal">([^<]+)<', card)
            trainer = re.search(r'<p class="title is-6[^"]*">(.*?)</p>', card, re.S)
            kind, season = _event_from_tags(tags + " " + clean(link.group(2)), seasons[-1])
            out.append(dict(url=html.unescape(link.group(1)), title=clean(link.group(2)),
                            trainer=clean(trainer.group(1)) if trainer else "", kind=kind, season=season,
                            rank=int(rank.group(1)) if rank else None,
                            rate=float(ri.group(1) + (rd.group(1) if rd else "")) if ri else None,
                            mons=mons, src="pokedb"))
        if len(cards) < 100:
            break
        page += 1
    return out


def src_yakkun(f: Fetcher, dex: Dex, seasons: list[int]) -> list[dict]:
    """ポケモン徹底攻略 (yakkun.com) team board, showcase posts only."""
    out, start = [], 0
    while start < 3000:
        t = f.get(f"https://yakkun.com/bbs/party/list/?soft=4&rule=0&is_beginner=0&start={start}",
                  max_age=LISTING_TTL, encoding="euc-jp")
        boxes = t.split('<div class="party_list_box">')[1:] if t else []
        if not boxes:
            break
        for b in boxes:
            href = re.search(r'<a href="(/bbs/party/n\d+)"', b)
            title = re.search(r"<h5>(.*?)</h5>", b, re.S)
            alt = re.search(r'class="party_img"', b) and re.search(r'<img src="[^"]*party-\d+\.png[^"]*" alt="([^"]*)"', b)
            if not (href and title and alt):
                continue
            cup = re.search(r'party_list_cup">(.*?)</div>', b, re.S)
            user = re.search(r'user_name">(.*?)</span>', b, re.S)
            mons = []
            for slot in html.unescape(alt.group(1)).split("／"):
                name, _, item = slot.partition("@")
                mons.append((dex.resolve(name), item))
            ttl = clean(title.group(1))
            rank, rate = _rank_rate_from_title(ttl)
            kind, season = _event_from_tags(clean(cup.group(1)) if cup else "", seasons[-1])
            out.append(dict(url="https://yakkun.com" + href.group(1), title=ttl,
                            trainer=clean(user.group(1)) if user else "", kind=kind, season=season,
                            rank=rank, rate=rate, mons=mons, src="yakkun"))
        # newest first: stop once a whole page predates the regulation
        if len(boxes) < 15 or all((x.get("season") or seasons[0]) < seasons[0] for x in out[-len(boxes):]):
            break
        start += 15
    return out


def src_damekei(f: Fetcher, dex: Dex, seasons: list[int]) -> list[dict]:
    """ダメ計 build-article digest."""
    t = f.get("https://damekei.com/build-articles", max_age=LISTING_TTL)
    out = []
    for m in re.finditer(r'<a href="(https?://[^"]+)" target="_blank" rel="nofollow[^"]*"[^>]*>(.*?)</a>', t or "", re.S):
        url, body = m.groups()
        if "シングル" not in body:
            continue
        spans = [clean(x) for x in re.findall(r"<span[^>]*>(.*?)</span>", body, re.S)]
        ps = [clean(x) for x in re.findall(r"<p[^>]*>(.*?)</p>", body, re.S)]
        mons = []
        for sm in re.finditer(r'<div class="relative w-9[^"]*"><img src="([^"]+)"[^>]*>(?:<img src="/sprites/items/([^".]+)\.png")?', body):
            pid = int(re.search(r"(\d+)\.png", sm.group(1)).group(1))
            sid = dex.pid2species.get(pid, pid)
            rec = dex.resolve(dex.id2ja.get(sid, ""), dex_hint=sid)
            if rec and pid > 10000 and pid in dex.pid2species:
                slug = next((k for k, v in dex.slug2pid.items() if v == pid), "")
                if "-mega" in slug:
                    suf = slug.rsplit("-", 1)[-1].upper() if re.search(r"-mega-[xyz]$", slug) else ""
                    rec = dex.resolve("メガ" + dex.id2ja.get(sid, "") + suf, dex_hint=sid)
            mons.append((rec, ""))
        rank = next((int(re.sub(r"\D", "", s)) for s in spans if s.endswith("位")), None)
        rate = next((float(s[1:]) for s in spans if re.fullmatch(r"R\d{4}(\.\d+)?", s)), None)
        season = next((int(s[2:]) for s in spans if re.fullmatch(r"M-\d+", s)), None)
        title = ps[0] if ps else ""
        if rank is None:
            rank = _rank_rate_from_title(title)[0]
        out.append(dict(url=html.unescape(url), title=title, trainer=spans[-3] if len(spans) >= 3 else "",
                        kind="Ladder" if season else "Other", season=season, rank=rank, rate=rate,
                        mons=mons, src="damekei"))
    return out


def src_pteams(f: Fetcher, dex: Dex, seasons: list[int]) -> list[dict]:
    """ポケモンチャンピオンズ 構築コレクション: rental teams shared on X (species only)."""
    out, seen, page = [], set(), 1
    while page <= 60:
        t = f.get(f"https://pokemon-teams.pages.dev/search?format=single&sort=newest&page={page}", max_age=LISTING_TTL)
        cards = re.findall(r'<a href="(https://(?:x|twitter)\.com/[^"]+)"[^>]*class="block">(.*?)</a>', t or "", re.S)
        new = 0
        for url, body in cards:
            if url in seen or "Single" not in body:
                continue
            seen.add(url)
            new += 1
            season = re.search(r"M-(\d+)", body)
            rate = re.search(r'title="レート ([\d.]+)"', body)
            rank = re.search(r"RANK\s*#?\s*(\d+)|#(\d+)", clean(body))
            h3 = re.search(r"<h3[^>]*>(.*?)</h3>", body, re.S)
            names = re.findall(r'<img alt="([^"]+)"', body)
            out.append(dict(url=url, title=clean(h3.group(1)) if h3 else "",
                            trainer=clean(h3.group(1)).replace("さん の構築", "") if h3 else "",
                            kind="Ladder", season=int(season.group(1)) if season else None,
                            rank=int(next(g for g in rank.groups() if g)) if rank else None,
                            rate=float(rate.group(1)) if rate else None,
                            mons=[(dex.resolve(n), "") for n in names[:6]], src="pteams"))
        if not cards or not new:
            break
        page += 1
    return out


SOURCES = (("pokedb", src_pokedb), ("yakkun", src_yakkun), ("damekei", src_damekei), ("pteams", src_pteams))


def merge(teams: list[dict]) -> list[dict]:
    """Same article from several sites -> one team; keep the richest fields."""
    def norm(u):
        return re.sub(r"[?#].*$", "", re.sub(r"^https?://(www\.)?", "", u.strip()).rstrip("/"))
    order = {name: i for i, (name, _) in enumerate(SOURCES)}
    by: dict[str, dict] = {}
    for t in sorted(teams, key=lambda t: order[t["src"]]):
        k = norm(t["url"])
        if k not in by:
            by[k] = dict(t, sources=[t["src"]])
            continue
        cur = by[k]
        cur["sources"].append(t["src"])
        for fld in ("rank", "rate", "season", "trainer"):
            if not cur.get(fld) and t.get(fld):
                cur[fld] = t[fld]
        if sum(1 for r, _ in cur["mons"] if r) < sum(1 for r, _ in t["mons"] if r):
            cur["mons"] = t["mons"]
    return list(by.values())


# ───────────────────────── article details ─────────────────────────

def _detail_url(url: str) -> tuple[str | None, str | None]:
    """Where to read the full team from -> (url to fetch, kind)."""
    host = urllib.parse.urlsplit(url).netloc
    if host.endswith("yakkun.com") and "/bbs/party/n" in url:
        return url, "yakkun"
    if host.endswith("pokesol.app"):
        return url, "pokesol"
    if host == "note.com":
        m = re.search(r"/n/(n[0-9a-f]+)", url)
        return (f"https://note.com/api/v3/notes/{m.group(1)}", "note") if m else (None, None)
    if host in ("x.com", "twitter.com", "www.youtube.com", "youtu.be", "cafe.naver.com"):
        return None, None  # needs login / JS, or video
    return url, "blog"


def _attach_sets(t: dict, d: dict, dex: Dex) -> None:
    sets = d.get("sets") or []
    if not sets or not d.get("setsFrom"):
        return
    t["setsFrom"] = d["setsFrom"]
    slots = t["mons"]
    if d["setsFrom"] == "parsed":       # already aligned with team order
        t["sets"] = sets
        return
    aligned: list = [None] * len(slots)  # structured: align by species (order can differ)
    free = list(range(len(slots)))
    for s in sets:
        if not s:
            continue
        rec = dex.resolve(s["name"]) if s.get("name") else None
        if not rec and s.get("pid"):
            sid = dex.pid2species.get(s["pid"])
            rec = dex.resolve(dex.id2ja.get(sid, ""), dex_hint=sid) if sid else None
        idx = next((i for i in free if slots[i][0] and rec and slots[i][0]["dex"] == rec["dex"]), None)
        if idx is None and not any(r for r, _ in slots):
            idx = free[0] if free else None
        if idx is None:
            continue
        free.remove(idx)
        aligned[idx] = s
        if rec and not slots[idx][0]:
            slots[idx] = (rec, s.get("item", ""))
    t["sets"] = aligned


def _read_articles(f: Fetcher, teams: list[dict], dex: Dex, lex: "extract.Lexicon") -> Counter:
    """Fetch every article once (cached) and attach its sets."""
    jobs = [(t, *_detail_url(t["url"])) for t in teams]
    jobs = [j for j in jobs if j[1]]
    master = None
    master_path = f.dir / "pokesol_master.json"
    if master_path.exists():
        master = extract.PokesolMaster.from_json(json.loads(master_path.read_text(encoding="utf-8")))
    if master is None or not master.legal:
        first = next((url for _, url, kind in jobs if kind == "pokesol"), "https://pokesol.app/")
        page = f.get(first)
        root = extract.decode_page(page) if page else None
        if root:
            master = extract.PokesolMaster(root)
            master_path.write_text(json.dumps(master.to_json(), ensure_ascii=False), encoding="utf-8")
    legal = {}
    if master:
        legal = master.legal_by_species(lambda name: (dex.resolve(name) or {}).get("dex") if name else None)

    by_host: dict[str, list] = defaultdict(list)  # one lane per host: a slow site never blocks the others
    for n, j in enumerate(jobs):
        host = urllib.parse.urlsplit(j[1]).netloc
        by_host[f"{host}#{n % HOST_PARALLEL.get(host, 1)}"].append(j)

    def crawl(host_jobs):
        return [(j, f.get(j[1], encoding="euc-jp" if j[2] == "yakkun" else None)) for j in host_jobs]

    results = []
    with ThreadPoolExecutor(max_workers=min(48, len(by_host) or 1)) as pool:
        for chunk in pool.map(crawl, by_host.values()):
            results.extend(chunk)

    done: Counter = Counter()
    for (t, url, kind), page in results:
        if not page:
            done["unreadable"] += 1
            continue
        try:
            if kind == "yakkun":
                d = extract.parse_yakkun(page, lex)
            elif kind == "pokesol":
                d, _ = extract.parse_pokesol(page, master)
            else:
                text = extract.article_text(page, url)
                names = []
                for rec, _ in t["mons"]:
                    if not rec:
                        names.append(("?", [], None))
                        continue
                    base_ja = dex.id2ja.get(rec["dex"], "")
                    aliases = [rec["ja"], base_ja] + ([rec["ja"].replace("メガ", "", 1)] if rec["mega"] else [])
                    names.append((rec["ja"], [a for a in dict.fromkeys(aliases) if a and len(a) >= 2],
                                  legal.get(rec["dex"])))
                d = extract.parse_freeform(text, names, lex)
        except Exception:  # a weird page must not stop the pull
            done["parse_error"] += 1
            continue
        _attach_sets(t, d, dex)
        done[d.get("setsFrom") or "no_sets"] += 1
    return done


# ───────────────────────── scrape + ingest ─────────────────────────

def scrape(cache_dir: Path, regulations: tuple[str, ...],
           fetcher: Fetcher | None = None) -> tuple[list[dict], dict[str, Any]]:
    """Collect this regulation's team reports with their sets (Japanese names).

    Args:
        cache_dir: Page cache directory.
        regulations: Regulations to collect (their ladder seasons).
        fetcher: Override the HTTP fetcher (tests replay recorded pages).

    Returns:
        (teams, stats). Each team: url, title, trainer, season, rank, rate,
        sources, setsFrom, and ``members`` = six (record, item_ja, set|None).
    """
    seasons = _seasons_for(regulations)
    if not seasons:
        return [], {"skipped": "no ladder seasons mapped to " + ",".join(regulations)}
    f = fetcher or Fetcher(cache_dir)
    csv_text = lambda table: f.get(POKEAPI_CSV.format(table), max_age=NAME_TABLE_TTL) or ""  # noqa: E731
    dex = Dex(csv_text)
    raw, stats = [], {}
    for name, fn in SOURCES:
        try:
            got = fn(f, dex, seasons)
        except Exception as e:  # one broken source must not kill the pull
            got = []
            stats[f"{name}_error"] = type(e).__name__
        stats[f"listed_{name}"] = len(got)
        raw += got
    teams = [t for t in merge(raw)
             if t.get("season") in seasons and sum(1 for r, _ in t["mons"] if r) >= 4]
    stats["reports_in_regulation"] = len(teams)
    lex = extract.Lexicon(csv_text)
    stats.update({f"sets_{k}": v for k, v in _read_articles(f, teams, dex, lex).items()})
    for t in teams:
        sets = t.get("sets") or []
        t["members"] = [(rec, item, sets[i] if i < len(sets) else None) for i, (rec, item) in enumerate(t["mons"])]
    stats.update({f"fetch_{k}": v for k, v in f.errors.items()})
    return teams, {"lexicon": lex, **stats}


# Champions-original names PokeAPI's tables lack.
_JA_EXTRA = {"はどうのぼうご": "Aura Guard", "ほのおのたてがみ": "Fire Mane"}
_NO_ITEM = {"", "(持ち物なし)", "持ち物なし", "なし", "不明", "対応メガストーン", "メガストーン"}
_SP_ORDER = ("hp", "attack", "defense", "special-attack", "special-defense", "speed")
_SUFFIX = re.compile(r"\s+[XYZ]$")


def _species_slug(db: Db, en: str) -> str | None:
    """English label ('Mega Garchomp Z', 'Rotom (Wash)', 'Galarian Slowking') -> our slug."""
    name = en.strip()
    if name.startswith("Mega "):
        name = _SUFFIX.sub("", name[5:])
    candidates = [name]
    m = re.match(r"(.+?) \((.+)\)$", name)
    if m:
        candidates += [f"{m.group(1)}-{m.group(2)}", f"{m.group(2)} {m.group(1)}", m.group(1)]
    for c in candidates:
        slug = try_resolve(db, c)
        if slug:
            return slug
    return None


def _en(lex: "extract.Lexicon", kind: str, ja: str) -> str:
    ja = unicodedata.normalize("NFKC", ja or "").strip()
    if ja in _JA_EXTRA:
        return _JA_EXTRA[ja]
    key = lex.index[kind].get(ja)
    return lex.name(key, "en") if key else ja


def _db_name(db: Db, kind: str, en: str) -> str | None:
    row = {"move": db.get_move, "ability": db.get_ability, "item": db.get_item}[kind](en) if en else None
    return row["name"] if row else None


def _stone(db: Db, rec: dict, ja: str) -> str | None:
    """A Mega Stone written in Japanese (ガブリアスナイトＺ) -> its English name.

    Champions-only stones aren't in PokeAPI's tables; the holder is the
    stone's species, so look it up among that species' Megas (X/Y/Z by suffix).
    """
    m = re.match(r"(.+?)ナイト\s*([XYZ])?$", unicodedata.normalize("NFKC", ja))
    if not m:
        return None
    suffix = m.group(2) or ""
    for row in db.query("SELECT mega_kind, mega_stone FROM mega_forms WHERE species_slug=?",
                        (_species_slug(db, rec["en"]) or "",)):
        kind_suffix = row["mega_kind"].replace("Mega", "").strip()
        if kind_suffix == suffix:
            return row["mega_stone"]
    return None


def ingest_reports(db: Db, teams: list[dict], lex: "extract.Lexicon", regulations: tuple[str, ...]) -> dict[str, int]:
    """Store every team that resolves completely into ``full_teams``."""
    natures = {n.lower() for n in lex.nature_ja2en.values() if n}
    stats = Counter()
    rows = []
    for t in teams:
        if not t.get("setsFrom"):
            stats["skipped_no_sets"] += 1
            continue
        members, reason = [], None
        for rec, item_ja, s in t["members"]:
            if not rec or not s:
                reason = "incomplete"
                break
            slug = _species_slug(db, rec["en"])
            raw_item = unicodedata.normalize("NFKC", s.get("item") or item_ja or "").strip()
            item = None
            if raw_item not in _NO_ITEM:
                item = _db_name(db, "item", _en(lex, "item", raw_item)) or _stone(db, rec, raw_item)
            ability = _db_name(db, "ability", _en(lex, "ability", s.get("ability") or ""))
            nature = lex.nature_ja2en.get(unicodedata.normalize("NFKC", s.get("nature") or ""), "")
            moves = [_db_name(db, "move", _en(lex, "move", m)) for m in (s.get("moves") or [])]
            sp = s.get("sp") or []
            spread = {k: int(v) for k, v in zip(_SP_ORDER, sp) if v}
            if not slug or (raw_item not in _NO_ITEM and not item) or not ability:
                reason = "unresolved"
                break
            if nature.lower() not in natures or len(moves) != 4 or not all(moves):
                reason = "incomplete"
                break
            if len(sp) != 6 or not validate_spread(spread).ok:
                reason = "bad_spread"
                break
            members.append({"species": slug, "item": item, "ability": ability, "nature": nature,
                            "moves": moves, "stat_points": spread})
        if reason or len(members) != 6:
            stats[f"skipped_{reason or 'incomplete'}"] += 1
            continue
        reg = POKEDB_SEASON_REGULATION.get(t["season"])
        rows.append(("report:" + re.sub(r"^https?://", "", t["url"]), "singles", reg, t["season"], t.get("rank"),
                     t.get("rate"), t.get("title"), t.get("trainer"), t["url"],
                     "+".join(t.get("sources") or []) + f"/{t['setsFrom']}",
                     json.dumps(members, ensure_ascii=False)))
    db.executemany("DELETE FROM full_teams WHERE team_id LIKE ?", [("report:%",)])
    db.executemany("INSERT OR REPLACE INTO full_teams VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    stats["full_teams"] = len(rows)
    return dict(stats)


def pull_reports(db: Db, cache_dir: Path, regulations: tuple[str, ...],
                 fetcher: Fetcher | None = None) -> dict[str, Any]:
    """Scrape this regulation's team reports and store the complete ones."""
    teams, stats = scrape(cache_dir, regulations, fetcher)
    lex = stats.pop("lexicon", None)
    if lex is None:
        return stats
    return {**stats, **ingest_reports(db, teams, lex, regulations)}
