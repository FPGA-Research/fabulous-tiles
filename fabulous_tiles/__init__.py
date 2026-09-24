"""FABulous tile libraries and primitives, registered from the directory layout.

`tile_libraries["fabulous"]["LUT4AB"]` is a `TileSource`, and
`primitives["MULADD"]` a `PrimitiveSource`. See `fabulous_tiles.sources` for the
layout rules that register them.
"""

from fabulous_tiles.sources import (
    PRIMITIVES_ROOT,
    TILES_ROOT,
    BelRef,
    Language,
    PrimitiveSource,
    Registry,
    TileKind,
    TileLibrary,
    TileSource,
    load_primitives,
    load_tile_library,
    primitives,
    tile_libraries,
)

__all__ = [
    "PRIMITIVES_ROOT",
    "TILES_ROOT",
    "BelRef",
    "Language",
    "PrimitiveSource",
    "Registry",
    "TileKind",
    "TileLibrary",
    "TileSource",
    "load_primitives",
    "load_tile_library",
    "primitives",
    "tile_libraries",
]
