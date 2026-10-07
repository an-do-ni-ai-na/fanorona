#include "book.h"

#include <fstream>
#include <random>
#include <sstream>
#include <unordered_map>
#include <vector>

#include "movegen.h"

namespace fanorona::Book {

namespace {

std::unordered_map<std::string, std::string> table;  // suite de coups -> "<coup> <poids> ..."
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
        std::string seq = line.substr(0, tab);
        table[seq == "-" ? std::string() : seq] = line.substr(tab + 1);
    }
    return table.size();
}

size_t size() { return table.size(); }

Move probe(const Position& pos, const std::string& line) {
    if (table.empty() || !rules_ok()) return MOVE_NONE;
    auto it = table.find(line);
    if (it == table.end()) return MOVE_NONE;
    std::istringstream is(it->second);
    std::vector<Move> moves;
    std::vector<int> weights;
    std::string tok;
    int w;
    while (is >> tok >> w) {
        Move m = parse_move(pos, tok);
        if (m != MOVE_NONE && w > 0) moves.push_back(m), weights.push_back(w);
    }
    if (moves.empty()) return MOVE_NONE;
    std::discrete_distribution<size_t> d(weights.begin(), weights.end());
    return moves[d(rng)];
}

}  // namespace fanorona::Book
