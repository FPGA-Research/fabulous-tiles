"""Tile libraries whose tiles are defined by FABulous tile CSVs.

A tile is any `<name>/<name>.csv` below the library root; files directly in the
root, such as a TileLibrary CSV named after the library, are library metadata.
Every file reference a tile CSV makes (`INCLUDE`, `MATRIX`, `BEL`) is resolved and
checked when the library is read, so a broken reference fails at lookup rather than
when FABulous later reads the materialised project.
"""

import csv
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from fabulous_tiles.model import (
    HDL_SUFFIX,
    BelRef,
    Language,
    PrimitiveSource,
    Status,
    TileKind,
    TileLibrary,
    TileSource,
)


def _read_rows(path: Path) -> list[list[str]]:
    with path.open(newline="") as f:
        return [row for row in csv.reader(f) if row]


def _resolve_ref(base: Path, ref: str, what: str, owner: Path) -> Path:
    target = (base / ref.strip()).resolve()
    if not target.is_file():
        raise FileNotFoundError(
            f"{what} {ref.strip()} in {owner} resolves to {target}, which does not "
            "exist."
        )
    return target


def _list_includes(matrix: Path) -> list[Path]:
    """Return the `.list` files `matrix` includes, transitively."""
    found: list[Path] = []
    for row in _read_rows(matrix):
        if row[0].strip() == "INCLUDE":
            target = _resolve_ref(matrix.parent, row[1], "INCLUDE", matrix)
            found += [target, *_list_includes(target)]
    return found


def _bel_ref(
    tile_csv: Path, row: str, primitives: Mapping[str, PrimitiveSource]
) -> BelRef:
    """Resolve a `BEL` row to the primitive sources it selects per language."""
    if HDL_SUFFIX in row:
        candidates = {
            language: (
                tile_csv.parent / row.replace(HDL_SUFFIX, language.suffix)
            ).resolve()
            for language in Language
        }
    else:
        match Path(row).suffix:
            case ".v":
                language = Language.VERILOG
            case ".vhdl" | ".vhd":
                language = Language.VHDL
            case suffix:
                raise ValueError(
                    f"BEL {row} in {tile_csv} has suffix {suffix!r}, which is no "
                    "known HDL."
                )
        candidates = {language: (tile_csv.parent / row).resolve()}
    sources = {
        language: path for language, path in candidates.items() if path.is_file()
    }
    if not sources:
        raise FileNotFoundError(
            f"BEL {row} in {tile_csv} resolves to none of "
            f"{sorted(map(str, candidates.values()))}."
        )
    owners = {
        primitive.name
        for path in sources.values()
        for primitive in primitives.values()
        if path.is_relative_to(primitive.root)
    }
    if len(owners) != 1:
        raise ValueError(
            f"BEL {row} in {tile_csv} resolves to "
            f"{sorted(map(str, sources.values()))}, which lie in no single registered "
            "primitive. Tile BELs must be primitives."
        )
    (name,) = owners
    return BelRef(row=row, primitive=primitives[name], sources=sources)


def load_tile_library(
    root: Path, primitives: Mapping[str, PrimitiveSource]
) -> TileLibrary:
    """Read every tile below the library directory `root`.

    Raises
    ------
    ValueError
        If two tiles share a name, a header is malformed, a BEL lies in no
        registered primitive or a supertile names a tile the library lacks.
    FileNotFoundError
        If an `INCLUDE`, `MATRIX` or `BEL` reference does not exist.
    """
    tiles: dict[str, TileSource] = {}
    members: dict[str, list[str]] = {}
    for tile_csv in sorted(root.rglob("*.csv")):
        name = tile_csv.parent.name
        if tile_csv.parent == root or tile_csv.stem != name:
            continue
        rows = _read_rows(tile_csv)
        header = rows[0]
        try:
            kind = TileKind(header[0].strip())
        except ValueError:
            raise ValueError(
                f"{tile_csv} starts with {header[0]!r}, not TILE or SuperTILE."
            ) from None
        if header[1].strip() != name:
            raise ValueError(
                f"{tile_csv} names tile {header[1]!r}, but its directory is {name}."
            )
        marker = header[2].strip() if len(header) > 2 else ""
        match marker:
            case "":
                status = Status.STABLE
            case Status.EXPERIMENTAL | Status.DEPRECATED:
                status = Status(marker)
            case _:
                raise ValueError(
                    f"{tile_csv} has {marker!r} in header column 3. Leave it empty "
                    "for a stable tile, or write EXPERIMENTAL or DEPRECATED."
                )

        includes: list[Path] = []
        matrix: Path | None = None
        bels: list[BelRef] = []
        subtile_names: list[str] = []
        for row in rows[1:]:
            keyword = row[0].strip()
            if kind is TileKind.SUPERTILE:
                if (
                    keyword
                    and keyword != "EndSuperTILE"
                    and not keyword.startswith("#")
                ):
                    subtile_names += [
                        c.strip() for c in row if c.strip() not in ("", "NULL")
                    ]
                continue
            match keyword:
                case "INCLUDE":
                    includes.append(
                        _resolve_ref(tile_csv.parent, row[1], "INCLUDE", tile_csv)
                    )
                case "MATRIX":
                    matrix = _resolve_ref(tile_csv.parent, row[1], "MATRIX", tile_csv)
                    if matrix.suffix == ".list":
                        includes += _list_includes(matrix)
                case "BEL":
                    bels.append(_bel_ref(tile_csv, row[1].strip(), primitives))

        if name in tiles:
            raise ValueError(
                f"Library {root.name} has two tiles named {name}: "
                f"{tiles[name].definition} and {tile_csv.resolve()}."
            )
        tile_root = tile_csv.parent.resolve()
        config_mem = tile_root / f"{name}_ConfigMem.csv"
        tiles[name] = TileSource(
            name=name,
            library=root.name,
            kind=kind,
            root=tile_root,
            definition=tile_csv.resolve(),
            status=status,
            own_files=tuple(sorted(p for p in tile_root.iterdir() if p.is_file())),
            includes=tuple(dict.fromkeys(includes)),
            matrix=matrix,
            config_mem=config_mem if config_mem.is_file() else None,
            bels=tuple(bels),
        )
        members[name] = subtile_names

    for name, subtile_names in members.items():
        if not subtile_names:
            continue
        missing = [s for s in subtile_names if s not in tiles]
        if missing:
            raise ValueError(
                f"Supertile {root.name}/{name} names tiles {missing} the library lacks."
            )
        tiles[name] = replace(
            tiles[name], subtiles=tuple(tiles[s] for s in dict.fromkeys(subtile_names))
        )
    return TileLibrary(name=root.name, root=root.resolve(), tiles=tiles)
