# One-click launcher spec

* **Play entry stays `Play Azeroth Survivors.cmd`** in the main game folder. The installer never edits it, replaces the
  EXE start or runs at game launch.
* **What is installed** is one command away and read-only:

  ```
  python as_installer.py status --game-root "<main game folder>"
  ```

  Output: one line per recipe, `installed` / `partial` / `absent` / `unavailable`, plus installer-managed files and
  transactions. State is read from the files themselves, so it is correct for hand-made and installer-made installs.
* **Optional launcher button (not built)**: a `Check mods.cmd` that runs the line above and pauses. It is a separate
  file next to the launcher; the launcher itself is untouched.
* **Install and undo** are explicit, separate steps run by the owner: `integrate ... --apply --yes`, then, if needed,
  `rollback --yes`. Neither runs while `AzerothSurvivors.exe` is open.
