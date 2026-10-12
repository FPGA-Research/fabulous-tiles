from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from fabulous_tiles import (
    Language,
    Registry,
    Status,
    TileKind,
    TileLibrary,
    load_entry_points,
    load_primitives,
    load_tile_library,
    primitives,
    tile_libraries,
)

T_CSV = """TILE,T,DEPRECATED,,
INCLUDE,../include/Base.csv
BEL,../../../primitives/A/fabulous/A.{HDL_SUFFIX},X_
#BEL,retired.{HDL_SUFFIX},Y_
MATRIX,./T_switch_matrix.list
EndTILE
"""
V_CSV = """TILE,V,,,
BEL,../../../primitives/B/fabulous/B.v
MATRIX,./V_switch_matrix.list
EndTILE
"""
S_CSV = """SuperTILE,S,EXPERIMENTAL,
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
        assets / "tiles/lib", load_primitives(assets / "primitives")
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
    ("tile", "kind", "status", "languages"),
    [
        ("T", TileKind.TILE, Status.DEPRECATED, {Language.VERILOG, Language.VHDL}),
        ("V", TileKind.TILE, Status.STABLE, {Language.VERILOG}),
        ("S", TileKind.SUPERTILE, Status.EXPERIMENTAL, {Language.VERILOG}),
    ],
)
def test_tile_metadata(
    assets: Path, tile: str, kind: TileKind, status: Status, languages: set[Language]
) -> None:
    source = _load(assets)[tile]
    assert (source.kind, source.status, source.languages) == (kind, status, languages)


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
        (
            _replace_in("T/T.csv", "DEPRECATED", "LEGACY"),
            ValueError,
            "EXPERIMENTAL or DEPRECATED",
        ),
        (
            _replace_in("T/T.csv", "DEPRECATED", "STABLE"),
            ValueError,
            "Leave it empty",
        ),
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
            "no single registered primitive",
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
        assert tile.status is Status.DEPRECATED, tile.name
    assert primitives["MULADD"].hdl.keys() == {Language.VERILOG, Language.VHDL}


@dataclass(frozen=True)
class _EntryPoint:
    name: str
    value: str
    target: object

    def load(self) -> object:
        return self.target


@pytest.mark.parametrize(
    ("roots", "error", "match"),
    [
        ([], RuntimeError, "No installed package"),
        (["not a path"], TypeError, "not a directory Path"),
        (["a", "b"], ValueError, "'x' is registered by both"),
    ],
)
def test_load_entry_points_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    roots: list[str],
    error: type[Exception],
    match: str,
) -> None:
    registered = [
        _EntryPoint(
            name=r,
            value=f"pkg_{r}:ROOT",
            target=r if r == "not a path" else tmp_path / r,
        )
        for r in roots
    ]
    for r in roots:
        (tmp_path / r).mkdir(exist_ok=True)
    monkeypatch.setattr(
        "fabulous_tiles.registry.entry_points", lambda group: registered
    )
    with pytest.raises(error, match=match):
        load_entry_points("test.group", "widget", lambda root: {"x": root})


def test_load_entry_points_merges_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for r in ("a", "b"):
        (tmp_path / r).mkdir()
    registered = [
        _EntryPoint(name=r, value=f"pkg_{r}:ROOT", target=tmp_path / r)
        for r in ("b", "a")
    ]
    monkeypatch.setattr(
        "fabulous_tiles.registry.entry_points", lambda group: registered
    )
    found = load_entry_points("test.group", "widget", lambda root: {root.name: root})
    assert found == {"a": tmp_path / "a", "b": tmp_path / "b"}


def _dual_language(assets: Path) -> None:
    """Give V a VHDL source too, so the whole library builds in both languages."""
    _write(assets / "primitives/B/fabulous/B.vhdl", "-- B\n")
    _replace_in("V/V.csv", "B/fabulous/B.v", "B/fabulous/B.{HDL_SUFFIX}")(assets)


@pytest.mark.parametrize("language", list(Language))
def test_materialise_localises_bel_rows(
    assets: Path, tmp_path: Path, language: Language
) -> None:
    _dual_language(assets)
    dest = tmp_path / "dest"
    _load(assets).materialise(dest, language)
    suffix = language.suffix
    assert (dest / "T/T.csv").read_text() == T_CSV.replace(
        "../../../primitives/A/fabulous/A.{HDL_SUFFIX}", f"./A.{suffix}"
    ).replace("{HDL_SUFFIX}", suffix)
    assert (dest / f"T/A.{suffix}").read_text() == (
        assets / f"primitives/A/fabulous/A.{suffix}"
    ).read_text()
    assert f"BEL,./B.{suffix}\n" in (dest / "V/V.csv").read_text()
    assert (dest / f"V/B.{suffix}").is_file()
    assert (dest / "S/S.csv").read_text() == S_CSV
    assert (dest / "include/Base.list").is_file()
    assert not (dest / "lib.csv").exists()


def test_materialise_rejects_unsupported_language(assets: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="lib/S supports"):
        _load(assets).materialise(tmp_path / "dest", Language.VHDL)


def test_materialise_keeps_crlf(assets: Path, tmp_path: Path) -> None:
    csv = assets / "tiles/lib/T/T.csv"
    csv.write_bytes(csv.read_bytes().replace(b"\n", b"\r\n"))
    dest = tmp_path / "dest"
    _load(assets).materialise(dest, Language.VERILOG)
    assert b"BEL,./A.v,X_\r\n" in (dest / "T/T.csv").read_bytes()


def test_materialise_writes_writable_copies(assets: Path, tmp_path: Path) -> None:
    library = _load(assets)
    for path in assets.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    dest = tmp_path / "dest"
    try:
        library.materialise(dest, Language.VERILOG)
    finally:
        for path in assets.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
    for path in dest.rglob("*"):
        assert path.stat().st_mode & 0o200, path


@pytest.mark.parametrize(
    "edit",
    [
        # Two BEL rows select different files named A.v.
        _both(
            lambda assets: _write(assets / "primitives/C/fabulous/C.v", "// C\n"),
            lambda assets: _write(assets / "primitives/C/fabulous/A.v", "// other A\n"),
            _replace_in(
                "V/V.csv",
                "BEL,../../../primitives/B/fabulous/B.v",
                "BEL,../../../primitives/A/fabulous/A.v\n"
                "BEL,../../../primitives/C/fabulous/A.v",
            ),
        ),
        # A tile file sorting before the tile CSV clashes with its BEL source.
        _add("T/A.v", "// tile local\n"),
        # A tile file sorting after the tile CSV clashes with its BEL source.
        _both(
            lambda assets: _write(assets / "primitives/Z/fabulous/Z.v", "// Z\n"),
            _add("V/Z.v", "// tile local\n"),
            _replace_in("V/V.csv", "B/fabulous/B.v", "Z/fabulous/Z.v"),
        ),
    ],
)
def test_materialise_rejects_clashing_files(
    assets: Path, tmp_path: Path, edit: Callable[[Path], None]
) -> None:
    edit(assets)
    with pytest.raises(FileExistsError, match="which differ"):
        _load(assets).materialise(tmp_path / "dest", Language.VERILOG)
