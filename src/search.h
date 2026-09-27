// Fanorona engine - recherche alpha-bêta
#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

#include "position.h"

namespace fanorona {

struct SearchLimits {
    int depth = 0;           // 0 = illimité
    int64_t movetime = 0;    // ms
    int64_t time[COLOR_NB] = {0, 0};
    int64_t inc[COLOR_NB] = {0, 0};
    int movestogo = 0;
    uint64_t nodes = 0;
    bool infinite = false;
};

struct SearchResult {
    Move bestMove = MOVE_NONE;
    Value score = VALUE_NONE;
    int depth = 0;
    uint64_t nodes = 0;
    int64_t timeMs = 0;
    std::vector<Move> pv;
};

namespace Search {

extern std::atomic<bool> stopSignal;

void init();
void clear();

// `history` contient les clés des positions de la partie, la dernière étant
// celle de `pos` (sert à détecter les répétitions).
// Si `verbose`, affiche des lignes "info ..." façon UCI.
SearchResult think(const Position& pos, const std::vector<Key>& history, const SearchLimits& limits, bool verbose);

}  // namespace Search

}  // namespace fanorona
