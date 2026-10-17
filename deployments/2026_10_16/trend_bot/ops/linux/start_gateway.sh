#!/bin/bash
export DISPLAY=:99
exec /home/varmak/ibc/scripts/ibcstart.sh 1045 -g   --tws-path=/home/varmak/Jts --tws-settings-path=/home/varmak/Jts   --ibc-path=/home/varmak/ibc --ibc-ini=/home/varmak/ibc/config.ini   --mode=paper --on2fatimeout=exit
