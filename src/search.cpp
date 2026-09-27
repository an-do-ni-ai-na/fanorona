#include "search.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>
#include <sstream>

#include "bitboard.h"
#include "evaluate.h"
#include "movegen.h"
#include "tt.h"

namespace fanorona {

namespace Search {

std::atomic<bool> stopSignal{false};

namespace {

using Clock = std::chrono::steady_clock;

constexpr int QS_MAX_DEPTH = 12;  // borne de sécurité sur les séquences de captures en quiescence

int Reductions[MAX_PLY][64];

// Tables d'historique conservées entre les recherches d'une même partie.
int HistoryTable[COLOR_NB][SQUARE_NB][SQUARE_NB];

Value value_to_tt(Value v, int ply) {
    return v >= VALUE_MATE_IN_MAX_PLY ? v + ply : v <= -VALUE_MATE_IN_MAX_PLY ? v - ply : v;
}
Value value_from_tt(Value v, int ply) {
    if (v == VALUE_NONE) return VALUE_NONE;
    return v >= VALUE_MATE_IN_MAX_PLY ? v - ply : v <= -VALUE_MATE_IN_MAX_PLY ? v + ply : v;
}

bool has_paika(Bitboard us, Bitboard empty) {
    for (int d = 0; d < DIR_NB; ++d)
        if (shift(us, d) & empty) return true;
    return false;
}

std::string score_to_uci(Value v) {
    std::ostringstream os;
    if (std::abs(v) >= VALUE_MATE_IN_MAX_PLY) {
        int plies = VALUE_MATE - std::abs(v);
        os << "mate " << (v > 0 ? (plies + 1) / 2 : -(plies / 2));
    } else
        os << "cp " << v;
    return os.str();
}

class Worker {
   public:
    Worker(const Position& root, const std::vector<Key>& history, const SearchLimits& lim, bool verb)
        : rootPos(root), keys(history), limits(lim), verbose(verb) {
        keys.reserve(keys.size() + MAX_PLY + 8);
        if (keys.empty() || keys.back() != root.key) keys.push_back(root.key);
        std::memset(killers, 0, sizeof(killers));
    }

    SearchResult run();

   private:
    Value search(const Position& pos, Value alpha, Value beta, int depth, int ply, bool pvNode, bool allowNull);
    Value qsearch(const Position& pos, Value alpha, Value beta, int ply, int qdepth);
    bool is_draw(const Position& pos) const;
    void check_time();
    int64_t elapsed() const {
        return std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - startTime).count();
    }
    void set_time_limits();
    std::string pv_string(const std::vector<Move>& pv) const;

    Position rootPos;
    std::vector<Key> keys;
    SearchLimits limits;
    bool verbose;

    Clock::time_point startTime;
    int64_t optimumTime = 0, maximumTime = 0;
    bool timeManaged = false;
    bool stopped = false;
    uint64_t nodes = 0;
    int selDepth = 0;
    int rootDepth = 0;

    Move killers[MAX_PLY + 2][2];
    Move pvTable[MAX_PLY + 1][MAX_PLY + 1];
    int pvLength[MAX_PLY + 1];
};

void Worker::set_time_limits() {
    Color us = rootPos.sideToMove;
    if (limits.movetime > 0) {
        optimumTime = maximumTime = std::max<int64_t>(1, limits.movetime - 5);
        timeManaged = true;
    } else if (limits.time[us] > 0 && !limits.infinite) {
        int64_t t = limits.time[us], inc = limits.inc[us];
        int mtg = limits.movestogo > 0 ? std::min(limits.movestogo, 40) : 35;
        int64_t safety = std::min<int64_t>(50, t / 10);
        optimumTime = std::max<int64_t>(1, std::min(t / mtg + inc * 3 / 4, t - safety));
        maximumTime = std::max<int64_t>(1, std::min(optimumTime * 4, t / 3 + inc - safety));
        maximumTime = std::max(maximumTime, optimumTime);
        timeManaged = true;
    }
}

void Worker::check_time() {
    if ((nodes & 1023) != 0) return;
    if (stopSignal.load(std::memory_order_relaxed)) stopped = true;
    else if (limits.nodes && nodes >= limits.nodes) stopped = true;
    else if (timeManaged && elapsed() >= maximumTime) stopped = true;
}

// Nulle par la règle des coups sans capture, ou répétition (une seule
// répétition suffit à l'intérieur de l'arbre de recherche).
bool Worker::is_draw(const Position& pos) const {
    if (pos.rule50 >= Rules::noCaptureLimit) return true;
    int n = int(keys.size());
    for (int i = n - 3; i >= 0 && n - 1 - i <= pos.rule50; i -= 2)
        if (keys[i] == pos.key) return true;
    return false;
}

Value Worker::qsearch(const Position& pos, Value alpha, Value beta, int ply, int qdepth) {
    ++nodes;
    check_time();
    if (stopped) return VALUE_ZERO;
    selDepth = std::max(selDepth, ply);

    Color us = pos.sideToMove;
    Bitboard u = pos.pieces(us), t = pos.pieces(~us);
    if (!u) return mated_in(ply);
    if (ply >= MAX_PLY - 1) return evaluate(pos);

    // Sans capture possible, la position est "calme" : on renvoie
    // l'évaluation statique (sauf blocage total = défaite).
    if (!capturers(u, t)) return has_paika(u, pos.empty()) ? evaluate(pos) : mated_in(ply);

    // Les captures sont obligatoires, donc le "stand pat" n'est pas légal au
    // sens strict. On l'utilise tout de même comme borne heuristique (comme
    // aux échecs) : l'évaluation compte déjà les menaces de capture, et sans
    // cela la quiescence explose dans les ouvertures riches en captures.
    Value standPat = evaluate(pos);
    if (qdepth >= QS_MAX_DEPTH || standPat >= beta) return standPat;
    if (standPat > alpha) alpha = standPat;

    bool found;
    TTEntry* tte = TT.probe(pos.key, found);
    Value ttValue = found ? value_from_tt(tte->value, ply) : VALUE_NONE;
    if (found && ttValue != VALUE_NONE && (tte->bound() & (ttValue >= beta ? BOUND_LOWER : BOUND_UPPER)))
        return ttValue;
    Move ttMove = found ? tte->move : MOVE_NONE;

    MoveList list;
    generate_moves(pos, list);
    int scores[MAX_MOVES];
    for (int i = 0; i < list.size; ++i)
        scores[i] = list.moves[i] == ttMove ? 1 << 20 : popcount(move_captured(list.moves[i]));

    Value oldAlpha = alpha, best = standPat;
    Move bestMove = MOVE_NONE;
    for (int i = 0; i < list.size; ++i) {
        int bi = i;
        for (int j = i + 1; j < list.size; ++j)
            if (scores[j] > scores[bi]) bi = j;
        std::swap(list.moves[i], list.moves[bi]);
        std::swap(scores[i], scores[bi]);
        Move m = list.moves[i];

        // Delta pruning : même en gagnant ces pièces, on ne remonte pas alpha.
        if (standPat + popcount(move_captured(m)) * 130 + 150 <= alpha) continue;

        Position child = pos;
        child.do_move(m);
        Value v = -qsearch(child, -beta, -alpha, ply + 1, qdepth + 1);
        if (stopped) return VALUE_ZERO;
        if (v > best) {
            best = v;
            if (v > alpha) {
                alpha = v;
                bestMove = m;
                if (v >= beta) break;
            }
        }
    }

    Bound b = best >= beta ? BOUND_LOWER : best > oldAlpha ? BOUND_EXACT : BOUND_UPPER;
    TT.store(tte, pos.key, value_to_tt(best, ply), b, 0, bestMove, VALUE_NONE);
    return best;
}

Value Worker::search(const Position& pos, Value alpha, Value beta, int depth, int ply, bool pvNode,
                     bool allowNull) {
    pvLength[ply] = ply;
    Color us = pos.sideToMove;

    if (ply > 0) {
        if (is_draw(pos)) return VALUE_DRAW;
        // Élagage par distance au mat.
        alpha = std::max(mated_in(ply), alpha);
        beta = std::min(mate_in(ply + 1), beta);
        if (alpha >= beta) return alpha;
    }
    if (!pos.pieces(us)) return mated_in(ply);
    if (depth <= 0) return qsearch(pos, alpha, beta, ply, 0);
    if (ply >= MAX_PLY - 1) return evaluate(pos);

    ++nodes;
    check_time();
    if (stopped) return VALUE_ZERO;
    selDepth = std::max(selDepth, ply);

    // --- Table de transposition ---
    bool found;
    TTEntry* tte = TT.probe(pos.key, found);
    Move ttMove = found ? tte->move : MOVE_NONE;
    Value ttValue = found ? value_from_tt(tte->value, ply) : VALUE_NONE;
    if (!pvNode && found && tte->depth >= depth && ttValue != VALUE_NONE &&
        (tte->bound() & (ttValue >= beta ? BOUND_LOWER : BOUND_UPPER)))
        return ttValue;

    MoveList list;
    generate_moves(pos, list);
    if (list.size == 0) return mated_in(ply);  // bloqué : défaite

    bool capturePos = is_capture(list.moves[0]);
    Value staticEval = VALUE_NONE;
    if (!capturePos) staticEval = (found && tte->eval != VALUE_NONE) ? Value(tte->eval) : evaluate(pos);

    // --- Élagages avant d'examiner les coups (positions calmes uniquement) ---
    if (!pvNode && !capturePos && std::abs(beta) < VALUE_MATE_IN_MAX_PLY) {
        // Reverse futility pruning
        if (depth <= 6 && staticEval - 90 * depth >= beta) return staticEval;

        // Null move pruning
        if (allowNull && depth >= 3 && staticEval >= beta && popcount(pos.pieces(us)) >= 3) {
            int R = 3 + depth / 4;
            Position child = pos;
            child.do_null_move();
            keys.push_back(child.key);
            Value v = -search(child, -beta, -beta + 1, depth - 1 - R, ply + 1, false, false);
            keys.pop_back();
            if (stopped) return VALUE_ZERO;
            if (v >= beta) return v >= VALUE_MATE_IN_MAX_PLY ? beta : v;
        }
    }

    // Coup forcé : on prolonge (sans exploser : limité par la profondeur racine).
    int extension = (list.size == 1 && ply < 2 * rootDepth) ? 1 : 0;

    // --- Ordonnancement des coups ---
    int scores[MAX_MOVES];
    for (int i = 0; i < list.size; ++i) {
        Move m = list.moves[i];
        if (m == ttMove) scores[i] = 1 << 30;
        else if (is_capture(m)) scores[i] = (1 << 24) + popcount(move_captured(m)) * 1024;
        else if (m == killers[ply][0]) scores[i] = (1 << 23);
        else if (m == killers[ply][1]) scores[i] = (1 << 23) - 1;
        else scores[i] = HistoryTable[us][move_from(m)][move_to(m)];
    }

    Value oldAlpha = alpha, best = -VALUE_INFINITE;
    Move bestMove = MOVE_NONE;
    Move quietsTried[64];
    int quietCount = 0;

    for (int i = 0; i < list.size; ++i) {
        int bi = i;
        for (int j = i + 1; j < list.size; ++j)
            if (scores[j] > scores[bi]) bi = j;
        std::swap(list.moves[i], list.moves[bi]);
        std::swap(scores[i], scores[bi]);
        Move m = list.moves[i];
        int moveCount = i + 1;
        bool quiet = !is_capture(m);

        Position child = pos;
        child.do_move(m);
        keys.push_back(child.key);

        int newDepth = depth - 1 + extension;
        Value v;
        if (moveCount == 1)
            v = -search(child, -beta, -alpha, newDepth, ply + 1, pvNode, true);
        else {
            // Late move reductions sur les coups calmes tardifs.
            int r = 0;
            if (depth >= 3 && moveCount > 3 && quiet && m != killers[ply][0] && m != killers[ply][1]) {
                r = Reductions[std::min(depth, MAX_PLY - 1)][std::min(moveCount, 63)];
                if (pvNode) --r;
                if (scores[i] > 4000) --r;
                r = std::clamp(r, 0, newDepth - 1);
            }
            v = -search(child, -alpha - 1, -alpha, newDepth - r, ply + 1, false, true);
            if (v > alpha && r > 0) v = -search(child, -alpha - 1, -alpha, newDepth, ply + 1, false, true);
            if (pvNode && v > alpha && v < beta) v = -search(child, -beta, -alpha, newDepth, ply + 1, true, true);
        }
        keys.pop_back();
        if (stopped) return VALUE_ZERO;

        if (v > best) {
            best = v;
            if (v > alpha) {
                bestMove = m;
                alpha = v;
                if (pvNode) {
                    pvTable[ply][ply] = m;
                    for (int k = ply + 1; k < pvLength[ply + 1]; ++k) pvTable[ply][k] = pvTable[ply + 1][k];
                    pvLength[ply] = std::max(pvLength[ply + 1], ply + 1);
                }
                if (alpha >= beta) {
                    if (quiet) {
                        if (killers[ply][0] != m) killers[ply][1] = killers[ply][0], killers[ply][0] = m;
                        int bonus = std::min(depth * depth, 400);
                        auto upd = [&](Move mv, int b) {
                            int& h = HistoryTable[us][move_from(mv)][move_to(mv)];
                            h += b - h * std::abs(b) / 16384;
                        };
                        upd(m, bonus);
                        for (int k = 0; k < quietCount; ++k) upd(quietsTried[k], -bonus);
                    }
                    break;
                }
            }
        }
        if (quiet && quietCount < 64) quietsTried[quietCount++] = m;
    }

    Bound b = best >= beta ? BOUND_LOWER : (pvNode && best > oldAlpha) ? BOUND_EXACT : BOUND_UPPER;
    TT.store(tte, pos.key, value_to_tt(best, ply), b, depth, bestMove, staticEval);
    return best;
}

std::string Worker::pv_string(const std::vector<Move>& pv) const {
    std::string s;
    Position p = rootPos;
    for (Move m : pv) {
        std::string ms = move_to_string(p, m);
        if (ms == "(illegal)") break;
        s += (s.empty() ? "" : " ") + ms;
        p.do_move(m);
    }
    return s;
}

SearchResult Worker::run() {
    startTime = Clock::now();
    set_time_limits();
    TT.new_search();

    SearchResult result;
    MoveList rootMoves;
    generate_moves(rootPos, rootMoves);
    if (rootMoves.size == 0) {
        result.score = mated_in(0);
        return result;
    }
    result.bestMove = rootMoves.moves[0];

    int maxDepth = limits.depth > 0 ? std::min(limits.depth, MAX_PLY - 1) : MAX_PLY - 1;
    Value prevScore = VALUE_NONE;

    for (rootDepth = 1; rootDepth <= maxDepth; ++rootDepth) {
        selDepth = 0;
        Value alpha = -VALUE_INFINITE, beta = VALUE_INFINITE, delta = 25;
        if (rootDepth >= 5 && prevScore != VALUE_NONE && std::abs(prevScore) < VALUE_MATE_IN_MAX_PLY) {
            alpha = std::max(prevScore - delta, -VALUE_INFINITE);
            beta = std::min(prevScore + delta, VALUE_INFINITE);
        }

        Value score;
        // Fenêtres d'aspiration
        while (true) {
            score = search(rootPos, alpha, beta, rootDepth, 0, true, false);
            if (stopped) break;
            if (score <= alpha) {
                beta = (alpha + beta) / 2;
                alpha = std::max(score - delta, -VALUE_INFINITE);
            } else if (score >= beta)
                beta = std::min(score + delta, VALUE_INFINITE);
            else
                break;
            delta += delta / 2 + 5;
        }

        if (stopped && rootDepth > 1) break;  // itération incomplète : on garde la précédente

        result.depth = rootDepth;
        result.score = score;
        result.pv.assign(pvTable[0], pvTable[0] + pvLength[0]);
        if (!result.pv.empty()) result.bestMove = result.pv[0];
        prevScore = score;

        int64_t ms = elapsed();
        if (verbose) {
            std::cout << "info depth " << rootDepth << " seldepth " << selDepth << " score " << score_to_uci(score)
                      << " nodes " << nodes << " nps " << (nodes * 1000 / std::max<int64_t>(ms, 1)) << " hashfull "
                      << TT.hashfull() << " time " << ms << " pv " << pv_string(result.pv) << std::endl;
        }

        if (stopped) break;
        if (timeManaged && !limits.infinite) {
            if (rootMoves.size == 1) break;  // un seul coup légal : inutile de réfléchir
            if (ms >= optimumTime * 6 / 10) break;
        }
        if (!limits.infinite && std::abs(score) >= VALUE_MATE_IN_MAX_PLY &&
            rootDepth > VALUE_MATE - std::abs(score) + 2)
            break;
    }

    result.nodes = nodes;
    result.timeMs = elapsed();
    return result;
}

}  // namespace

void init() {
    for (int d = 1; d < MAX_PLY; ++d)
        for (int m = 1; m < 64; ++m) Reductions[d][m] = int(0.5 + std::log(d) * std::log(m) / 2.25);
}

void clear() {
    std::memset(HistoryTable, 0, sizeof(HistoryTable));
    TT.clear();
}

SearchResult think(const Position& pos, const std::vector<Key>& history, const SearchLimits& limits, bool verbose) {
    auto w = std::make_unique<Worker>(pos, history, limits, verbose);
    return w->run();
}

}  // namespace Search

}  // namespace fanorona
