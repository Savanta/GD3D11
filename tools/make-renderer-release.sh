#!/usr/bin/env bash
# Publish a CI build of the fork as the flat tarball the instance rebuild pins.
#
# Why this exists: the Nightly release carries zips (GD3D11-git-<sha>.zip + a -debug.zip that is
# 193 MB), while rebuild-instance.sh pins a flat tar.gz (libraries at the top level, GD3D11/{Bin,
# shaders,D3D12} below them) plus its own .sha256. Doing that conversion by hand once per build is
# how the instance ends up with a renderer nobody pinned.
#
# Uzycie: make-renderer-release.sh <run-id> [tag]        (tag domyslnie renderer-<data>)
set -euo pipefail
REPO=Savanta/GD3D11
RUN="${1:?podaj id przebiegu CI}"
TAG="${2:-renderer-$(date +%Y.%m.%d)}"
WORK="${WORK:-$HOME/.hermes/cache/scratch/gd3d11-release}"
LIBS="ddraw.dll d3dcompiler_47.dll dxcompiler.dll dxil.dll assimp-vc143-mt.dll GFSDK_SSAO_D3D11.win32.dll"

export SSH_AUTH_SOCK="$(gpgconf --list-dirs agent-ssh-socket)" 2>/dev/null || true
SHA="$(gh run view "$RUN" --repo "$REPO" --json headSha --jq '.headSha[0:9]')"
echo "run $RUN, commit $SHA, tag $TAG"

rm -rf "$WORK"; mkdir -p "$WORK/raw"
if ! gh run download "$RUN" --repo "$REPO" -n "GD3D11-git-$SHA" -D "$WORK/raw" 2>/dev/null; then
    echo "  artefakt nazwany nie zadzialal - biore wszystko (moze byc wolne)" >&2
    gh run download "$RUN" --repo "$REPO" -D "$WORK/raw"
fi
PKG="$(dirname "$(find "$WORK/raw" -name ddraw.dll | head -1)")"
[ -f "$PKG/ddraw.dll" ] || { echo "BLAD: brak ddraw.dll w artefakcie" >&2; exit 1; }

# Uklad plaski, dokladnie taki, jaki rozpakowuje rebuild-instance.sh do system/.
S="$WORK/stage"; mkdir -p "$S/GD3D11"
for f in $LIBS; do [ -f "$PKG/$f" ] && cp -a "$PKG/$f" "$S/"; done
for f in "$PKG"/ffx_fsr3upscaler_*.dll; do [ -f "$f" ] && cp -a "$f" "$S/"; done
for d in Bin shaders D3D12; do [ -d "$PKG/GD3D11/$d" ] && cp -a "$PKG/GD3D11/$d" "$S/GD3D11/"; done
echo "  stage: $(find "$S" -type f | wc -l) plikow, shaderow $(find "$S/GD3D11/shaders" -type f | wc -l), ddraw.dll $(stat -c %s "$S/ddraw.dll") B"

NAME="gd3d11-renderer-git-$SHA.tar.gz"
tar czf "$WORK/$NAME" -C "$S" .
( cd "$WORK" && sha256sum "$NAME" > "$NAME.sha256" )
echo "  $NAME: $(stat -c %s "$WORK/$NAME") B"
echo "  sha256: $(cut -d' ' -f1 "$WORK/$NAME.sha256")"

if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    gh release upload "$TAG" --repo "$REPO" --clobber "$WORK/$NAME" "$WORK/$NAME.sha256"
else
    gh release create "$TAG" --repo "$REPO" --title "Renderer $TAG (git-$SHA)" \
        --notes "Flat package for rebuild-instance.sh: libraries at top level, GD3D11/{Bin,shaders,D3D12} below. Built by CI run $RUN." \
        "$WORK/$NAME" "$WORK/$NAME.sha256"
fi
echo "  https://github.com/$REPO/releases/download/$TAG/$NAME"
