"""FABulous tile libraries and primitives, registered from the directory layout.

`tile_libraries["fabulous"]["LUT4AB"]` is a `TileSource`, and
`primitives["MULADD"]` a `PrimitiveSource`. See `fabulous_tiles.sources` for the
layout rules that register them and the entry-point groups another package uses to
add its own.
"""

from fabulous_tiles.sources import (
    PRIMITIVES_GROUP,
    PRIMITIVES_ROOT,
    TILE_LIBRARIES_GROUP,
    TILES_ROOT,
    BelRef,
    Language,
    PrimitiveSource,
    Registry,
    Status,
    TileKind,
    TileLibrary,
    TileSource,
    load_entry_points,
    load_primitives,
    load_tile_library,
    primitives,
    tile_libraries,
)

__all__ = [
    "PRIMITIVES_GROUP",
    "PRIMITIVES_ROOT",
    "TILES_ROOT",
    "TILE_LIBRARIES_GROUP",
    "BelRef",
    "Language",
    "PrimitiveSource",
    "Registry",
    "Status",
    "TileKind",
    "TileLibrary",
    "TileSource",
    "load_entry_points",
    "load_primitives",
    "load_tile_library",
    "primitives",
    "tile_libraries",
]
