#include "tt.h"

#include <cstring>

namespace fanorona {

TranspositionTable TT;

void TranspositionTable::resize(size_t mb) {
    size_t n = 1;
    while (n * 2 * sizeof(Bucket) <= mb * 1024 * 1024) n *= 2;
    table.assign(n, Bucket{});
    mask = n - 1;
}

void TranspositionTable::clear() {
    std::memset(static_cast<void*>(table.data()), 0, table.size() * sizeof(Bucket));
    generation = 0;
}

TTEntry* TranspositionTable::probe(Key key, bool& found) {
    Bucket& b = table[key & mask];
    for (auto& e : b.entry)
        if (e.key == key && e.genBound) {
            e.genBound = uint8_t(generation | e.bound());
            found = true;
            return &e;
        }
    found = false;
    // Remplace l'entrée la moins précieuse (ancienne génération ou peu profonde).
    auto worth = [&](const TTEntry& e) {
        int age = ((generation - (e.genBound & 0xFC)) & 0xFC) >> 2;
        return e.depth - 8 * age;
    };
    return worth(b.entry[0]) <= worth(b.entry[1]) ? &b.entry[0] : &b.entry[1];
}

void TranspositionTable::store(TTEntry* e, Key key, Value v, Bound b, int depth, Move m, Value eval) {
    if (m != MOVE_NONE || e->key != key) e->move = m;
    if (b == BOUND_EXACT || e->key != key || depth + 2 > e->depth) {
        e->key = key;
        e->value = int16_t(v);
        e->eval = int16_t(eval);
        e->depth = int8_t(depth);
        e->genBound = uint8_t(generation | b);
    }
}

int TranspositionTable::hashfull() const {
    int cnt = 0;
    for (size_t i = 0; i < 500 && i < table.size(); ++i)
        for (const auto& e : table[i].entry)
            if (e.genBound && (e.genBound & 0xFC) == generation) ++cnt;
    return cnt;
}

}  // namespace fanorona
