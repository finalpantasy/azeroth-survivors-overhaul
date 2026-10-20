# Recipe contract (`as-recipe/1`)

A recipe is one JSON file that tells the installer how to rebuild a mod **from files the owner already has**
(their own Azeroth Survivors install, their own WoW client, or files they supply themselves). A recipe is data only:
it never contains Blizzard bytes, scripts or shell commands. Anything that executes is a named, hash-pinned tool from
`tools/registry.json`.

Put recipes in `recipes/` (shipped) or any folder passed with `--recipes-dir`. The validator rejects unknown keys,
bad hashes, unsafe paths and unregistered tools, so a typo fails at load time, not mid-install.

## Top level

| key | meaning |
|---|---|
| `schema` | must be `as-recipe/1` |
| `id`, `version` (semver), `title`, `description` | identity; `id` is `[a-z0-9-]+` |
| `status` | `ready` (default-eligible), `experimental` (needs `--allow-experimental`), `unavailable` (listed, never built) |
| `default` | true = included when no `--recipe` is given (only `ready` recipes) |
| `unavailable_reason` | required when `status` is `unavailable`: why the inputs are not reproducible from owned installs |
| `requires.game.exe_sha256` | list of AzerothSurvivors.exe SHA-256 values the recipe supports |
| `requires.game.versions` | informational version strings |
| `requires.tools`, `requires.disk_mb` | tool ids that must resolve, free space needed |
| `sources` | map of source id to source (below) |
| `steps` | ordered list of steps (below) |
| `outputs` | what is written: `mpq_files` and/or `exe_patches` |
| `acceptance` | checks run on the built result before anything is installed |

## Sources

* `casc` – files from the owner's Retail/Forever client, via the offline CASC runtime (`CascLib.dll`, supplied by the
  owner, hash-checked). Fields: `product`, `version`, `build_number`, `build_key`, `cdn_key`, `locale` (**enUS only**
  for now: the offline runtime's metadata check is hard-coded), optional `build_config`/`cdn_config` `{bytes,sha256}`
  pins, and `files[]` = `{file_data_id, name, bytes, sha256, role}`. The build is pinned: a client on another build
  is refused with a message naming the build it has and the one needed.
* `game-chain` – files read from the owner's own game MPQs (`patch.MPQ`, `patch-2..8.MPQ`, later archive wins).
  `exclude_slots` lists the installer slots to ignore so the base is always the official one. `files[]` =
  `{path, sha256: [accepted hashes]}`.
* `user-supplied` – files the owner must provide (never downloaded). `instructions` is shown verbatim when they are
  missing; `files[]` = `{name, bytes, sha256, origin}`. Passed with `--source <id>=<folder>`. The installer never
  downloads; the owner may run the separate helper `tools/fetch_community_sources.py --recipe <id> --dest <folder> --yes`,
  which fetches only these hash-pinned files (https://raw.githubusercontent.com/ only) and keeps nothing that fails
  its size/sha256 check. Check the source repositories' licenses first: community files are not redistributed here.

## Steps

Each step has a unique `id`. Later steps and outputs reference earlier files as **`"<step-id>:<relative path>"`**
(forward slashes, no `..`).

| op | fields | result |
|---|---|---|
| `casc-export` | `source` | exports the source's files, verifies every hash |
| `game-extract` | `source` | extracts files from the game's official archive chain |
| `user-source` | `source` | copies and verifies the owner's files |
| `run-tool` | `tool`, `inputs` (`{name: "step:path"}`), `expect_outputs[{path,sha256}]` | runs a registered tool with `python -I`, timeout, then verifies each declared output hash |
| `binary-edit` | `input`, `output`, `expect_input_sha256`, `edits[{offset,length,expect_sha256,new_b64}]`?, `append_b64`?, `expect_output_sha256` | byte-exact patch of one file (same-length edits, then an optional appended tail). Used to turn a community model into the shipped variant without shipping the model |
| `dbc-edit` | `input`, `output`, `expect_input_sha256`, `edits[{row_id,column,type,expect_before,value}]`, `expect_output_sha256` | WDBC cell edit (string or uint) that refuses to run if the cell is not what `expect_before` says |

### Tools (`tools/registry.json`)

Each tool is `{script, sha256, search[], argv?, timeout?}`. `argv` placeholders: `{in:<name>}`, `{out}`,
`{tool:<id>}`. The script is found in one of the `search` folders and **its SHA-256 must match**, otherwise the run
stops. To add or update a tool: put the script in `tools/`, run `python -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" tools/<script>`,
and write that hash in the registry entry. Changing a tool without changing the hash is intentionally impossible.

## Outputs

* `mpq_files[]` = `{slot, path, from, sha256}`. `slot` is **only** `patch-0.MPQ` or `patch-1.MPQ` (the installer's
  overlay slots, loaded below the official `patch-2..8`). `path` uses backslashes (`Item\Thing\x.m2`). The packed
  MPQ is deterministic (sorted names, zlib sectors), so the same inputs give the same archive hash.
* `exe_patches[]` = `{target: "AzerothSurvivors.exe", expect_exe_sha256, result_exe_sha256, edits[{offset,length,expect_sha256,new_b64}]}`.
  Same-length, non-overlapping edits, plus an optional `append_b64` tail (a new PE section, as the renderer's sharpen
  patch does). Every edit's current bytes must match `expect_sha256` (checked against the ORIGINAL EXE) and a
  single patch's result must match `result_exe_sha256`. Several recipes compose into one EXE when they share the
  same base, their edits are disjoint and at most one appends; otherwise the plan reports an `exe-conflict` blocker.
  A recipe that rewrites existing bytes with a different length is still out of scope.

Slot policy when the target slot exists: installer-owned (hash matches `state.json`) → merged automatically;
foreign → `--foreign-slots refuse|merge|replace` (default refuse). Replacing always backs the old file up first.

## Acceptance checks

* `mpq-has` – `{slot, path, sha256}`: the built slot contains that file with that hash.
* `dbc-cell` – `{slot, path, row_id, column, type, equals}`: a cell in the built DBC.
* `exe-sha256` – `{equals}`: the patched EXE hash (skipped when several recipes share the EXE).
* `slot-sha256` – `{slot, equals}`: the whole built slot hash. Checked only when that recipe alone produced the slot
  and nothing was merged into it; otherwise reported as skipped.

## Profiles (`profiles/*.json`, `as-profile/1`)

An ordered list of recipe ids that together make one game state: `{schema, id, version, title, description, recipes[],
allow_experimental?, expect_slot_sha256?{slot: sha256}}`. `expect_slot_sha256` is enforced at build time when the
slot is built only from the profile's recipes (no merged foreign content): a mismatch stops the build
(`profile-slot-hash`). Use `--profile <id>` instead of `--recipe`. Shipped: `current-upgrade` (HD terrain, 12 HD
spell files, Retail silver mace) and `current-upgrade-renderer` (adds the water and sharpen EXE patches).

### Conflicts between recipes and `priority`

* Same slot + same path from two recipes with **identical bytes**: fine, written once.
* Same slot + same path with **different bytes**: the build stops with `recipe-conflict`, naming both recipes and both
  hashes. To resolve it on purpose, add `"priority": [ids, highest first]` to the profile; when both recipes are listed
  the higher one wins, the loser is dropped for that path, and the choice is recorded in the plan warnings and in
  `build.json` -> `conflicts_resolved`. A recipe missing from the list never silently loses.
* EXE edits that overlap, patches written for different base executables, or two recipes that append a section: always an
  error that names the recipes (byte intervals cannot be merged by priority).
* Whole-file DBC edits conflict like any other path; two recipes that each edit `ItemDisplayInfo.dbc` need one merged
  recipe or a priority winner.

Checks run on the built artifacts (and are repeated after install), never on the live game before the transaction.

## Producing a recipe from a backlog entry

For an `analysis/asset-coverage/recipes-backlog.json` entry (not present when this was written; shape assumed):

1. `casc` source: one `files[]` row per FileDataID with bytes and SHA-256 (hash the export once on a machine that
   owns the client). Pin `build_key`/`cdn_key`.
2. `game-chain` source for any base DBC you edit, with the official hash.
3. Steps: `casc-export` → `run-tool` (converter, hash-pinned) → `game-extract` → `dbc-edit`.
4. `outputs.mpq_files` for each shipped file, `acceptance` for each DBC cell and each model.
5. Run `as_installer.py plan` and `build` against a copy, then add `expect_output_sha256` values from that
   verified build. Use `dev/make_mace_recipe.py` as the template.
6. If an input is a community download or cannot be reproduced from owned installs, set
   `"status":"unavailable"` and say why in `unavailable_reason` (see `hd-terrain-community.json`).

Reference recipes: `retail-mace-kultiras-silver.json` (ready), `water-shader-adt-liquid.json` (experimental EXE patch),
`hd-terrain-community.json` and `hd-spells-community.json` (ready, user-supplied community files),
`renderer-v3-post.json` (experimental same-length EXE patch, official v0.103.1; the earlier v0.103.0 `renderer-sharpen-ffx` with an appended section is kept under `recipes/legacy-0.103.0/`).

Reproducibility limits: the HD terrain, spell and player-model sources are community repositories, not part of any
WoW install, so they are `user-supplied` and hash-pinned (the recipe reproduces the exact current files byte for byte,
including the edited Frostbolt and Arcane Explosion models, from the pinned community originals). The two EXE
recipes were verified by hash only; their combination is not runtime-tested.
