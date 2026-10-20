# Retail rigid preview builder

This builder converts one pinned, rigid retail M2 asset into an isolated WoW 1.12.1-compatible MD20 v256 model and MPQ. It validates the exact source layout and SHA-256 pins, then requires the generated model to match the accepted preverified MD20 hash. It is intentionally specific to this asset and is not a general modern-M2 converter.

## Portable source layout

Pass a directory containing the export report and these four files:

```text
source-dir/
  export-report.json
  model-1693135.m2
  skin-1693139.skin
  texture-144333.blp
  texture-1733940.blp
```

The report must identify a successful WoW `12.1.5.70448` export, unchanged metadata, no progress download events, and exactly four assets totaling 194,288 bytes. The builder checks the four pinned FileDataIDs, byte counts, SHA-256 values, nested model tracks, vertex attributes, attachments, texture records, and skin topology. Portable mode needs no CASC or game data; its only supplemental code dependency is the first-party `pack-mpq.py` script.

From the project root, convert an existing export into a new output directory:

```powershell
python tools/retail-rigid-preview/build_retail_rigid.py `
  --source-dir analysis/portable-export-verified `
  --output-dir analysis/retail-rigid-preview/output-portable
```

The output path must be outside the source directory and must not already exist. Relative paths are resolved from the current working directory. The builder uses the first-party pure-Python `pack-mpq.py` beside the builder or in its parent directory. If it is elsewhere, pass `--packer C:\path\to\pack-mpq.py`. The output includes loose MPQ payloads, the MPQ, a packer log, and `conversion-report.json`.

For internal project use, omitting `--source-dir` keeps the original pinned closure source. `--preflight` validates inputs without writing output. `--help` lists all options.

## Verification and limits

Portable builds use `preverified-output-hash` mode. They validate the loose payload hashes and MPQ v1 header, and require the MD20 to match `b69b55d092912005907d9190a3616160d0b2e4cf845d8eb8860354eefab8004d`, an exact model hash previously checked with the native parser. The portable build does not run or bundle `mpq-inspect.exe`, does not extract MPQ contents for readback, and records `native_parser_status: not_run`.

The conversion is a static visual approximation. All 1,712 vertices are rigid on bone 0; the output collapses the unused bones to one identity bone and emits a neutral 1,000 ms sequence. It preserves positions, normals, UV0, UV1, topology, and all five attachment anchors. The two-layer modern batch becomes one opaque diffuse batch. The reflective auxiliary texture and modern shader/material behavior are omitted. The texture-weight track's single 1.0 key is omitted because the output material is opaque. The `$WTB`/`$WTT` events are omitted, so their weapon-trail start/end marker semantics are not reproduced. Source sequence timing and flags are also omitted.

No retail item-ID/DB2 mapping or exact renderer equivalence is claimed, and this output has not been validated in the game client.
