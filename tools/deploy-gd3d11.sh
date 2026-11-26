#!/usr/bin/env bash
# Wdrozenie PACZKI GD3D11 z naszego forka (Savanta/GD3D11) na instancje + dowod, ze dziala.
#
# ZMIERZONE 26.11.2026:
#  * NIE WOLNO podmieniac samego ddraw.dll. kirides/master ma 272 shadery (potok compute:
#    tiled light culling, TAA, DoF, SAO) wobec 92 w grze (era SK9), a renderer kompiluje
#    shadery Z DYSKU GRY - sama binarka daje czarny ekran.
#  * NIE WOLNO kopiowac calej paczki: ma GD3D11/Textures (1383) i GD3D11/Meshes (175), a gra
#    wozi wlasne, kuratorowane przez NB (nieba, modele). Nadpisanie = utrata wygladu moda.
#  * Artefakt "Release" z CI rozpakowuje sie PLASKO i JEST ukladem docelowym, a jego nazwa to
#    GD3D11-git-<krotki sha runu>. Bez -n gh pobiera takze 193 MB debuga i rozklada do podkatalogow.
#  * Sciezka gry ma SPACJE ("GOG Games") - kazde uzycie po stronie instancji MUSI byc w cudzyslowie.
#  * Na instancji nie ma GD3D11/D3D12 (dodatek mastera), wiec lista do kopii zapasowej musi byc
#    budowana dynamicznie - tar na brakujacym czlonku konczy sie bledem i pustą kopią.
#
# Dlatego komendy zdalne ida jako JEDEN SKRYPT po stdin (jak sondy), a nie jako jedna linia
# z zagniezdzonymi cudzyslowami - ten skrypt przewrocil sie na tym dwa razy.
#
# Wdrazamy WYBIORCZO: binarka + jej biblioteki + GD3D11/{Bin, shaders, D3D12}, kopiujac NA
# istniejace drzewo, zeby shadery wlasne NB (PS_Ghost.hlsl, PS_LinDepth.hlsl, SMAA.fx/fxh) zostaly.
#
# Uzycie: deploy-gd3d11.sh [run-id] [ssh-port]
set -euo pipefail
RUN="${1:-}"
PORT="${2:-51950}"
HOST_IP="${ORCH_HOST_IP:-85.19.177.45}"
KEY="$HOME/.ssh/hermes_dom"
SYSTEM="/workspace/Games/Gothic2-NB/prefix/drive_c/GOG Games/Gothic 2 Gold/system"
WORK="$HOME/.hermes/cache/scratch/gd3d11-artifact"
SSH="ssh -i $KEY -p $PORT -o BatchMode=yes -o StrictHostKeyChecking=no root@$HOST_IP"
LIBS="ddraw.dll d3dcompiler_47.dll dxcompiler.dll dxil.dll assimp-vc143-mt.dll GFSDK_SSAO_D3D11.win32.dll"

export SSH_AUTH_SOCK="$(gpgconf --list-dirs agent-ssh-socket)"
if [ -z "$RUN" ]; then
    RUN="$(gh run list --repo Savanta/GD3D11 --limit 20 --json databaseId,conclusion --jq '[.[] | select(.conclusion=="success")][0].databaseId')"
fi
echo "run: $RUN"

rm -rf "$WORK"; mkdir -p "$WORK"
SHA="$(gh run view "$RUN" --repo Savanta/GD3D11 --json headSha --jq '.headSha[0:9]')"
if gh run download "$RUN" --repo Savanta/GD3D11 -n "GD3D11-git-$SHA" -D "$WORK" 2>/dev/null && [ -f "$WORK/ddraw.dll" ]; then
    PKG="$WORK"
else
    echo "   (artefakt nazwany nie zadzialal - biore wszystko, moze byc wolne)"
    gh run download "$RUN" --repo Savanta/GD3D11 -D "$WORK"
    PKG="$(dirname "$(find "$WORK" -name ddraw.dll | head -1)")"
fi
[ -f "$PKG/ddraw.dll" ] || { echo BLAD: brak ddraw.dll; find "$WORK" -maxdepth 2 | head -20; exit 1; }

STAGE="$(mktemp -d)"
for f in $LIBS; do [ -f "$PKG/$f" ] && cp -a "$PKG/$f" "$STAGE/"; done
for f in "$PKG"/ffx_fsr3upscaler_*.dll; do [ -f "$f" ] && cp -a "$f" "$STAGE/"; done
mkdir -p "$STAGE/GD3D11"
for d in Bin shaders D3D12; do [ -d "$PKG/GD3D11/$d" ] && cp -a "$PKG/GD3D11/$d" "$STAGE/GD3D11/"; done
echo "   paczka: ddraw.dll $(stat -c %s "$PKG/ddraw.dll") B | shaders $(find "$PKG/GD3D11/shaders" -type f | wc -l) | Bin $(find "$PKG/GD3D11/Bin" -type f | wc -l) | D3D12 $(find "$PKG/GD3D11/D3D12" -type f 2>/dev/null | wc -l)"
echo "   do wyslania: $(du -sh "$STAGE" | cut -f1), $(find "$STAGE" -type f | wc -l) plikow"

echo "=== 1. wysylam paczke na instancje ==="
tar czf - -C "$STAGE" . | $SSH "rm -rf /tmp/gd3d11-new && mkdir -p /tmp/gd3d11-new && tar xzf - -C /tmp/gd3d11-new && find /tmp/gd3d11-new -type f | wc -l"

STAMP="$(date +%H%M%S)"
# Skrypt zdalny: heredoc NIECYTATOWANY -> $SYSTEM i $STAMP rozwija sie lokalnie,
# a to, co ma zostac zdalne, jest escapowane \$.
cat > /tmp/apply-gd3d11.sh <<REMOTE
set -e
SYSTEM="$SYSTEM"
BK="/workspace/.orch/gd3d11-backup-$STAMP"
mkdir -p "\$BK"
cd "\$SYSTEM"
MEMBERS="ddraw.dll"
for d in GD3D11/Bin GD3D11/shaders GD3D11/D3D12; do [ -e "\$d" ] && MEMBERS="\$MEMBERS \$d"; done
tar czf "\$BK/renderer-before.tgz" \$MEMBERS
echo "2. kopia zapasowa: \$(stat -c %s "\$BK/renderer-before.tgz") B, czlonkowie: \$MEMBERS"
cp -a /tmp/gd3d11-new/. "\$SYSTEM/"
echo "3. skopiowane na system/; shaderow teraz: \$(find "\$SYSTEM/GD3D11/shaders" -type f | wc -l)"
pkill -9 -x Gothic2.exe || true
sleep 3
bash /root/session-up.sh > /tmp/su-new.log 2>&1 || true
sleep 30
echo "4. wersja z logu: \$(grep -a 'GD3D11 Version' "\$SYSTEM/Log.txt" | tail -1)"
rm -f /tmp/chk.png
DISPLAY=:99 timeout 12 ffmpeg -hide_banner -loglevel error -f x11grab -video_size 1920x1080 -i :99 -frames:v 1 -y /tmp/chk.png 2>/dev/null || true
echo "   klatka: \$(stat -c %s /tmp/chk.png 2>/dev/null || echo 0) B (3 MB = swiat, ~13 kB = czarno/okno)"
echo "   proces gry: \$(pgrep -c -x Gothic2.exe || echo 0)"
echo "   bledy w logu renderera:"
grep -aiE 'fail|error|not found|missing|cannot' "\$SYSTEM/Log.txt" | tail -5 | cut -c1-130 || true
echo "   wycofanie: tar xzf \$BK/renderer-before.tgz -C \"\$SYSTEM\""
REMOTE

echo "=== 2-4. kopia zapasowa, kopiowanie, restart i dowod ==="
$SSH bash -s < /tmp/apply-gd3d11.sh
