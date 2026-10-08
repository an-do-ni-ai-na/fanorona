// Point d'entrée WebAssembly (tools/wasm/build.sh) : le moteur dans le navigateur, pour jouer hors ligne.
// fanorona_init() puis fanorona_cmd("<commande>") (même protocole que la ligne de commande, synchrone).
#include <string>

#include "bitboard.h"
#include "position.h"
#include "search.h"
#include "tt.h"
#include "uci.h"

using namespace fanorona;

extern "C" {

void fanorona_init(int hash_mb) {
    init_bitboards();
    Zobrist::init();
    Search::init();
    TT.resize(hash_mb > 0 ? hash_mb : 16);
}

const char* fanorona_cmd(const char* cmd) {
    static std::string out;
    out = UCI::execute(cmd);
    return out.c_str();
}

}
