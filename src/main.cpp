#include <iostream>

#include "bitboard.h"
#include "position.h"
#include "search.h"
#include "tt.h"
#include "uci.h"

using namespace fanorona;

int main(int argc, char* argv[]) {
    std::cout << "Fanorona-Engine 0.1 - tapez 'help' pour l'aide" << std::endl;
    init_bitboards();
    Zobrist::init();
    Search::init();
    TT.resize(64);
    UCI::loop(argc, argv);
    return 0;
}
