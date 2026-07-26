"""Data-pull package: build champions.sqlite from championsbattledata.com.

``source.py``   downloads the single source (pokemon-index.json).
``pokeapi.py``  one-time text enrichment (move/ability/item/type/locale names).
``build.py``    parses the index into the SQLite schema and enriches it.
``__main__.py`` the ``champions-mcp-pull`` CLI entry point.
"""
