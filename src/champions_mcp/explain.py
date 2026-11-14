"""The type effectiveness behind a damage calc, and the form/ability it hit."""

from __future__ import annotations

from typing import Any

from . import profiles, teambuilder
from .db import Db
from .names import try_resolve as names_try_resolve
from .normalize import slugify


# Abilities that retype a Normal move and boost it 1.2x (every one in the
# Champions data: Pixilate, Aerilate, Refrigerate, Dragonize).
_NORMAL_RETYPE = {"pixilate": "Fairy", "aerilate": "Flying", "refrigerate": "Ice",
                  "dragonize": "Dragon"}
# Liquid Voice retypes sound moves to Water (no power boost). The data has no
# sound flag, so the Champions-legal sound moves are listed here.
_SOUND_MOVES = frozenset(slugify(m) for m in (
    "Alluring Voice", "Boomburst", "Bug Buzz", "Chatter", "Clanging Scales", "Clangorous Soul",
    "Confide", "Disarming Voice", "Echoed Voice", "Eerie Spell", "Grass Whistle", "Growl",
    "Heal Bell", "Howl", "Hyper Voice", "Metal Sound", "Noble Roar", "Overdrive", "Parting Shot",
    "Perish Song", "Psychic Noises", "Relic Song", "Roar", "Round", "Screech", "Sing", "Snarl",
    "Snore", "Sparkling Aria", "Supersonic", "Torch Song", "Uproar"))


def _form_of(db: Db, name: str, item: str | None, ability: str | None) -> dict[str, Any] | None:
    """The form (types/ability) a side battles with: a named Mega's own, a
    Mega Stone's, else the base form with the set's ability."""
    ekey = profiles.resolve_entity(db, name)
    ent = profiles._entities(db).get(ekey or "")
    if ent and ent["is_mega"]:
        return {"form": ent["name"], "types": ent["types"], "ability": ent["ability"]}
    slug = names_try_resolve(db, name)
    if not slug:
        return None
    return teambuilder._member_form(db, {"item": item, "ability": ability}, slug)


def explain_damage(db: Db, move: str, defender: str, defender_item: str | None,
                    defender_ability: str | None, attacker: str | None = None,
                    attacker_item: str | None = None,
                    attacker_ability: str | None = None) -> dict[str, Any] | None:
    """The type effectiveness behind a calc, and the defender form/ability it
    hit. A small model that sees a number lower than it expected otherwise
    spends pages re-deriving the type chart and the damage formula (it did, for
    Close Combat into Aura Guard Mega Lucario Z) instead of reading why."""
    mv = db.get_move(move)
    if not mv or not mv.get("type"):
        return None
    form = _form_of(db, defender, defender_item, defender_ability)
    if not form:
        return None
    atk = str(mv["type"]).capitalize()
    base_type = atk
    # The attacker's ability can retype the move before it hits (Pixilate turns
    # Normal into Fairy): judge effectiveness on the type that actually lands.
    atk_form = _form_of(db, attacker, attacker_item, attacker_ability) if attacker else None
    atk_ability = attacker_ability or (atk_form or {}).get("ability")
    akey = slugify(str(atk_ability or ""))
    boost = False
    if akey == "liquid-voice" and slugify(move) in _SOUND_MOVES:
        atk = "Water"
    elif base_type == "Normal" and akey in _NORMAL_RETYPE:
        atk, boost = _NORMAL_RETYPE[akey], True
    mult = 1.0
    for t in form["types"]:
        mult *= db.type_multiplier(atk, t)
    ability = defender_ability or form.get("ability")
    out = {
        "move_type": atk, "move_category": mv.get("damage_class"),
        "defender_form": form["form"], "defender_types": form["types"],
        "type_effectiveness": f"{mult:g}x",
    }
    if atk != base_type:
        out["base_move_type"] = base_type
        out["move_type_note"] = (f"{atk_ability} turns this {base_type}-type move into {atk}"
                                 + (" and boosts it 20%" if boost else "")
                                 + "; effectiveness is for the converted type.")
    if ability:
        out["defender_ability"] = ability
    out["note"] = ("Type effectiveness is from the Champions chart. Any gap between it "
                   "and the damage comes from abilities, items, weather, boosts or "
                   "screens named in `description` (e.g. Aura Guard halves contact "
                   "damage) — read them there; don't re-derive the formula.")
    return out
