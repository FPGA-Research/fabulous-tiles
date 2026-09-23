# DEPRECATED: do not use for new fabrics

This tile library is a verbatim copy of the tiles shipped in the FABulous project template (`fabulous/fabric_files/FABulous_project_template_{common,verilog,vhdl}/Tile` at FABulous commit `e9e85cc71`). It exists only so that FABulous can keep creating and building its legacy demo fabric while it migrates to consume tiles from this repository. It will be removed once that migration is complete.

New fabrics should use the `classic` or `tiny` library.

The files are kept in the FABulous template format and are not a LibreLane tile library:

- BEL rows use the `{HDL_SUFFIX}` placeholder, which FABulous replaces with `v` or `vhdl` at project creation. Both the Verilog and VHDL BEL sources sit beside each tile CSV.
- Hardening settings are the FABulous `gds_config.yaml` files. There is no per-tile `config.yaml`, so `TILE_LIBRARY=deprecated make` does not work and CI does not build this library.
- `include/` holds the shared `Base.csv` and `Base.list` that the tile CSVs pull in with `INCLUDE`. It plays the role `common/` plays in the other libraries.
