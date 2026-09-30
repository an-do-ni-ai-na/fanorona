// Fanorona engine - géométrie du plateau et bitboards
#pragma once

#include <string>

#include "types.h"

namespace fanorona {

// Géométrie du plateau de la variante courante (fixée par Board::set, avant toute recherche).
// Fanoron-Tsivy : 9 x 5 (défaut) ; Fanoron-Dimy : 5 x 5, logé dans les colonnes a..e de l'encodage 9 x 5.
struct Board {
    static inline int files = FILE_NB;
    static inline int ranks = RANK_NB;
    static inline Bitboard mask = ALL_SQUARES;  // cases qui existent
    static void set(int files, int ranks);     // recalcule Neighbor et les masques de décalage
    static bool contains(int x, int y) { return x >= 0 && x < files && y >= 0 && y < ranks; }
};

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

// ShiftFrom[d] : cases qui ont un voisin dans la direction d (ligne existante, arrivée sur le plateau).
// Calculé depuis Neighbor : un seul ET avant le décalage, quel que soit le plateau.
extern Bitboard ShiftFrom[DIR_NB];

// Décalage d'un bitboard d'un pas dans la direction d, en respectant les
// lignes du plateau (les diagonales ne partent que des points forts).
inline Bitboard shift(Bitboard b, int d) {
    switch (d) {
    case EAST:       return (b & ShiftFrom[EAST]) << 1;
    case WEST:       return (b & ShiftFrom[WEST]) >> 1;
    case NORTH:      return (b & ShiftFrom[NORTH]) << 9;
    case SOUTH:      return (b & ShiftFrom[SOUTH]) >> 9;
    case NORTH_EAST: return (b & ShiftFrom[NORTH_EAST]) << 10;
    case NORTH_WEST: return (b & ShiftFrom[NORTH_WEST]) << 8;
    case SOUTH_EAST: return (b & ShiftFrom[SOUTH_EAST]) >> 8;
    case SOUTH_WEST: return (b & ShiftFrom[SOUTH_WEST]) >> 10;
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
