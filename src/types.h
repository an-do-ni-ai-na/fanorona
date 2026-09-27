// Fanorona engine - types de base
#pragma once

#include <cstdint>

namespace fanorona {

using Bitboard = uint64_t;
using Key = uint64_t;

// Plateau 9 x 5 : colonnes a..i (x = 0..8), rangées 1..5 (y = 0..4).
// Case sq = y * 9 + x  ->  45 cases, tiennent dans un uint64_t.
constexpr int FILE_NB = 9;
constexpr int RANK_NB = 5;
constexpr int SQUARE_NB = 45;
constexpr int SQ_NONE = -1;

constexpr Bitboard ALL_SQUARES = (1ULL << SQUARE_NB) - 1;

enum Color : int { WHITE = 0, BLACK = 1, COLOR_NB = 2 };
constexpr Color operator~(Color c) { return Color(c ^ 1); }

// 8 directions, dans l'ordre trigonométrique. opposite(d) = d ^ 4.
enum Direction : int { EAST, NORTH_EAST, NORTH, NORTH_WEST, WEST, SOUTH_WEST, SOUTH, SOUTH_EAST, DIR_NB };
constexpr int opposite(int d) { return d ^ 4; }
constexpr bool is_diagonal(int d) { return d & 1; }

constexpr int make_square(int x, int y) { return y * FILE_NB + x; }
constexpr int file_of(int sq) { return sq % FILE_NB; }
constexpr int rank_of(int sq) { return sq / FILE_NB; }
constexpr Bitboard square_bb(int sq) { return 1ULL << sq; }

// Un coup = un tour complet (déplacement simple "paika" ou séquence de
// captures), encodé dans 64 bits :
//   bits  0..44 : ensemble des pièces capturées (bitboard)
//   bits 45..50 : case de départ
//   bits 51..56 : case d'arrivée finale
// Deux séquences différentes menant au même (départ, arrivée, captures)
// donnent exactement la même position : elles sont fusionnées.
using Move = uint64_t;
constexpr Move MOVE_NONE = 0;

constexpr Move make_move(int from, int to, Bitboard captured) {
    return captured | (Move(from) << 45) | (Move(to) << 51);
}
constexpr int move_from(Move m) { return int((m >> 45) & 63); }
constexpr int move_to(Move m) { return int((m >> 51) & 63); }
constexpr Bitboard move_captured(Move m) { return m & ALL_SQUARES; }
constexpr bool is_capture(Move m) { return move_captured(m) != 0; }

constexpr int MAX_PLY = 128;
constexpr int MAX_MOVES = 1024;

using Value = int;
constexpr Value VALUE_ZERO = 0;
constexpr Value VALUE_DRAW = 0;
constexpr Value VALUE_MATE = 32000;
constexpr Value VALUE_INFINITE = 32001;
constexpr Value VALUE_NONE = 32002;
constexpr Value VALUE_MATE_IN_MAX_PLY = VALUE_MATE - MAX_PLY;

constexpr Value mate_in(int ply) { return VALUE_MATE - ply; }
constexpr Value mated_in(int ply) { return -VALUE_MATE + ply; }

inline int popcount(Bitboard b) { return __builtin_popcountll(b); }
inline int lsb(Bitboard b) { return __builtin_ctzll(b); }
inline int pop_lsb(Bitboard& b) {
    int s = lsb(b);
    b &= b - 1;
    return s;
}

}  // namespace fanorona
