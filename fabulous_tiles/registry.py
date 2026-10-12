"""Tile libraries and primitives registered through entry points.

A package registers a directory of tile libraries under the entry-point group
`fabulous.tile_libraries` and a directory of primitives under `fabulous.primitives`;
this package registers its own directories the same way. Inside a registered
directory, every subdirectory is a tile library and a primitive is any `<name>/`
holding `fabulous/<name>.v` or `fabulous/<name>.vhdl`. Adding such a directory
registers it, so no list of tiles or primitives exists to keep in sync. The
registries scan on first access and cache the result for the life of the process.
"""

from collections.abc import Callable, Iterator, Mapping
from importlib import resources
from importlib.metadata import entry_points
from pathlib import Path

from fabulous_tiles.model import Language, PrimitiveSource, TileLibrary
from fabulous_tiles.tile_csv import load_tile_library

TILE_LIBRARIES_GROUP = "fabulous.tile_libraries"
PRIMITIVES_GROUP = "fabulous.primitives"

PACKAGE_ROOT = resources.files(__package__)
if not isinstance(PACKAGE_ROOT, Path):
    raise RuntimeError(
        f"fabulous_tiles is installed as {PACKAGE_ROOT!r}, not a directory. Install "
        "it from a wheel or a source checkout, not a zip archive."
    )
TILES_ROOT = PACKAGE_ROOT / "tiles"
PRIMITIVES_ROOT = PACKAGE_ROOT / "primitives"


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


def load_primitives(root: Path) -> dict[str, PrimitiveSource]:
    """Read every primitive directory under `root` that holds a `fabulous/` source.

    Raises
    ------
    FileNotFoundError
        If a `fabulous/` directory holds no `<name>.v` or `<name>.vhdl`.
    """
    found: dict[str, PrimitiveSource] = {}
    for d in sorted(root.iterdir()):
        if not (d / "fabulous").is_dir():
            continue
        d = d.resolve()
        hdl = {
            language: d / "fabulous" / f"{d.name}.{language.suffix}"
            for language in Language
            if (d / "fabulous" / f"{d.name}.{language.suffix}").is_file()
        }
        if not hdl:
            raise FileNotFoundError(
                f"Primitive {d.name} has no fabulous/{d.name}.v or "
                f"fabulous/{d.name}.vhdl in {d}."
            )
        readme = d / "README.md"
        found[d.name] = PrimitiveSource(
            name=d.name,
            root=d,
            hdl=hdl,
            techmaps=tuple(sorted(p for p in (d / "yosys").rglob("*") if p.is_file())),
            readme=readme if readme.is_file() else None,
        )
    return found


primitives: Registry[PrimitiveSource] = Registry(
    "primitive",
    lambda: load_entry_points(PRIMITIVES_GROUP, "primitive", load_primitives),
)
tile_libraries: Registry[TileLibrary] = Registry(
    "tile library",
    lambda: load_entry_points(
        TILE_LIBRARIES_GROUP,
        "tile library",
        lambda root: {
            d.name: load_tile_library(d, primitives)
            for d in sorted(root.iterdir())
            if d.is_dir()
        },
    ),
)
