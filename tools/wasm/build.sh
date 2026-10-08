#!/bin/bash
# Moteur en WebAssembly pour le jeu hors ligne de l'interface (tools/gui/engine/) : Emscripten dans Docker.
#   tools/wasm/build.sh            (depuis la racine du dépôt ; Docker requis, image emscripten/emsdk)
# Deux binaires, même colle JavaScript : fanorona-simd.wasm (WebAssembly SIMD, le code AVX2 du réseau traduit
# par Emscripten, ~4x plus rapide) et fanorona.wasm (sans SIMD, anciens navigateurs). Pile de 16 Mo : la recherche
# pose de gros tableaux sur la pile (la pile par défaut de 64 Ko fait planter l'évaluation classique).
set -e
cd "$(dirname "$0")/../.."
OUT=tools/gui/engine
SRC="src/bitboard.cpp src/position.cpp src/movegen.cpp src/evaluate.cpp src/nnue.cpp src/tt.cpp src/book.cpp src/search.cpp src/telo.cpp src/uci.cpp src/wasm.cpp"
COMMON="-O3 -DNDEBUG -std=c++17 -fexceptions -sMODULARIZE=1 -sEXPORT_NAME=FanoronaEngine -sEXPORTED_FUNCTIONS=_fanorona_init,_fanorona_cmd -sEXPORTED_RUNTIME_METHODS=cwrap,FS -sALLOW_MEMORY_GROWTH=1 -sINITIAL_MEMORY=67108864 -sSTACK_SIZE=16777216 -sENVIRONMENT=worker,node -sFORCE_FILESYSTEM=1"
TMP=$(mktemp -d)
trap 'docker run --rm -v "$TMP:/out" alpine rm -rf /out/plain /out/simd >/dev/null 2>&1; rm -rf "$TMP"' EXIT
run() { docker run --rm -v "$PWD:/src" -v "$TMP:/out" -w /src emscripten/emsdk:latest em++ $COMMON $SRC "$@"; }
mkdir -p "$TMP/plain" "$TMP/simd"
run -o /out/plain/fanorona.js
run -msimd128 -mavx2 -mfma -o /out/simd/fanorona.js
mkdir -p "$OUT"
cp "$TMP/plain/fanorona.js" "$OUT/fanorona.js"
cp "$TMP/plain/fanorona.wasm" "$OUT/fanorona.wasm"
cp "$TMP/simd/fanorona.wasm" "$OUT/fanorona-simd.wasm"
cmp -s "$TMP/plain/fanorona.js" "$TMP/simd/fanorona.js" || echo "attention : colles JavaScript différentes (SIMD / sans)"
ls -la "$OUT"
