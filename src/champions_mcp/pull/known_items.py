"""Full list of holdable Champions items, independent of usage stats.

championsbattledata only reports a Pokémon's *top-10* held items per format,
so items that exist in Champions but never crack a top-10 list (e.g. the
status-cure berries) are invisible to ``parse_index``'s usage-derived item
set. This is the "Hold Items" / "Mega Stone" / "Berries" categories from
https://www.serebii.net/pokemonchampions/items.shtml, verified against the
in-game item list.

Deliberately excludes Serebii's "Miscellaneous Items" category (Affinity
Tickets, Quick Coupon, Teammate Ticket, Training Ticket, *_Guaranteed
Ticket) — those are Recruit Ranch / recruitment currency, never held by a
Pokémon in battle.
"""

from __future__ import annotations

KNOWN_HOLDABLE_ITEMS: tuple[str, ...] = (
    "Abomasite", "Absolite", "Aerodactylite", "Aggronite", "Alakazite",
    "Altarianite", "Ampharosite", "Aspear Berry", "Audinite", "Babiri Berry",
    "Banettite", "Barbaracite", "Beedrillite", "Big Root", "Black Belt",
    "Black Glasses", "Blastoisinite", "Blazikenite", "Bright Powder",
    "Cameruptite", "Chandelurite", "Charcoal", "Charizardite X",
    "Charizardite Y", "Charti Berry", "Cheri Berry", "Chesnaughtite",
    "Chesto Berry", "Chilan Berry", "Chimechite", "Choice Scarf",
    "Chople Berry", "Clefablite", "Coba Berry", "Colbur Berry",
    "Crabominite", "Damp Rock", "Delphoxite", "Dragalgite", "Dragon Fang",
    "Dragoninite", "Drampanite", "Eelektrossite", "Emboarite", "Excadrite",
    "Expert Belt", "Fairy Feather", "Falinksite", "Feraligite", "Floettite",
    "Focus Band", "Focus Sash", "Froslassite", "Galladite", "Garchompite",
    "Gardevoirite", "Gengarite", "Glalitite", "Glimmoranite", "Golurkite",
    "Greninjite", "Gyaradosite", "Haban Berry", "Hard Stone", "Hawluchanite",
    "Heat Rock", "Heracronite", "Houndoominite", "Icy Rock", "Iron Ball",
    "Kangaskhanite", "Kasib Berry", "Kebia Berry", "King's Rock", "Leftovers",
    "Leppa Berry", "Life Orb", "Light Ball", "Light Clay", "Lopunnite",
    "Lucarionite", "Lum Berry", "Magnet", "Malamarite", "Manectite",
    "Mawilite", "Medichamite", "Meganiumite", "Mental Herb", "Meowsticite",
    "Metagrossite", "Metal Coat", "Metronome", "Miracle Seed", "Muscle Band",
    "Mystic Water", "Never-Melt Ice", "Occa Berry", "Oran Berry",
    "Passho Berry", "Payapa Berry", "Pecha Berry", "Persim Berry",
    "Pidgeotite", "Pinsirite", "Poison Barb", "Pyroarite", "Quick Claw",
    "Raichunite X", "Raichunite Y", "Rawst Berry", "Rindo Berry",
    "Roseli Berry", "Sablenite", "Sceptilite", "Scizorite", "Scolipite",
    "Scope Lens", "Scovillainite", "Scraftinite", "Sharp Beak",
    "Sharpedonite", "Shed Shell", "Shell Bell", "Shuca Berry", "Silk Scarf",
    "Silver Powder", "Sitrus Berry", "Skarmorite", "Slowbronite",
    "Smooth Rock", "Soft Sand", "Spell Tag", "Staraptite", "Starminite",
    "Steelixite", "Swampertite", "Tanga Berry", "Twisted Spoon",
    "Tyranitarite", "Venusaurite", "Victreebelite", "Wacan Berry",
    "White Herb", "Wide Lens", "Wise Glasses", "Yache Berry", "Zoom Lens",
)
