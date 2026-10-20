# Azeroth Survivors recipe installer

Rebuilds visual mods on **your** machine from files you already own, then installs them transactionally with a
backup and a receipt, and can roll them back. This package contains code, recipes (hashes and instructions) and
tests only: **no Blizzard assets, no game EXE, no CascLib.dll.**

## Needs

* Windows, Python 3 (tested on 3.14), standard library only.
* Your Azeroth Survivors folder (the one with `AzerothSurvivors.exe`).
* For recipes with a `casc` source: your own WoW client folder (the build named in the recipe) and the audited
  offline `CascLib.dll`, supplied separately (`tools/casc-offline/build.ps1` builds it; the installer checks its
  SHA-256 before loading it).

## Commands

```
python as_installer.py detect   --game-root <game> [--client-root <wow>]
python as_installer.py recipes
python as_installer.py check    --game-root <game> [--client-root <wow>] [--casc-dll <dll>]
python as_installer.py plan     --game-root <game> --client-root <wow> --casc-dll <dll> [--recipe ID]
python as_installer.py apply    --game-root <game> --client-root <wow> --casc-dll <dll> --yes
python as_installer.py status   --game-root <game>
python as_installer.py rollback --game-root <game> [--transaction ID] [--yes]
python as_installer.py profiles
python as_installer.py integrate --game-root <game> --profile current-upgrade [--saves-dir <dir>]          (plan only)
python as_installer.py integrate --game-root <game> --profile current-upgrade --apply --yes --work <new dir> ...
```

* `plan` changes nothing and lists targets, what is backed up and every blocker with a fix hint.
* `build` writes a new work folder only; `apply` = plan, build, verify, then install. `--use-build <dir>` installs a
  previously built and verified folder.
* `rollback` is a dry run unless `--yes`. It refuses if the installed file no longer matches the receipt (you or
  another tool changed it) or a newer transaction owns it.
* `--json` for machine output, `--saves-dir none` to skip the save-folder proof, exit code 0 ok / 1 error.

## Whole-install profile (`integrate`) and status

* `profiles` lists ordered recipe sets (`profiles/*.json`). `integrate --profile <id>` is the full-install flow for a real
  game folder: without `--apply` it is a plan only (nothing is written) that also reports the saves/config locations,
  the rollback command and, by file content, which recipes are already installed (`already-installed`,
  `partly-installed` or `not-installed`).
* `integrate --apply --yes --work <new dir>` builds and verifies in the work folder, then installs one transaction:
  refuses while the game runs; verified backups of every touched file; a read-only copy of the saves folder
  (`%APPDATA%\wenilla-rogue` by default, override with `--saves-dir`) and `benilla-config` under
  `<game>/mod-backups/installer/transactions/<id>/saves-snapshot/`; hash proof that saves and config are unchanged;
  atomic replace; receipt; any failure restores everything. Undo with `rollback --yes` (one command).
* `status --game-root <game>` prints each recipe as installed / partial / absent / unavailable (checked by file
  content, so a hand-made install is recognised), installer-managed files and transactions.
* Community recipes need `--source <id>=<folder>` (see `RECIPES.md`); `--foreign-slots merge` is how the installer adopts
  an existing hand-made `patch-0/1.MPQ` (its files are kept, recipe files added or confirmed).
* Launcher: `Play Azeroth Survivors.cmd` stays the way to start the game; the installer does not replace it. To see what
  the launcher's game folder currently has, run `status` against that folder (see `LAUNCHER.md`).

## Safety model

* Writes only `Data/patch-0.MPQ`, `Data/patch-1.MPQ` and `AzerothSurvivors.exe`; official archives are never opened for write.
* Refuses while the game runs or a target file is locked; foreign `patch-0/1.MPQ` are refused unless you choose `merge`/`replace`.
* Every file is hash-verified at each stage; backups are verified before the first write; a failure restores the backups.
* Saves and `benilla-config` are hashed before and after; a change aborts and restores.
* State and receipts: `<game>/mod-backups/installer/`.

See `RECIPES.md` for writing recipes and `tests/` (`python -m unittest` from that folder).

## Local "next" build tooling (`next/`, not packaged)

`next/next_build.py` is an owner-only helper for the Azeroth Survivors workspace: it reads the worker status files, keeps a
preview copy of the game (`previews/next-v0.103.0`) in sync with every recipe marked VERIFIED, and refreshes
`analysis/installer/next-manifest.md`. It uses fixed workspace paths and is deliberately not part of the shareable ZIP.
Recipe conflicts are handled by the engine: identical bytes pass, differing bytes are an error unless both recipes are in the
profile's `priority` list (see `RECIPES.md`, "Conflicts between recipes and priority").
