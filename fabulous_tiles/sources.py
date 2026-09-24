"""Tile libraries and primitives discovered from the directory layout.

A package registers a directory of tile libraries under the entry-point group
`fabulous.tile_libraries` and a directory of primitives under `fabulous.primitives`;
this package registers its own directories the same way. Inside a registered
directory, a tile is any `<library>/**/<name>/<name>.csv` below the library root and
a primitive is any `<name>/` holding `fabulous/<name>.v` or `fabulous/<name>.vhdl`.
Adding such a directory registers it, so no list of tiles or primitives exists to
keep in sync. The registries scan on first access and cache the result for the life
of the process.

Every file reference a tile CSV makes (`INCLUDE`, `MATRIX`, `BEL`) is resolved and
checked when its library is scanned, so a broken reference fails at lookup rather
than when FABulous later reads the materialised project.
"""

import csv
import shutil
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from importlib import resources
from importlib.metadata import entry_points
from pathlib import Path

HDL_SUFFIX = "{HDL_SUFFIX}"
TILE_LIBRARIES_GROUP = "fabulous.tile_libraries"
PRIMITIVES_GROUP = "fabulous.primitives"


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


class Status(StrEnum):
    """The support status of a tile, set by column 3 of its CSV header.

    An empty column means `STABLE`; `EXPERIMENTAL` and `DEPRECATED` are written out.
    """

    STABLE = "STABLE"
    EXPERIMENTAL = "EXPERIMENTAL"
    DEPRECATED = "DEPRECATED"


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


def load_entry_points[V](
    group: str, kind: str, load: Callable[[Path], Mapping[str, V]]
) -> dict[str, V]:
    """Merge what `load` finds in every directory registered under `group`.

    Each entry point in `group` must load a `Path` to a directory, which `load`
    turns into named entries.

    Raises
    ------
    RuntimeError
        If no installed package registers anything under `group`, which happens
        when a package is imported from a checkout that was never installed.
    TypeError
        If an entry point loads something other than a directory `Path`.
    ValueError
        If two registered directories provide an entry of the same name.
    """
    registered = sorted(entry_points(group=group), key=lambda ep: ep.name)
    if not registered:
        raise RuntimeError(
            f"No installed package registers a {kind} directory under the entry-point "
            f"group {group}. Install the package, for example with `uv sync`, rather "
            "than importing it from a bare checkout."
        )
    found: dict[str, V] = {}
    owners: dict[str, str] = {}
    for ep in registered:
        root = ep.load()
        owner = f"entry point {ep.name} ({ep.value})"
        if not isinstance(root, Path) or not root.is_dir():
            raise TypeError(
                f"The {owner} in group {group} loads {root!r}, not a directory Path."
            )
        for name, entry in load(root).items():
            if name in found:
                raise ValueError(
                    f"The {kind} {name!r} is registered by both {owners[name]} and "
                    f"{owner}. Rename one of them."
                )
            found[name] = entry
            owners[name] = owner
    return found


def _copy_writable(src: Path, dest: Path) -> None:
    """Copy file contents only, so a read-only source yields a writable copy."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)


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
    """A `BEL` row of a tile, its primitive and the file it selects per language.

    `row` is the reference exactly as the tile definition writes it.
    """

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

    `files(language)` is the full set: the tile's own files, what its definition and
    switch matrix `INCLUDE`, the BEL primitive sources for `language` and, for a
    supertile, the files of its subtiles. `definition` is the file that declares the
    tile, today its CSV.
    """

    name: str
    library: str
    kind: TileKind
    root: Path
    definition: Path
    status: Status
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
    tile_csv: Path, row: str, primitives: Mapping[str, PrimitiveSource]
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


def _read_tile(
    tile_csv: Path, library: str, primitives: Mapping[str, PrimitiveSource]
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
    match marker:
        case "":
            status = Status.STABLE
        case Status.EXPERIMENTAL | Status.DEPRECATED:
            status = Status(marker)
        case _:
            raise ValueError(
                f"{tile_csv} has {marker!r} in header column 3. Leave it empty for a "
                "stable tile, or write EXPERIMENTAL or DEPRECATED."
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
                bels.append(_bel_ref(tile_csv, row[1].strip(), primitives))

    root = tile_csv.parent.resolve()
    config_mem = root / f"{name}_ConfigMem.csv"
    tile = TileSource(
        name=name,
        library=library,
        kind=kind,
        root=root,
        definition=tile_csv.resolve(),
        status=status,
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

    def materialise(self, dest: Path, language: Language) -> None:
        """Copy the library into `dest` so the copy references nothing outside it.

        Files directly in the library root are library metadata and are skipped.
        Every `BEL` row is rewritten to `./<file name>` of the source it selects for
        `language`, and that source is copied next to the tile definition. Any other
        `{HDL_SUFFIX}` in a CSV, such as in a commented-out row, becomes the suffix
        of `language`. The copies are writable even when the installed package is not, because
        FABulous writes netlists next to a VHDL BEL when it parses it.

        Raises
        ------
        ValueError
            If a tile has no BEL source for `language`, or a CSV that is no tile of
            the library holds a `BEL` row.
        FileExistsError
            If two files with different bytes would land at the same path.
        """
        for tile in self.tiles.values():
            if language not in tile.languages:
                raise ValueError(
                    f"Tile {self.name}/{tile.name} supports "
                    f"{sorted(tile.languages)}, not {language}."
                )
        bel_sources = {
            tile.definition: {bel.row: bel.source(language) for bel in tile.bels}
            for tile in self.tiles.values()
        }
        copied: dict[Path, Path] = {}

        def copy(src: Path, target: Path) -> None:
            if target in copied and copied[target].read_bytes() != src.read_bytes():
                raise FileExistsError(
                    f"{target} would hold both {copied[target]} and {src}, which "
                    "differ. Rename one of them."
                )
            copied[target] = src
            _copy_writable(src, target)

        for src in sorted(self.root.rglob("*")):
            if not src.is_file() or src.parent == self.root:
                continue
            target = dest / src.relative_to(self.root)
            if src.suffix != ".csv":
                copy(src, target)
                continue
            with src.open(newline="") as f:
                lines = f.read().splitlines(keepends=True)
            out: list[str] = []
            for line in lines:
                body = line.rstrip("\r\n")
                fields = body.split(",")
                if fields[0] != "BEL":
                    out.append(line.replace(HDL_SUFFIX, language.suffix))
                    continue
                if src not in bel_sources:
                    raise ValueError(
                        f"{src} holds a BEL row but is no tile of library {self.name}."
                    )
                bel_src = bel_sources[src][fields[1].strip()]
                copy(bel_src, target.parent / bel_src.name)
                fields[1] = f"./{bel_src.name}"
                out.append(",".join(fields) + line[len(body) :])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("".join(out), newline="")


def load_tile_library(
    root: Path, primitives: Mapping[str, PrimitiveSource]
) -> TileLibrary:
    """Read every tile below the library directory `root`.

    A tile is a `<name>/<name>.csv` below `root`. Files directly in `root`, such as
    a TileLibrary CSV named after the library, are not tiles.

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
        if tile_csv.parent == root or tile_csv.stem != tile_csv.parent.name:
            continue
        tile, subtile_names = _read_tile(tile_csv, root.name, primitives)
        if tile.name in tiles:
            raise ValueError(
                f"Library {root.name} has two tiles named {tile.name}: "
                f"{tiles[tile.name].definition} and {tile.definition}."
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


def _load_libraries(root: Path) -> dict[str, TileLibrary]:
    return {
        d.name: load_tile_library(d, primitives)
        for d in sorted(root.iterdir())
        if d.is_dir()
    }


primitives: Registry[PrimitiveSource] = Registry(
    "primitive",
    lambda: load_entry_points(PRIMITIVES_GROUP, "primitive", load_primitives),
)
tile_libraries: Registry[TileLibrary] = Registry(
    "tile library",
    lambda: load_entry_points(TILE_LIBRARIES_GROUP, "tile library", _load_libraries),
)
