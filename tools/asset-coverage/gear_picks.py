#!/usr/bin/env python3
"""Curated retail-model picks for native weapon/shield ItemDisplayInfo rows (R1 starters, R3 drop pool).

Display ids come from the native client: R1 = CharStartOutfit starter displays; R3 = the class weapon pool embedded in
AzerothSurvivors.exe (11 records x 5 tiers at file offsets 112831080..112833752, see analysis/asset-coverage/work/exe-weapon-pool.json).
Models were chosen by eye from analysis/asset-coverage/work/sheets/*.png (gear_preview.py contact sheets): plain looks for low
tiers, ornate for high tiers, legendaries by name.  Writes picks-r1.json / picks-r3.json into the output dir.
"""
import json
import sys
from pathlib import Path

# (id, kind, retail model fdid, output stem, native displays, note)
R1 = [
    ("stave-northrend-b03", "Weapon", 147611, "Stave2H_Northrend_B_03_Static", [472, 2778], "starter staff; casters tier 0"),
    ("sword1h-northrend-d02", "Weapon", 148059, "Sword1H_Northrend_D_02_Static", [1542], "starter short sword"),
    ("dagger-cataclysm-c03", "Weapon", 460835, "Knife1H_Cataclysm_C_03_Static", [6442, 1845], "starter dagger; rogue tier 0"),
    ("rifle-draenorcrafted", "Weapon", 960974, "Rifle2H_DraenorCrafted_D_01_Static", [6606], "starter rifle"),
    ("maul-newplayer-a01", "Weapon", 3736685, "Mace2H_NewPlayer_A_01_Static", [8690, 1243], "starter maul; paladin tier 0"),
    ("hatchet-draenorogre-b01", "Weapon", 926326, "Axe1H_DraenorOgre_B_01_Static", [14029], "starter hatchet"),
    ("stave-cataclysm-c02", "Weapon", 454348, "Stave2H_Cataclysm_C_02_Static", [18530], "druid/mage starter staff"),
    ("shield-northrend-c03", "Shield", 142970, "Shield_Northrend_C_03_Static", [18730, 1684], "starter shield; shaman shield tier 0"),
    ("axe2h-panprog-b01", "Weapon", 588993, "Axe2H_PanProg_B_01_Static", [22291], "starter battle axe"),
]

R3 = [
    ("sword2h-valhallas-c01", "Weapon", 1447939, "Sword2H_Valhallas_C_01_Static", [563], "warrior tier 0"),
    ("sword2h-pvpalliance-a01", "Weapon", 148485, "Sword2H_PvPAlliance_A_01_Static", [1639], "warrior tier 1"),
    ("sword2h-raid-d03", "Weapon", 148494, "Sword2H_Raid_D_03_Static", [22905], "warrior tier 2"),
    ("sword2h-blackwing-a01", "Weapon", 148250, "Sword2H_BlackWing_A_01_Static", [33881], "warrior tier 3"),
    ("mace2h-sulfuras-d01", "Weapon", 515208, "Mace2H_Sulfuras_D_01_Static", [29698], "warrior legendary (effects dropped)"),
    ("maul-draenorogre-b02", "Weapon", 926334, "Mace2H_DraenorOgre_B_02_Static", [2056], "paladin tier 1"),
    ("maul-draenei-b01", "Weapon", 917614, "Mace2H_Draenei_B_01_Static", [25362], "paladin tier 2"),
    ("maul-paladin-d01", "Weapon", 146880, "Mace2H_Paladin_D_01_Static", [34109], "paladin tier 3"),
    ("sword2h-ahnqiraj-d01", "Weapon", 148228, "Sword2H_AhnQiraj_D_01_Static", [23875], "paladin legendary slot (artifact Ashbringer has animated glow bones)"),
    ("rifle-outlandraid-d03", "Weapon", 145451, "Rifle2H_OutlandRaid_D_03_Static", [33826], "hunter tier 3 rifle"),
    ("dagger-rogue-pvp-c01", "Weapon", 238704, "Knife1H_Rogue_PvP_C_01_Static", [2707], "rogue tier 1"),
    ("dagger-maldraxxus-d01", "Weapon", 3159965, "Knife1H_Maldraxxus_D_01_Static", [3512], "rogue tier 2"),
    ("dagger-pandariaquest-b03", "Weapon", 655174, "Knife1H_PandariaQuest_B_03_Static", [31850], "rogue tier 3"),
    ("sword1h-cataclysm-c05", "Weapon", 458187, "Sword1H_Cataclysm_C_05_Static", [30606], "rogue legendary slot (artifact Thunderfury model is not in this Retail build)"),
    ("dagger-draenorhonors-c02", "Weapon", 1068827, "Knife1H_DraenorHonors2_C_02_Static", [35712], "rogue off-hand legendary slot"),
    ("stave-pvp320-c01", "Weapon", 306740, "Stave2H_PvP320_C_01_Static", [2943], "casters tier 1"),
    ("stave-naga-d01", "Weapon", 147585, "Stave2H_Naga_D_01_Static", [5554], "casters tier 2"),
    ("stave-hellfire-c01", "Weapon", 147492, "Stave2H_Hellfire_C_01_Static", [31347], "priest tier 3 (EpicPriest01 has animated scale bone)"),
    ("stave-dragonquest-b01", "Weapon", 4039118, "Staff2H_DragonQuest_B_01_Static", [31346], "priest tier 4 (EpicPriest02 has animated scale bone)"),
    ("stave-future-c01", "Weapon", 531598, "Stave2H_430Future_C_01_Static", [34250], "mage/druid tier 3"),
    ("stave-darkwatcher-c01", "Weapon", 1661446, "Staff2H_DarkWatcherNPC_C_01_Static", [35632, 35631], "mage/warlock tier 4 (Medivh staff has animated scale bone)"),
    ("stave-naga-a01", "Weapon", 2734020, "Staff2H_Naga_A_01_Static", [31960], "warlock tier 3"),
    ("staff-pandarenmonk-c01", "Weapon", 839612, "Staff2H_PandarenMonk_C_01_Static", [35633], "druid tier 4"),
    ("hammer-drogbar-b01", "Weapon", 1308166, "Mace1H_Drogbar_B_01_Static", [1839], "shaman tier 0"),
    ("hammer-newplayer-a01", "Weapon", 3736684, "Mace1H_NewPlayer_A_01_Static", [1784], "shaman tier 1"),
    ("mace-northrend-c03", "Weapon", 146598, "Mace1H_Northrend_C_03_Static", [35250], "shaman tier 2"),
    ("mace-pvp320-c02", "Weapon", 306141, "Mace1H_PvP320_C_02_Static", [31861], "shaman tier 3"),
    ("mace-doomhammer-d02", "Weapon", 953089, "Mace1H_Doomhammer_D_02_Static", [35574], "shaman legendary"),
    ("shield-councilsentinel", "Shield", 1568518, "Shield1H_CouncilSentinel_C_01_Static", [1705], "shaman shield tier 1"),
    ("shield-cataclysm-c01", "Shield", 463101, "Shield_Cataclysm_C_01_Static", [23835], "shaman shield tier 2"),
    ("shield-pvpcataclysm-c02", "Shield", 528725, "Shield_PvPCataclysmS3_C_02_Static", [32558], "shaman shield tier 3"),
    ("shield-pvpdraenors2-d01", "Shield", 1068281, "Shield1H_PvPDraenorS2_D_01_Static", [35573], "shaman shield tier 4"),
]


def dump(batch, rows, out):
    items = [{"id": i, "kind": k, "fdid": f, "name": n, "displays": d, "note": note} for i, k, f, n, d, note in rows]
    Path(out).write_text(json.dumps({"batch": batch, "items": items}, indent=1), encoding="utf8")


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    dump("R1", R1, out / "picks-r1.json")
    dump("R3", R3, out / "picks-r3.json")
    print("wrote", out)
