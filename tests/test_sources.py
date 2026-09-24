from collections.abc import Callable
from pathlib import Path

import pytest

from fabulous_tiles import (
    Language,
    Registry,
    TileKind,
    TileLibrary,
    load_primitives,
    load_tile_library,
    primitives,
    tile_libraries,
)

T_CSV = """TILE,T,DEPRECATED,,
INCLUDE,../include/Base.csv
BEL,../../../primitives/A/fabulous/A.{HDL_SUFFIX},X_
MATRIX,./T_switch_matrix.list
EndTILE
"""
V_CSV = """TILE,V,,,
BEL,../../../primitives/B/fabulous/B.v
MATRIX,./V_switch_matrix.list
EndTILE
"""
S_CSV = """SuperTILE,S,,
T,NULL
V,,
EndSuperTILE
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def assets(tmp_path: Path) -> Path:
    """A package root shaped like fabulous_tiles, with one library `lib`."""
    for name, languages in (("A", ("v", "vhdl")), ("B", ("v",))):
        for suffix in languages:
            _write(
                tmp_path / f"primitives/{name}/fabulous/{name}.{suffix}", f"// {name}\n"
            )
    _write(tmp_path / "primitives/A/yosys/techmap/map.v", "// map\n")
    _write(tmp_path / "primitives/A/README.md", "# A\n")
    _write(tmp_path / "primitives/docs_only/README.md", "# not a primitive\n")
    lib = tmp_path / "tiles/lib"
    _write(lib / "lib.csv", "TileLibraryBegin\nTile,T/T.csv\nTileLibraryEnd\n")
    _write(lib / "include/Base.csv", "JUMP,X,0,0,Y,1\n")
    _write(lib / "include/Base.list", "A,B\n")
    _write(lib / "T/T.csv", T_CSV)
    _write(lib / "T/T_switch_matrix.list", "INCLUDE, ../include/Base.list\n")
    _write(lib / "T/gds_config.yaml", "{}\n")
    _write(lib / "V/V.csv", V_CSV)
    _write(lib / "V/V_switch_matrix.list", "A,B\n")
    _write(lib / "S/S.csv", S_CSV)
    return tmp_path


def _load(assets: Path) -> TileLibrary:
    return load_tile_library(
        assets / "tiles/lib",
        load_primitives(assets / "primitives"),
        assets / "primitives",
    )


@pytest.mark.parametrize(
    ("tile", "language", "expected"),
    [
        (
            "T",
            Language.VHDL,
            [
                "tiles/lib/T/T.csv",
                "tiles/lib/T/T_switch_matrix.list",
                "tiles/lib/T/gds_config.yaml",
                "tiles/lib/include/Base.csv",
                "tiles/lib/include/Base.list",
                "primitives/A/fabulous/A.vhdl",
            ],
        ),
        (
            "V",
            Language.VERILOG,
            [
                "tiles/lib/V/V.csv",
                "tiles/lib/V/V_switch_matrix.list",
                "primitives/B/fabulous/B.v",
            ],
        ),
        (
            "S",
            Language.VERILOG,
            [
                "tiles/lib/S/S.csv",
                "tiles/lib/T/T.csv",
                "tiles/lib/T/T_switch_matrix.list",
                "tiles/lib/T/gds_config.yaml",
                "tiles/lib/include/Base.csv",
                "tiles/lib/include/Base.list",
                "primitives/A/fabulous/A.v",
                "tiles/lib/V/V.csv",
                "tiles/lib/V/V_switch_matrix.list",
                "primitives/B/fabulous/B.v",
            ],
        ),
    ],
)
def test_tile_files(
    assets: Path, tile: str, language: Language, expected: list[str]
) -> None:
    files = _load(assets)[tile].files(language)
    assert sorted(files) == sorted((assets / p).resolve() for p in expected)


@pytest.mark.parametrize(
    ("tile", "kind", "deprecated", "languages"),
    [
        ("T", TileKind.TILE, True, {Language.VERILOG, Language.VHDL}),
        ("V", TileKind.TILE, False, {Language.VERILOG}),
        ("S", TileKind.SUPERTILE, False, {Language.VERILOG}),
    ],
)
def test_tile_metadata(
    assets: Path, tile: str, kind: TileKind, deprecated: bool, languages: set[Language]
) -> None:
    source = _load(assets)[tile]
    assert (source.kind, source.deprecated, source.languages) == (
        kind,
        deprecated,
        languages,
    )


def test_library_skips_root_files(assets: Path) -> None:
    assert sorted(_load(assets)) == ["S", "T", "V"]


@pytest.mark.parametrize("tile", ["V", "S"])
def test_files_rejects_unsupported_language(assets: Path, tile: str) -> None:
    with pytest.raises(ValueError, match="vhdl"):
        _load(assets)[tile].files(Language.VHDL)


def test_primitives_need_a_fabulous_source(assets: Path) -> None:
    found = load_primitives(assets / "primitives")
    assert sorted(found) == ["A", "B"]
    assert found["A"].hdl == {
        Language.VERILOG: (assets / "primitives/A/fabulous/A.v").resolve(),
        Language.VHDL: (assets / "primitives/A/fabulous/A.vhdl").resolve(),
    }
    assert found["A"].techmaps == (
        (assets / "primitives/A/yosys/techmap/map.v").resolve(),
    )


def _replace_in(rel: str, old: str, new: str) -> Callable[[Path], None]:
    def edit(assets: Path) -> None:
        path = assets / "tiles/lib" / rel
        path.write_text(path.read_text().replace(old, new))

    return edit


def _add(rel: str, text: str) -> Callable[[Path], None]:
    return lambda assets: _write(assets / "tiles/lib" / rel, text)


def _both(*edits: Callable[[Path], None]) -> Callable[[Path], None]:
    def edit(assets: Path) -> None:
        for each in edits:
            each(assets)

    return edit


@pytest.mark.parametrize(
    ("edit", "error", "match"),
    [
        (_replace_in("T/T.csv", "TILE,T,", "TILE,U,"), ValueError, "directory is T"),
        (_replace_in("T/T.csv", "DEPRECATED", "LEGACY"), ValueError, "only DEPRECATED"),
        (
            _replace_in("T/T.csv", "../include/Base.csv", "../include/Gone.csv"),
            FileNotFoundError,
            "Gone.csv",
        ),
        (
            _replace_in("T/T_switch_matrix.list", "Base.list", "Gone.list"),
            FileNotFoundError,
            "Gone.list",
        ),
        (
            _replace_in("T/T.csv", "../../../primitives/A", "../../../primitives/Nope"),
            FileNotFoundError,
            "Nope",
        ),
        (
            _replace_in("V/V.csv", "B/fabulous/B.v", "B/fabulous/B.sv"),
            ValueError,
            "known HDL",
        ),
        (
            _both(
                _add("V/local.v", "// not a primitive\n"),
                _replace_in(
                    "V/V.csv", "../../../primitives/B/fabulous/B.v", "./local.v"
                ),
            ),
            ValueError,
            "outside",
        ),
        (
            _add("nested/T/T.csv", "TILE,T,,\nEndTILE\n"),
            ValueError,
            "two tiles named T",
        ),
        (_replace_in("S/S.csv", "V,,", "W,,"), ValueError, "lacks"),
    ],
)
def test_library_errors(
    assets: Path,
    edit: Callable[[Path], None],
    error: type[Exception],
    match: str,
) -> None:
    edit(assets)
    with pytest.raises(error, match=match):
        _load(assets)


def test_registry_lists_names_on_miss() -> None:
    registry = Registry("widget", lambda: {"a": 1, "b": 2})
    with pytest.raises(KeyError, match=r"No widget 'c'. Available: \['a', 'b'\]"):
        registry["c"]


@pytest.mark.parametrize("library", ["classic", "fabulous", "tiny"])
def test_packaged_libraries_load(library: str) -> None:
    assert len(tile_libraries[library]) > 0


def test_packaged_fabulous_library_supports_both_languages() -> None:
    for tile in tile_libraries["fabulous"].values():
        assert tile.languages == {Language.VERILOG, Language.VHDL}, tile.name
        assert tile.deprecated, tile.name
    assert primitives["MULADD"].hdl.keys() == {Language.VERILOG, Language.VHDL}
