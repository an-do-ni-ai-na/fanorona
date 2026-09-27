// Fanorona engine - table de transposition
#pragma once

#include <cstddef>
#include <vector>

#include "types.h"

namespace fanorona {

enum Bound : uint8_t { BOUND_NONE, BOUND_UPPER, BOUND_LOWER, BOUND_EXACT = BOUND_UPPER | BOUND_LOWER };

struct TTEntry {
    Key key;
    Move move;
    int16_t value;
    int16_t eval;
    int8_t depth;
    uint8_t genBound;  // génération (6 bits) | borne (2 bits)

    Bound bound() const { return Bound(genBound & 3); }
};

// Table à seaux de 2 entrées : remplacement privilégiant les entrées
// profondes et récentes, comme dans les moteurs d'échecs classiques.
class TranspositionTable {
   public:
    void resize(size_t mb);
    void clear();
    void new_search() { generation = (generation + 4) & 0xFC; }
    TTEntry* probe(Key key, bool& found);
    void store(TTEntry* e, Key key, Value v, Bound b, int depth, Move m, Value eval);
    int hashfull() const;

   private:
    struct Bucket {
        TTEntry entry[2];
    };
    std::vector<Bucket> table;
    size_t mask = 0;
    uint8_t generation = 0;
};

extern TranspositionTable TT;

}  // namespace fanorona
