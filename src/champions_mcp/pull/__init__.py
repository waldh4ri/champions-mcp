"""Data-pull package: build champions.sqlite from pokechamp.gg.

``pokechamp_source.py`` reads roster/movepool/reference/usage data from
                        pokechamp.gg.
``nuxt_payload.py``     decodes pokechamp's Nuxt SSR `_payload.json` format.
``pokeapi.py``          one-time enrichment: type chart + localized species names.
``builds_source.py``    reads observed teams from Limitless (VGC) and pokedb (BSS).
``builds.py``           ingests the observed-team corpus into build_teams/build_members.
``build.py``            parses pokechamp into the SQLite schema and enriches it.
``__main__.py``         the ``champions-mcp-pull`` CLI entry point.
"""
