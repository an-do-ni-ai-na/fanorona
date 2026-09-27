// Fanorona engine - géométrie du plateau et bitboards
#pragma once

#include <string>

#include "types.h"

namespace fanorona {

// Colonnes utiles pour éviter les débordements lors des décalages.
constexpr Bitboard FILE_A_BB = 0x1ULL | 0x1ULL << 9 | 0x1ULL << 18 | 0x1ULL << 27 | 0x1ULL << 36;
constexpr Bitboard FILE_I_BB = FILE_A_BB << 8;

// Points "forts" : (x + y) pair. Seuls eux sont reliés en diagonale.
constexpr Bitboard make_strong() {
    Bitboard b = 0;
    for (int s = 0; s < SQUARE_NB; ++s)
        if ((file_of(s) + rank_of(s)) % 2 == 0) b |= square_bb(s);
    return b;
}
constexpr Bitboard STRONG_BB = make_strong();

// Voisin de `sq` dans la direction d (SQ_NONE si hors plateau / pas de ligne).
extern int Neighbor[SQUARE_NB][DIR_NB];

// Décalage d'un bitboard d'un pas dans la direction d, en respectant les
// lignes du plateau (les diagonales ne partent que des points forts).
inline Bitboard shift(Bitboard b, int d) {
    switch (d) {
    case EAST:       return (b & ~FILE_I_BB) << 1;
    case WEST:       return (b & ~FILE_A_BB) >> 1;
    case NORTH:      return (b << 9) & ALL_SQUARES;
    case SOUTH:      return b >> 9;
    case NORTH_EAST: return ((b & STRONG_BB & ~FILE_I_BB) << 10) & ALL_SQUARES;
    case NORTH_WEST: return ((b & STRONG_BB & ~FILE_A_BB) << 8) & ALL_SQUARES;
    case SOUTH_EAST: return (b & STRONG_BB & ~FILE_I_BB) >> 8;
    case SOUTH_WEST: return (b & STRONG_BB & ~FILE_A_BB) >> 10;
    default:         return 0;
    }
}

// Ensemble des pièces de `them` alignées de façon contiguë à partir de `start`
// dans la direction d (pièces capturées par approche ou retrait).
inline Bitboard capture_line(Bitboard them, int start, int d) {
    Bitboard r = 0;
    for (int s = start; s != SQ_NONE && (them & square_bb(s)); s = Neighbor[s][d])
        r |= square_bb(s);
    return r;
}

// Pièces de `us` ayant au moins une capture (approche ou retrait) disponible.
Bitboard capturers(Bitboard us, Bitboard them);

std::string square_to_string(int sq);
int string_to_square(const std::string& s);

void init_bitboards();

}  // namespace fanorona
