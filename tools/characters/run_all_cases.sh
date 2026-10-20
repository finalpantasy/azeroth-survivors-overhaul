#!/bin/bash
cd "tools/visual-inspection"
for c in ../characters/cases/chars-*.json; do
  id=$(basename $c .json)
  [ "$id" = "chars-human-male" ] && continue
  python run_case.py "$c" --lock-wait 1800 2>&1 | tail -8 >> "./analysis/characters/lab/run-all.log"
done
echo ALLDONE >> "./analysis/characters/lab/run-all.log"
