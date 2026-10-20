# skinned-converter (R4): animated modern M2 -> native MD20 v256

Converts a skinned, animated modern creature model (MD21 v264-v274, Forever/Classic or Retail, embedded or external
`.anim` data) into the game's native v256 format, with its skeleton, animations, attachments, events and textures,
so it can replace a native creature model in an MPQ overlay. Owner: W8. Format notes: `analysis/skinned-converter/NATIVE-FORMAT.md`.

## Files

| file | what |
|---|---|
| `m2ir.py` | modern reader (MD21 chunks, SKIN, AFM2/.anim), native v256 writer and reader, shared IR |
| `skinned_convert.py` | CLI and pipeline: fetch, select sequences, prune geosets, trim bones, batches/env layers, lookups, textures, write, readback, pack |
| `preview.py` + `render_preview.py` | offline check: decodes the output BLPs with the engine's own `mpq-inspect`, reads the written bytes back, renders stand/walk/run/attack/cast/death and measures per-sequence vertex excursion |
| `decimate.py` | quadric half-edge-collapse mesh decimation (vertices only move onto neighbours, so weights/UVs stay valid; seams collapse together) |
| `recipes/*.json` | one conversion recipe per model (kobold, murloc, gnoll, raptor, golemharvest, bloodelfmale) |
| `skinned_step.py` | installer entry point (`run-tool` tool `skinned-converter`); pins the modules and recipes above by SHA-256 |
| `make_installer_recipe.py` | writes `installer/recipes/skinned-<name>-forever.json` and proves it by running `skinned_step.py` on installer-style inputs |

## Use

```bash
python tools/skinned-converter/skinned_convert.py tools/skinned-converter/recipes/kobold.json --cache analysis/skinned-converter/work/cache-forever --out-dir analysis/skinned-converter/out/kobold-v2 --fetch forever
```

```bash
python tools/skinned-converter/preview.py analysis/skinned-converter/out/kobold-v2
```

Output: `payload/` (model + BLPs at native paths), `<Name>.mpq`, `conversion-report.json` (source/output cost, ratio vs
the native model, sequences, everything dropped), `preview-converted.png`. Nothing is installed. The cache holds
Blizzard files: local only, never shared.

## Recipe keys

`name`; `source.profile` (forever|retail), `source.model` (FileDataID), `source.lod` (skin index; LDV1 LOD skins are
handled), optional `source.skin`; `native_model_path`; `geosets` (default: every id < 100 plus each group's x01);
`textures.<slot>` = `{"replaceable": true, "skins": {native skin path: fdid}}` (keeps the replaceable type, publishes the
modern skins under the native names so every display keeps its skin) or `{"fdid", "path"}` (hardcoded type-0 texture);
`env_layers` (`split` = base + additive env layer, `drop`; use `drop`: modern env layers are masked by the base
texture alpha, which a split native additive batch cannot do, so `split` washes the model out in game); `trim_bones`
(drop bones nothing skins or names); `decimate` = `{"target_vertices": N, "weight_penalty", "boundary_weight"}`;
`drop_anims` (animation ids); `scale`.

## Cost controls (vs the native model)

- `source.lod`: pick a lower modern LOD skin (murloc2/raptor2 have 4, gnoll2 has 8; kobold2 has none smaller).
- `trim_bones`: keeps weighted bones, attachment bones, section centres and native key bones (+ ancestors); events move
  to the nearest kept ancestor. Pose is unchanged (excursion identical before/after).
- `decimate`: kobold 1875 -> 949 verts (1.84x native), gnoll 1753 -> 782 (1.75x); previews look the same apart from
  slightly more faceted limbs. Gnoll bones stay 2.28x (all are weighted).

## Characters (W7, W11)

`recipes/bloodelfmale.json` converts the TBC-era Blood Elf male (117170) to `Character/BloodElf/Male/BloodElfMale.m2`:
list every geoset the native character logic knows in `geosets` (the game picks hair/gear geosets itself), keep the
type 1/2/6 slots replaceable (body/cape/hair come from the compositor and CharSections), leave `trim_bones` off.
Modern texture types > 13 (customization slots) are removed when no kept batch uses them. HD `*_hd` bodies are not
supported: they keep their skeleton in a separate `.skel` (SKID). Lab status: see coordination/workers/skinned.md.
