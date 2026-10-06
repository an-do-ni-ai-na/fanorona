#include "book.h"

#include <fstream>
#include <random>
#include <sstream>
#include <unordered_map>
#include <vector>

#include "movegen.h"

namespace fanorona::Book {

namespace {

struct Entry {
    std::vector<Move> moves;
    std::vector<int> weights;
};

std::unordered_map<Key, Entry> table;
std::mt19937_64 rng{std::random_device{}()};

bool rules_ok() {
    return Rules::variant == Variant::Tsivy && Rules::vela == COLOR_NB && !Rules::mandatoryContinuation;
}

}  // namespace

size_t load(const std::string& path) {
    table.clear();
    if (!rules_ok()) return 0;
    std::ifstream in(path);
    if (!in) return 0;
    std::string line;
    while (std::getline(in, line)) {
        if (line.empty() || line[0] == '#') continue;
        auto tab = line.find('\t');
        if (tab == std::string::npos) continue;
        Position pos;
        pos.set(start_fen());
        std::istringstream seq(line.substr(0, tab)), mv(line.substr(tab + 1));
        std::string tok;
        bool ok = true;
        while (ok && seq >> tok) {
            if (tok == "-") break;
            Move m = parse_move(pos, tok);
            if (m == MOVE_NONE) ok = false;
            else pos.do_move(m);
        }
        if (!ok) continue;
        Entry e;
        int w;
        while (mv >> tok >> w) {
            Move m = parse_move(pos, tok);
            if (m != MOVE_NONE && w > 0) e.moves.push_back(m), e.weights.push_back(w);
        }
        if (!e.moves.empty()) table[pos.key] = std::move(e);
    }
    return table.size();
}

size_t size() { return table.size(); }

Move probe(const Position& pos) {
    if (table.empty() || !rules_ok()) return MOVE_NONE;
    auto it = table.find(pos.key);
    if (it == table.end()) return MOVE_NONE;
    const Entry& e = it->second;
    std::discrete_distribution<size_t> d(e.weights.begin(), e.weights.end());
    return e.moves[d(rng)];
}

}  // namespace fanorona::Book
