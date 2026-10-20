"""Regenerates recipes/retail-mace-kultiras-silver.json from the audited catalog + verified hashes (maintainer tool)."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent
cat = json.loads((HERE.parent / "local-asset-builder" / "retail-mace-silver.json").read_text("utf-8"))
M2 = r"Item\ObjectComponents\Weapon\KultirasQuestB01_Silver_Static.m2"
BLP = r"Item\ObjectComponents\Weapon\KultirasQuestB01_Silver_Static.blp"
DBC = r"DBFilesClient\ItemDisplayInfo.dbc"
recipe = {
  "schema": "as-recipe/1", "id": "retail-mace-kultiras-silver", "version": "1.0.0",
  "title": "Retail silver Kul Tiras mace on starter display 5194",
  "description": "Exports one Retail 12.1.5.70448 mace (4 files) from your own WoW install, converts it to the game's MD20 v256 rigid model, "
                 "and points ItemDisplayInfo row 5194 (the shared native starter mace) at it. Cosmetic only; no item stats change.",
  "status": "ready", "default": True,
  "requires": {"game": {"exe_sha256": ["6382e9ae654295293b82d48fb07a1aa999132775bda10c6c6f80d973d2c1ddfb",
                                        "524e83499dd8f216fadddf75c57346d03dcecb0b456b1f07f87eb6b109c35a1b"],
                        "versions": ["0.103.0"]},
               "tools": ["casc-export", "rigid-converter", "mpq-packer"], "disk_mb": 64},
  "sources": {
    "retail": {"kind": "casc", "product": cat["product"], "version": cat["version"], "build_number": cat["build_number"],
               "build_key": cat["build_key"], "cdn_key": cat["cdn_key"], "locale": "enUS",
               "build_config": cat["build_config"], "cdn_config": cat["cdn_config"],
               "files": [{"file_data_id": a["file_data_id"], "name": a["output"], "role": a["role"], "bytes": a["bytes"], "sha256": a["sha256"]} for a in cat["assets"]],
               "note": cat["variant"]},
    "game-dbc": {"kind": "game-chain", "exclude_slots": ["patch-0.MPQ", "patch-1.MPQ"],
                 "files": [{"path": DBC, "sha256": ["6f37ca862dc632a11aaf86e0ba7b183aa7abaebb052549e6120aa9fa69dd9dee"], "role": "official ItemDisplayInfo.dbc (v0.103.0 chain)"}]}
  },
  "steps": [
    {"id": "export", "op": "casc-export", "source": "retail"},
    {"id": "convert", "op": "run-tool", "tool": "rigid-converter", "inputs": {"src": "export"},
     "expect_outputs": [{"path": "payload/" + M2.replace("\\", "/"), "sha256": "b69b55d092912005907d9190a3616160d0b2e4cf845d8eb8860354eefab8004d"},
                        {"path": "payload/" + BLP.replace("\\", "/"), "sha256": "9c2f5b24dfe99d8ee7bc0233c825e0ae4f32abed04ca09ee9865b46970b0462b"}]},
    {"id": "base", "op": "game-extract", "source": "game-dbc"},
    {"id": "dbc", "op": "dbc-edit", "input": "base:" + DBC.replace("\\", "/"), "output": DBC.replace("\\", "/"),
     "expect_input_sha256": "6f37ca862dc632a11aaf86e0ba7b183aa7abaebb052549e6120aa9fa69dd9dee",
     "edits": [{"row_id": 5194, "column": 1, "type": "string", "expect_before": "Mace_1H_Flanged_A_01.mdx", "value": "KultirasQuestB01_Silver_Static.mdx"}],
     "expect_output_sha256": sys.argv[1]}
  ],
  "outputs": {"mpq_files": [
    {"slot": "patch-0.MPQ", "path": DBC, "from": "dbc:" + DBC.replace("\\", "/"), "sha256": sys.argv[1]},
    {"slot": "patch-0.MPQ", "path": M2, "from": "convert:payload/" + M2.replace("\\", "/"), "sha256": "b69b55d092912005907d9190a3616160d0b2e4cf845d8eb8860354eefab8004d"},
    {"slot": "patch-0.MPQ", "path": BLP, "from": "convert:payload/" + BLP.replace("\\", "/"), "sha256": "9c2f5b24dfe99d8ee7bc0233c825e0ae4f32abed04ca09ee9865b46970b0462b"}]},
  "acceptance": [
    {"check": "dbc-cell", "slot": "patch-0.MPQ", "path": DBC, "row_id": 5194, "column": 1, "type": "string", "equals": "KultirasQuestB01_Silver_Static.mdx"},
    {"check": "mpq-has", "slot": "patch-0.MPQ", "path": M2, "sha256": "b69b55d092912005907d9190a3616160d0b2e4cf845d8eb8860354eefab8004d"}],
  "provenance": "Retail 12.1.5.70448 FileDataIDs 1693135 (model), 1693139 (skin), 144333 and 1733940 (textures); pinned rigid converter; evidence analysis/retail-item-equip, analysis/local-recipe-verified.",
  "limits": ["Display 5194 is shared by native starter appearances (Shaman/Priest and others); the cosmetic changes all of them.",
             "Item-ID mapping to the Retail item is not reproduced; only the native display row is changed.",
             "Static rigid model: no animation tracks beyond the neutral sequence."]
}
out = HERE / "recipes" / "retail-mace-kultiras-silver.json"
out.write_text(json.dumps(recipe, indent=2) + "\n", encoding="utf-8")
print("wrote", out)
