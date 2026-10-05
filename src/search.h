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
    int multiPV = 1;         // nombre de meilleures lignes cherchées (analyse) ; 1 = recherche normale
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

// Paramètres de recherche réglables (options UCI, pour le réglage SPSA : tools/spsa.py). Les valeurs par défaut
// sont celles du code d'origine : signature bench inchangée tant qu'on n'y touche pas.
struct TuneParam {
    const char* name;
    int* value;
    int def, min, max;
};
const std::vector<TuneParam>& tune_params();
bool set_tune_param(const std::string& name, int value);  // false si inconnu

// `history` contient les clés des positions de la partie, la dernière étant
// celle de `pos` (sert à détecter les répétitions).
// Si `verbose`, affiche des lignes "info ..." façon UCI.
SearchResult think(const Position& pos, const std::vector<Key>& history, const SearchLimits& limits, bool verbose);

}  // namespace Search

}  // namespace fanorona
