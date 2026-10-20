# Staging allowlist

**State: policy only. No final ZIP exists.** Every staged file must be individually named and hashed in the package manifest before an archive is made. Wildcards, recursive copies, and “copy this folder” rules are prohibited.

## Permitted package entries

| Package path | Repository source | Condition |
| --- | --- | --- |
| README.md | tools/distribution/README.md | Always; keep the preflight-versus-extraction distinction and current limitations. |
| THIRD_PARTY_NOTICES.txt | tools/distribution/THIRD_PARTY_NOTICES.txt | Always; preserve the full applicable notices and unresolved-provenance caveats. |
| STAGING_ALLOWLIST.md | tools/distribution/STAGING_ALLOWLIST.md | Always; package policy and audit boundary. |
| tools/local_installer.py | tools/local-installer/local_installer.py | First-party script (current SHA-256 2057a06afcf9164a1a23f7c1b23d572879b9585bf3d500d24b2176f0db65d017); only for preflight and guarded application/rollback of a separately reviewed overlay. Record the exact source hash. |
| tools/CascLib.dll | tools/casc-offline/build-x64/Release/CascLib.dll | **Conditional.** Include only after the pinned binary and these notices are accepted for distribution, the package manifest records the exact hash, and the supported runtime is documented. Current candidate hash: 221833e9afa0d534a4e0e5679cabb24a827a8ac1560b6722acd6d500bfcc28b3. |
| tools/casc_offline.py | tools/casc-offline/casc_offline.py | **Not in the default package.** It may be added only after its exact hash and supported profile/path contract are recorded. Current code is experimental, profile-specific, and defaults to E:\World of Warcraft; it has no caller-selected storage or output root. Do not advertise it as a portable upgrader. |
| catalog/filedataid-catalog.csv | No file exists yet; generate only from individually approved public identifiers. | **Conditional.** Keep the catalog small and limited to the numeric FileDataID and a short human-written role label. Add the exact ID list and file hash to the release manifest before staging. |

The internal audit at analysis/distribution-audit.md is review evidence and is not a package entry.

## Explicit exclusions

Do not stage:

- AzerothSurvivors.exe, any other game or launcher executable, or any redistributed runtime installer.
- Native game assets, original or modified MPQ files, extracted or converted BLP, M2, SKIN, CASC data, CASC indexes, or generated asset closures.
- Client listfiles or listfile archives; raw asset maps; raw .build.info or Build/CDN Config manifests; root, encoding, or CDN key manifests; KeyRing material; or client inventories.
- User-path reports, local logs, test fixtures, private cache contents, or files from analysis/casc-reader-runtime/extracted.
- CascLib source archives, full source trees, source manifests, build trees, import libraries, object files, PDBs, logs, or build fixtures.
- Unreviewed binaries such as tools/mpq-inspect.exe.
- Developer-only scripts and tools such as build_runner.py, pack-mpq.py, compare-build-strings.py, and stage-official-update.ps1.
- native-shader-preview/patch_liquid_shader.py by default. If a later package includes it, carry the retained MIT and Apache-2.0 license files, preserve their exact text, and review the complete dependency/source set first.

The public build and content identifiers embedded in reader code are not account credentials. This exclusion covers raw client manifests and KeyRing data because they are outside the approved package contents, not because every identifier is secret.

## Release gate

Before producing an archive, add a per-file manifest containing package path, repository source, byte length, SHA-256, and approval state. Confirm that every binary has a matching notice, that no excluded client content is present, and that the reader's supported build/path behavior matches the user-facing README. Stop if any file is unlisted or its hash changes. The current state has not passed this gate.

