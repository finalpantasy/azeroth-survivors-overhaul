# Local-client tooling distribution packet

This folder records a distribution audit and a staging policy. **It is not a release package. No final ZIP has been assembled.** The CascLib DLL is a candidate only; the license-provenance items in THIRD_PARTY_NOTICES.txt and analysis/distribution-audit.md still need review before release.

The existing local-installer ZIP is tools/local-installer/local-installer-preflight.zip (SHA-256 B9602F096D44743775FB8FE074D0CF3BD3F7D052F0DD8611693B084E5D68941A). Its three entries are local_installer.py, test_local_installer.py, and README.md. It is a preflight and guarded visual-overlay helper: it can inspect selected client metadata and, when explicitly invoked, apply or roll back an already-reviewed, already-converted overlay. It does not extract CASC files, convert assets, create an overlay, or verify that overlay contents are safe. A clean preflight or dry run does not make it an extraction-ready installer or establish build compatibility.

The candidate reader runtime is pinned to CascLib 3.0 source commit 4971d363e665551ac4142f541e5f2d71f1cda653 and has an x64 Release build record. Its source adapter is still an experimental, profile-specific tool. Current code has pinned Forever and Retail build profiles and defaults storage access to E:\World of Warcraft; it does not accept a caller-selected client root or output directory. Do not present it as a general-purpose client upgrader. Any package that includes the reader must preserve the documented supported profile and path assumptions, or wait for a separately reviewed portability change.

The DLL imports Windows system libraries and the x64 Microsoft Visual C++ 2015–2022 runtime. Users who need that runtime must install the Microsoft-supplied x64 redistributable separately. This package does not bundle a redistributable.

## Staging boundary

Use STAGING_ALLOWLIST.md as the only packaging rule. Stage only individually named, hash-checked documentation, first-party scripts, an explicitly accepted CascLib DLL, and a small sanitized public FileDataID catalog. Do not copy a directory wholesale. Keep extracted assets and client-owned files local to the user.

The asset map in the analysis tree is evidence, not a package input: it includes client paths, a CASC content key, byte counts, content hashes, and listfile-derived name-resolution details. A future catalog may contain only specifically approved numeric FileDataIDs and short, human-written role labels. Public client build/content identifiers are not account credentials, but raw client manifests and resolution evidence are unnecessary for distribution.

This tooling does not include a native game executable, game data, MPQ overlays, extracted BLP/M2/SKIN files, a client listfile, or a KeyRing. It is not a complete converted asset upgrade. The user-owned client remains the source of client files, and asset extraction, dependency closure, conversion, compatibility checks, and visual review remain separate work.

