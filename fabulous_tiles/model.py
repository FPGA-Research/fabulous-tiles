"""Format-neutral descriptions of primitives, tiles and tile libraries.

A loader such as `fabulous_tiles.tile_csv` builds these from a definition format,
and consumers such as FABulous only call `TileLibrary.materialise`. A new tile
definition format therefore needs a new loader and no change here.
"""

import shutil
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

HDL_SUFFIX = "{HDL_SUFFIX}"


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


@dataclass(frozen=True, slots=True)
class PrimitiveSource:
    """A primitive: its HDL sources, Yosys techmaps and documentation."""

    name: str
    root: Path
    hdl: Mapping[Language, Path]
    techmaps: tuple[Path, ...]
    readme: Path | None


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


@dataclass(frozen=True, slots=True)
class TileLibrary(Mapping[str, TileSource]):
    """The tiles under one library directory, by tile name."""

    name: str
    root: Path
    tiles: Mapping[str, TileSource]

    def __getitem__(self, name: str) -> TileSource:
        if name not in self.tiles:
            raise KeyError(
                f"No tile {name!r} in library {self.name}. "
                f"Available: {sorted(self.tiles)}."
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
        of `language`. The copies are writable even when the installed package is
        not, because FABulous writes netlists next to a VHDL BEL when it parses it.

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
            target.parent.mkdir(parents=True, exist_ok=True)
            # copyfile copies contents only, so a read-only source yields a
            # writable copy.
            shutil.copyfile(src, target)

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
