# Project-local CascLib offline reader

This tool builds a local-only CascLib reader from the staged CascLib 3.0 source at commit `4971d363e665551ac4142f541e5f2d71f1cda653`. The source archive is pinned by SHA-256 `527920b8a48febb7da8fa72beee187cbd0a65cd377400b3160298d37fed34413`. The derivative is in `source/` (the 76 MB extracted `listfile/listfile.txt` is not shipped; the pinned `listfile-archive.zip` is).

The derivative contains only the required read-only/shared-open changes for the two metadata probes and an early `ERROR_ACCESS_DENIED` guard against `versions`, `CASC_FEATURE_ONLINE`, and `CASC_FEATURE_FORCE_DOWNLOAD`. `source-manifest.json` records every derived source hash. License and bundled-library notices remain with the copied source.

Build with `.\build.ps1`. Its Python runner verifies every source hash against `source-manifest.json`, captures real configure/build return codes, configures the audited Visual Studio 2022 x64 options, and builds only the Release `casc` target with one BelowNormal process and a 600-second limit. It does not invoke `install` or enable tests/static libraries. The resulting DLL stays under `build-x64/`; a unique build record and stdout/stderr logs are saved in `build-logs/` (gitignored). It finds CMake via `$CMAKE`, a Visual Studio 2022 install, or PATH; the Visual Studio 2022 C++ build tools are required.

`casc_offline.py` is the only runtime adapter. It loads the absolute project-local DLL through a restricted Windows DLL search path and binds only the audited storage/file/info/read/close/error functions. It refuses storage access unless passed `--allow-client-read`, uses the pinned `wowf` build configuration and enUS locale, asserts the build identity and offline feature bit, and writes at most one M2 plus one referenced texture under `analysis/casc-reader-runtime/extracted/`. It has no option for a caller-chosen DLL, storage, or output path.

No client data or CascLib binaries are distributed by this source tree. The extractor never downloads missing files, imports encryption keys, opens arbitrary local files through CascLib, or installs the library globally.

`casc_offline.py` is a developer probe that expects the original workspace layout (`analysis/` inventories); the installer does not need it, only the DLL built by `build.ps1`.
