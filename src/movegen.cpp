#include "movegen.h"

#include <algorithm>
#include <cctype>

#include "bitboard.h"

namespace fanorona {

namespace {

constexpr int MAX_STEPS = 32;

struct Path {
    int from;
    int n = 0;
    int to[MAX_STEPS];
    bool withdrawal[MAX_STEPS];
};

// Exploration en profondeur des séquences de captures d'une pièce.
// Contraintes : pas deux fois de suite la même direction, pas de retour sur
// une case déjà visitée pendant le tour. emit(to, captured, path) est appelé
// pour chaque séquence légale.
template <typename Emit>
void capture_dfs(Bitboard us, Bitboard them, int cur, Bitboard visited, int lastDir, Bitboard acc, Path& path,
                 Emit& emit) {
    Bitboard empty = ~(us | them | visited) & ALL_SQUARES;
    bool continued = false;

    for (int d = 0; d < DIR_NB; ++d) {
        if (d == lastDir) continue;
        int t = Neighbor[cur][d];
        if (t == SQ_NONE || !(empty & square_bb(t))) continue;

        for (int kind = 0; kind < 2; ++kind) {
            // kind 0 = approche (pièces devant l'arrivée), 1 = retrait (derrière le départ)
            Bitboard c = kind == 0 ? capture_line(them, Neighbor[t][d], d)
                                   : capture_line(them, Neighbor[cur][opposite(d)], opposite(d));
            if (!c || path.n >= MAX_STEPS) continue;

            continued = true;
            path.to[path.n] = t;
            path.withdrawal[path.n] = kind == 1;
            ++path.n;
            if (!Rules::mandatoryContinuation) emit(t, acc | c, path);
            capture_dfs(us ^ square_bb(cur) ^ square_bb(t), them & ~c, t, visited | square_bb(t), d, acc | c, path,
                        emit);
            --path.n;
        }
    }

    if (Rules::mandatoryContinuation && !continued && path.n > 0) emit(cur, acc, path);
}

template <typename Emit>
void generate_all(const Position& pos, Emit&& emit) {
    Bitboard us = pos.pieces(pos.sideToMove), them = pos.pieces(~pos.sideToMove);
    Bitboard caps = capturers(us, them);
    Path path;

    if (caps) {
        for (Bitboard b = caps; b;) {
            int s = pop_lsb(b);
            path.from = s;
            path.n = 0;
            auto e = [&](int to, Bitboard c, const Path& p) { emit(s, to, c, &p); };
            capture_dfs(us, them, s, square_bb(s), -1, 0, path, e);
        }
        return;
    }

    Bitboard empty = pos.empty();
    for (Bitboard b = us; b;) {
        int s = pop_lsb(b);
        for (int d = 0; d < DIR_NB; ++d) {
            int t = Neighbor[s][d];
            if (t != SQ_NONE && (empty & square_bb(t))) emit(s, t, Bitboard(0), nullptr);
        }
    }
}

std::string path_to_string(int from, int to, const Path* p) {
    std::string s = square_to_string(from);
    if (!p) return s + square_to_string(to);
    for (int i = 0; i < p->n; ++i) s += square_to_string(p->to[i]) + (p->withdrawal[i] ? 'W' : 'A');
    return s;
}

}  // namespace

void generate_moves(const Position& pos, MoveList& list) {
    list.size = 0;
    int pieceStart = 0, lastFrom = SQ_NONE;
    generate_all(pos, [&](int from, int to, Bitboard cap, const Path*) {
        if (from != lastFrom) pieceStart = list.size, lastFrom = from;
        Move m = make_move(from, to, cap);
        if (cap)  // déduplication : seules les séquences d'une même pièce peuvent coïncider
            for (int i = pieceStart; i < list.size; ++i)
                if (list.moves[i] == m) return;
        if (list.size < MAX_MOVES) list.moves[list.size++] = m;
    });
}

std::vector<DetailedMove> generate_detailed(const Position& pos) {
    std::vector<DetailedMove> v;
    generate_all(pos, [&](int from, int to, Bitboard cap, const Path* p) {
        v.push_back({make_move(from, to, cap), path_to_string(from, to, p)});
    });
    return v;
}

std::string move_to_string(const Position& pos, Move m) {
    if (m == MOVE_NONE) return "(none)";
    for (const auto& dm : generate_detailed(pos))
        if (dm.move == m) return dm.notation;
    return "(illegal)";
}

// Accepte la notation exacte (ex. "d3e3A"), insensible à la casse des cases,
// ainsi que la liste des cases seules ("d3e3") si elle est non ambiguë.
Move parse_move(const Position& pos, const std::string& str) {
    auto normalize = [](std::string s, bool keepKinds) {
        std::string r;
        for (size_t i = 0; i < s.size(); ++i) {
            char c = s[i];
            if (c == '-' || c == 'x' || c == ' ') continue;
            bool isKind = (c == 'A' || c == 'W' || c == 'a' || c == 'w') &&
                          (i + 1 >= s.size() || !std::isdigit((unsigned char)s[i + 1]));
            if (isKind) {
                if (keepKinds) r += char(std::toupper((unsigned char)c));
            } else
                r += char(std::tolower((unsigned char)c));
        }
        return r;
    };

    auto moves = generate_detailed(pos);
    std::string exact = normalize(str, true);
    for (const auto& dm : moves)
        if (normalize(dm.notation, true) == exact) return dm.move;

    std::string loose = normalize(str, false);
    Move found = MOVE_NONE;
    for (const auto& dm : moves)
        if (normalize(dm.notation, false) == loose) {
            if (found != MOVE_NONE && found != dm.move) return MOVE_NONE;  // ambigu
            found = dm.move;
        }
    return found;
}

uint64_t perft(const Position& pos, int depth) {
    MoveList list;
    generate_moves(pos, list);
    if (depth <= 1) return depth == 1 ? list.size : 1;
    uint64_t n = 0;
    for (Move m : list) {
        Position p = pos;
        p.do_move(m);
        n += perft(p, depth - 1);
    }
    return n;
}

}  // namespace fanorona
