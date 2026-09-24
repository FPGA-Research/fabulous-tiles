"""Tile libraries and primitives discovered from the directory layout.

A tile is any `tiles/<library>/**/<name>/<name>.csv` below the library root, and a
primitive is any `primitives/<name>/` holding `fabulous/<name>.v` or
`fabulous/<name>.vhdl`. Adding such a directory registers it, so no list of tiles
or primitives exists to keep in sync. The registries scan on first access and cache
the result for the life of the process.

Every file reference a tile CSV makes (`INCLUDE`, `MATRIX`, `BEL`) is resolved and
checked when its library is scanned, so a broken reference fails at lookup rather
than when FABulous later reads the copied project.
"""

import csv
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from importlib import resources
from pathlib import Path

HDL_SUFFIX = "{HDL_SUFFIX}"


def _package_root() -> Path:
    root = resources.files(__package__)
    if not isinstance(root, Path):
        raise RuntimeError(
            f"fabulous_tiles is installed as {root!r}, not a directory. Install it from "
            "a wheel or a source checkout, not a zip archive."
        )
    return root


PACKAGE_ROOT = _package_root()
TILES_ROOT = PACKAGE_ROOT / "tiles"
PRIMITIVES_ROOT = PACKAGE_ROOT / "primitives"


class Language(StrEnum):
    """An HDL a primitive can ship a source for."""

    VERILOG = "verilog"
    VHDL = "vhdl"

    @property
    def suffix(self) -> str:
        """The file suffix, without the dot, that `{HDL_SUFFIX}` expands to."""
        match self:
            case Language.VERILOG:
                return "v"
            case Language.VHDL:
                return "vhdl"


class TileKind(StrEnum):
    """The header keyword of a tile CSV."""

    TILE = "TILE"
    SUPERTILE = "SuperTILE"


class Registry[V](Mapping[str, V]):
    """A read-only name lookup that loads its entries on first access.

    A missing name raises `KeyError` listing the names that exist.
    """

    def __init__(self, kind: str, load: Callable[[], dict[str, V]]) -> None:
        self._kind = kind
        self._load = load
        self._entries: dict[str, V] | None = None

    def _loaded(self) -> dict[str, V]:
        if self._entries is None:
            self._entries = self._load()
        return self._entries

    def __getitem__(self, name: str) -> V:
        entries = self._loaded()
        if name not in entries:
            raise KeyError(f"No {self._kind} {name!r}. Available: {sorted(entries)}.")
        return entries[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._loaded())

    def __len__(self) -> int:
        return len(self._loaded())


@dataclass(frozen=True, slots=True)
class PrimitiveSource:
    """A primitive: its HDL sources, Yosys techmaps and documentation."""

    name: str
    root: Path
    hdl: Mapping[Language, Path]
    techmaps: tuple[Path, ...]
    readme: Path | None

    @classmethod
    def from_dir(cls, root: Path) -> "PrimitiveSource":
        """Read the primitive in `root`, named after the directory.

        Raises
        ------
        FileNotFoundError
            If `root/fabulous/` holds no `<name>.v` or `<name>.vhdl`.
        """
        root = root.resolve()
        hdl = {
            language: root / "fabulous" / f"{root.name}.{language.suffix}"
            for language in Language
            if (root / "fabulous" / f"{root.name}.{language.suffix}").is_file()
        }
        if not hdl:
            raise FileNotFoundError(
                f"Primitive {root.name} has no fabulous/{root.name}.v or "
                f"fabulous/{root.name}.vhdl in {root}."
            )
        yosys = root / "yosys"
        readme = root / "README.md"
        return cls(
            name=root.name,
            root=root,
            hdl=hdl,
            techmaps=tuple(sorted(p for p in yosys.rglob("*") if p.is_file())),
            readme=readme if readme.is_file() else None,
        )


def load_primitives(root: Path) -> dict[str, PrimitiveSource]:
    """Read every primitive directory under `root` that holds a `fabulous/` source."""
    return {
        d.name: PrimitiveSource.from_dir(d)
        for d in sorted(root.iterdir())
        if (d / "fabulous").is_dir()
    }


@dataclass(frozen=True, slots=True)
class BelRef:
    """A `BEL` row of a tile CSV, its primitive and the file it selects per language."""

    row: str
    primitive: PrimitiveSource
    sources: Mapping[Language, Path]

    @property
    def languages(self) -> frozenset[Language]:
        """The languages this row has a source for."""
        return frozenset(self.sources)

    def source(self, language: Language) -> Path:
        """Return the file this row selects for `language`.

        Raises
        ------
        ValueError
            If the row has no source for `language`, for example a `.v` row asked
            for VHDL.
        """
        if language not in self.sources:
            raise ValueError(
                f"BEL {self.row} has no {language} source. It has "
                f"{sorted(self.languages)}."
            )
        return self.sources[language]


@dataclass(frozen=True, slots=True)
class TileSource:
    """A tile or supertile and every file it needs.

    `files(language)` is the full set: the tile's own files, what its CSV and switch
    matrix `INCLUDE`, the BEL primitive sources for `language` and, for a supertile,
    the files of its subtiles.
    """

    name: str
    library: str
    kind: TileKind
    root: Path
    csv: Path
    deprecated: bool
    own_files: tuple[Path, ...]
    includes: tuple[Path, ...]
    matrix: Path | None
    config_mem: Path | None
    bels: tuple[BelRef, ...]
    subtiles: tuple["TileSource", ...] = field(default=())

    @property
    def languages(self) -> frozenset[Language]:
        """The languages every BEL of this tile and its subtiles has a source for."""
        languages = frozenset(Language)
        for bel in self.bels:
            languages &= bel.languages
        for subtile in self.subtiles:
            languages &= subtile.languages
        return languages

    def files(self, language: Language) -> tuple[Path, ...]:
        """Return every file the tile needs when built in `language`.

        Raises
        ------
        ValueError
            If a BEL of the tile has no source for `language`.
        """
        if language not in self.languages:
            raise ValueError(
                f"Tile {self.library}/{self.name} supports {sorted(self.languages)}, "
                f"not {language}."
            )
        found = {
            *self.own_files,
            *self.includes,
            *(b.source(language) for b in self.bels),
        }
        for subtile in self.subtiles:
            found.update(subtile.files(language))
        return tuple(sorted(found))


def _read_rows(path: Path) -> list[list[str]]:
    with path.open(newline="") as f:
        return [row for row in csv.reader(f) if row]


def _resolve_ref(base: Path, ref: str, what: str, owner: Path) -> Path:
    target = (base / ref.strip()).resolve()
    if not target.is_file():
        raise FileNotFoundError(
            f"{what} {ref.strip()} in {owner} resolves to {target}, which does not exist."
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


def _row_language(row: str) -> Language:
    match Path(row).suffix:
        case ".v":
            return Language.VERILOG
        case ".vhdl" | ".vhd":
            return Language.VHDL
        case suffix:
            raise ValueError(f"BEL {row} has suffix {suffix!r}, which is no known HDL.")


def _bel_ref(
    tile_csv: Path,
    row: str,
    primitives: Mapping[str, PrimitiveSource],
    primitives_root: Path,
) -> BelRef:
    if HDL_SUFFIX in row:
        candidates = {
            language: (
                tile_csv.parent / row.replace(HDL_SUFFIX, language.suffix)
            ).resolve()
            for language in Language
        }
    else:
        candidates = {_row_language(row): (tile_csv.parent / row).resolve()}
    sources = {
        language: path for language, path in candidates.items() if path.is_file()
    }
    if not sources:
        raise FileNotFoundError(
            f"BEL {row} in {tile_csv} resolves to none of {sorted(map(str, candidates.values()))}."
        )
    names = set()
    for path in sources.values():
        if not path.is_relative_to(primitives_root):
            raise ValueError(
                f"BEL {row} in {tile_csv} resolves to {path}, outside {primitives_root}. "
                "Tile BELs must be primitives."
            )
        names.add(path.relative_to(primitives_root).parts[0])
    (name,) = names
    if name not in primitives:
        raise ValueError(
            f"BEL {row} in {tile_csv} lands in primitives/{name}, which is no registered "
            "primitive because it lacks a fabulous/ source directory."
        )
    return BelRef(row=row, primitive=primitives[name], sources=sources)


def _read_tile(
    tile_csv: Path,
    library: str,
    primitives: Mapping[str, PrimitiveSource],
    primitives_root: Path,
) -> tuple[TileSource, list[str]]:
    """Read one tile CSV; return the tile and, for a supertile, its subtile names."""
    rows = _read_rows(tile_csv)
    header = rows[0]
    name = tile_csv.parent.name
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
    if marker not in ("", "DEPRECATED"):
        raise ValueError(
            f"{tile_csv} has {marker!r} in header column 3; only DEPRECATED is allowed."
        )

    includes: list[Path] = []
    matrix: Path | None = None
    bels: list[BelRef] = []
    subtile_names: list[str] = []
    for row in rows[1:]:
        keyword = row[0].strip()
        if kind is TileKind.SUPERTILE:
            if keyword and keyword != "EndSuperTILE" and not keyword.startswith("#"):
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
                bels.append(
                    _bel_ref(tile_csv, row[1].strip(), primitives, primitives_root)
                )

    root = tile_csv.parent.resolve()
    config_mem = root / f"{name}_ConfigMem.csv"
    tile = TileSource(
        name=name,
        library=library,
        kind=kind,
        root=root,
        csv=tile_csv.resolve(),
        deprecated=marker == "DEPRECATED",
        own_files=tuple(sorted(p for p in root.iterdir() if p.is_file())),
        includes=tuple(dict.fromkeys(includes)),
        matrix=matrix,
        config_mem=config_mem if config_mem.is_file() else None,
        bels=tuple(bels),
    )
    return tile, subtile_names


@dataclass(frozen=True, slots=True)
class TileLibrary(Mapping[str, TileSource]):
    """The tiles under one `tiles/<library>/` directory, by tile name."""

    name: str
    root: Path
    tiles: Mapping[str, TileSource]

    def __getitem__(self, name: str) -> TileSource:
        if name not in self.tiles:
            raise KeyError(
                f"No tile {name!r} in library {self.name}. Available: {sorted(self.tiles)}."
            )
        return self.tiles[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self.tiles)

    def __len__(self) -> int:
        return len(self.tiles)


def load_tile_library(
    root: Path,
    primitives: Mapping[str, PrimitiveSource],
    primitives_root: Path,
) -> TileLibrary:
    """Read every tile below the library directory `root`.

    A tile is a `<name>/<name>.csv` below `root`. Files directly in `root`, such as
    a TileLibrary CSV named after the library, are not tiles.

    Raises
    ------
    ValueError
        If two tiles share a name, a header is malformed, a BEL lies outside
        `primitives_root` or a supertile names a tile the library lacks.
    FileNotFoundError
        If an `INCLUDE`, `MATRIX` or `BEL` reference does not exist.
    """
    primitives_root = primitives_root.resolve()
    tiles: dict[str, TileSource] = {}
    members: dict[str, list[str]] = {}
    for tile_csv in sorted(root.rglob("*.csv")):
        if tile_csv.parent == root or tile_csv.stem != tile_csv.parent.name:
            continue
        tile, subtile_names = _read_tile(
            tile_csv, root.name, primitives, primitives_root
        )
        if tile.name in tiles:
            raise ValueError(
                f"Library {root.name} has two tiles named {tile.name}: {tiles[tile.name].csv} and {tile.csv}."
            )
        tiles[tile.name] = tile
        members[tile.name] = subtile_names
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


primitives: Registry[PrimitiveSource] = Registry(
    "primitive", lambda: load_primitives(PRIMITIVES_ROOT)
)
tile_libraries: Registry[TileLibrary] = Registry(
    "tile library",
    lambda: {
        d.name: load_tile_library(d, primitives, PRIMITIVES_ROOT)
        for d in sorted(TILES_ROOT.iterdir())
        if d.is_dir()
    },
)
