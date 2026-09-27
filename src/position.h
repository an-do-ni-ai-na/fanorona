// Fanorona engine - représentation de la position
#pragma once

#include <string>

#include "types.h"

namespace fanorona {

namespace Zobrist {
extern Key psq[COLOR_NB][SQUARE_NB];
extern Key side;
void init();
}  // namespace Zobrist

// Paramètres de règles (modifiables par "setoption").
struct Rules {
    // Si vrai, une séquence de captures doit être poursuivie tant que possible.
    // Par défaut (règle usuelle) le joueur peut s'arrêter quand il veut.
    static inline bool mandatoryContinuation = false;
    // Nulle après ce nombre de demi-coups consécutifs sans capture.
    static inline int noCaptureLimit = 100;
};

constexpr const char* START_FEN = "BBBBBBBBB/BBBBBBBBB/BWBW1BWBW/WWWWWWWWW/WWWWWWWWW w 0 1";

// La position tient dans 32 octets : on utilise la stratégie "copy-make".
struct Position {
    Bitboard byColor[COLOR_NB];
    Color sideToMove;
    Key key;
    int rule50;   // demi-coups depuis la dernière capture
    int gamePly;

    bool set(const std::string& fen);
    std::string fen() const;
    std::string pretty() const;

    Bitboard pieces(Color c) const { return byColor[c]; }
    Bitboard empty() const { return ~(byColor[WHITE] | byColor[BLACK]) & ALL_SQUARES; }
    Key compute_key() const;

    void do_move(Move m);
    void do_null_move();
};

}  // namespace fanorona
