// Fanorona engine - génération des coups
#pragma once

#include <string>
#include <vector>

#include "position.h"

namespace fanorona {

struct MoveList {
    Move moves[MAX_MOVES];
    int size = 0;

    const Move* begin() const { return moves; }
    const Move* end() const { return moves + size; }
    bool contains(Move m) const {
        for (int i = 0; i < size; ++i)
            if (moves[i] == m) return true;
        return false;
    }
};

// Génère tous les coups légaux (tours complets, dédupliqués).
// Les captures sont obligatoires : si une capture existe, seuls les coups
// capturants sont générés, sinon les déplacements simples (paika).
void generate_moves(const Position& pos, MoveList& list);

inline bool has_capture(const Position& pos);

// Coup avec son chemin détaillé, pour l'affichage et l'analyse des saisies.
struct DetailedMove {
    Move move;
    std::string notation;  // ex. "d3e3A", "c2d3Wd4A", "e2e3"
};
std::vector<DetailedMove> generate_detailed(const Position& pos);

std::string move_to_string(const Position& pos, Move m);
Move parse_move(const Position& pos, const std::string& str);

uint64_t perft(const Position& pos, int depth);

}  // namespace fanorona

#include "bitboard.h"

namespace fanorona {
inline bool has_capture(const Position& pos) {
    return capturers(pos.pieces(pos.sideToMove), pos.pieces(~pos.sideToMove)) != 0;
}
}  // namespace fanorona
