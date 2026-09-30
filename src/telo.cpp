#include "telo.h"

#include <algorithm>
#include <iostream>
#include <limits>
#include <sstream>

#include "position.h"

namespace fanorona::Telo {

namespace {

constexpr int N = 9;
constexpr int LINES[8] = {0007, 0070, 0700, 0111, 0222, 0444, 0421, 0124};  // rangées, colonnes, diagonales
int Adj[N];  // voisins (masque) de chaque case

bool has_line(int m) {
    for (int l : LINES)
        if ((m & l) == l) return true;
    return false;
}
int pieces_of(int w, int b, Color c) { return c == WHITE ? w : b; }

// Coups bruts : (from, to), from = -1 pour une pose.
template <typename F>
void for_each_move(int w, int b, Color stm, F&& f) {
    int us = pieces_of(w, b, stm), empty = ~(w | b) & 0777;
    if (__builtin_popcount(us) < 3) {
        for (int t = 0; t < N; ++t)
            if (empty >> t & 1) f(-1, t);
        return;
    }
    for (int s = 0; s < N; ++s)
        if (us >> s & 1)
            for (int t = 0; t < N; ++t)
                if ((Adj[s] >> t & 1) && (empty >> t & 1)) f(s, t);
}

void apply(int& w, int& b, Color stm, int from, int to) {
    int& us = stm == WHITE ? w : b;
    if (from >= 0) us &= ~(1 << from);
    us |= 1 << to;
}

std::string sq(int s) { return std::string(1, char('a' + s % 3)) + char('1' + s / 3); }
std::string notation(int from, int to) { return from < 0 ? sq(to) : sq(from) + sq(to); }

// Table de résolution : index = w | b << 9 | stm << 18.
int8_t Res[1 << 19];    // 1 gagné, -1 perdu, 0 nulle (ou état impossible)
uint8_t Dist[1 << 19];  // distance en demi-coups
bool solvedOnce = false;

int index_of(int w, int b, Color stm) { return w | b << 9 | int(stm) << 18; }

bool valid(int w, int b, Color stm) {
    if (w & b) return false;
    int cw = __builtin_popcount(w), cb = __builtin_popcount(b);
    if (cw > 3 || cb > 3) return false;
    if (cw == 3 && cb == 3) return true;
    return stm == WHITE ? cw == cb : cw == cb + 1;
}

}  // namespace

void init() {
    if (solvedOnce) return;
    solvedOnce = true;
    for (int s = 0; s < N; ++s) {
        int x = s % 3, y = s / 3;
        Adj[s] = 0;
        for (int dx = -1; dx <= 1; ++dx)
            for (int dy = -1; dy <= 1; ++dy) {
                if (!dx && !dy) continue;
                if (dx && dy && (x + y) % 2) continue;  // diagonales seulement depuis les points forts
                int nx = x + dx, ny = y + dy;
                if (nx >= 0 && nx < 3 && ny >= 0 && ny < 3) Adj[s] |= 1 << (ny * 3 + nx);
            }
    }
    std::vector<int> states;
    for (int w = 0; w < 512; ++w)
        for (int b = 0; b < 512; ++b)
            for (Color c : {WHITE, BLACK})
                if (valid(w, b, c)) {
                    int i = index_of(w, b, c);
                    Res[i] = 0;
                    if (has_line(pieces_of(w, b, ~c)) || has_line(pieces_of(w, b, c))) {
                        // L'adversaire vient d'aligner : perdu (l'autre cas est inatteignable).
                        Res[i] = has_line(pieces_of(w, b, ~c)) ? -1 : 1, Dist[i] = 0;
                        continue;
                    }
                    bool any = false;
                    for_each_move(w, b, c, [&](int, int) { any = true; });
                    if (!any) Res[i] = -1, Dist[i] = 0;  // bloqué : perdu
                    else states.push_back(i);
                }
    // Analyse rétrograde par couches de distance : un état est gagné en k s'il a un fils perdu en k-1 ;
    // perdu en k si tous ses fils sont gagnés et le plus lointain l'est en k-1.
    for (int k = 1;; ++k) {
        std::vector<std::pair<int, int8_t>> found;
        for (int i : states) {
            if (Res[i]) continue;
            int w = i & 0777, b = i >> 9 & 0777;
            Color c = Color(i >> 18);
            bool win = false, allWin = true;
            int maxWinDist = 0;
            for_each_move(w, b, c, [&](int from, int to) {
                int w2 = w, b2 = b;
                apply(w2, b2, c, from, to);
                int j = index_of(w2, b2, ~c);
                if (Res[j] == -1 && Dist[j] == k - 1) win = true;
                if (Res[j] != 1) allWin = false;
                else maxWinDist = std::max(maxWinDist, int(Dist[j]));
            });
            if (win) found.push_back({i, 1});
            else if (allWin && maxWinDist == k - 1) found.push_back({i, -1});
        }
        if (found.empty()) break;
        for (auto [i, r] : found) Res[i] = r, Dist[i] = uint8_t(k);
    }
}

int solved(const State& st) {
    int i = index_of(st.w, st.b, st.stm);
    return Res[i] > 0 ? Dist[i] : Res[i] < 0 ? -int(Dist[i]) - 1 : 0;
}

bool set_fen(State& st, const std::string& f) {
    std::istringstream is(f);
    std::string board, side;
    if (!(is >> board >> side)) return false;
    State s;
    int x = 0, y = 2;
    for (char ch : board) {
        if (ch == '/') {
            if (x != 3 || y == 0) return false;
            x = 0, --y;
        } else if (ch >= '1' && ch <= '3') x += ch - '0';
        else if (ch == 'W' || ch == 'B') {
            if (x >= 3) return false;
            (ch == 'W' ? s.w : s.b) |= 1 << (y * 3 + x);
            ++x;
        } else
            return false;
        if (x > 3) return false;
    }
    if (x != 3 || y != 0 || (side != "w" && side != "b")) return false;
    s.stm = side == "w" ? WHITE : BLACK;
    if (!valid(s.w, s.b, s.stm)) return false;
    int fullmove = 1;
    if (is >> s.quiet) is >> fullmove;
    s.ply = 2 * (fullmove - 1) + (s.stm == BLACK);
    st = s;
    return true;
}

std::string fen(const State& st) {
    std::ostringstream os;
    for (int y = 2; y >= 0; --y) {
        int e = 0;
        for (int x = 0; x < 3; ++x) {
            int s = y * 3 + x;
            char ch = st.w >> s & 1 ? 'W' : st.b >> s & 1 ? 'B' : 0;
            if (!ch) {
                ++e;
                continue;
            }
            if (e) os << e, e = 0;
            os << ch;
        }
        if (e) os << e;
        if (y) os << '/';
    }
    os << (st.stm == WHITE ? " w " : " b ") << st.quiet << ' ' << 1 + st.ply / 2;
    return os.str();
}

std::string pretty(const State& st) {
    std::ostringstream os;
    for (int y = 2; y >= 0; --y) {
        os << ' ' << y + 1 << "  ";
        for (int x = 0; x < 3; ++x) {
            int s = y * 3 + x;
            os << (st.w >> s & 1 ? 'W' : st.b >> s & 1 ? 'B' : '.') << (x < 2 ? " - " : "");
        }
        os << '\n';
        if (y) os << (y == 2 ? "    | \\ | / |\n" : "    | / | \\ |\n");
    }
    os << "    a   b   c\n\nFen: " << fen(st) << "\nTrait: " << (st.stm == WHITE ? "blancs (W)" : "noirs (B)")
       << "  Phase: " << (__builtin_popcount(pieces_of(st.w, st.b, st.stm)) < 3 ? "pose" : "déplacement") << '\n';
    int v = solved(st);
    os << "Valeur exacte: " << (v > 0   ? "gain en " + std::to_string(v) + " demi-coups"
                                : v < 0 ? "perte en " + std::to_string(-v - 1) + " demi-coups"
                                        : "nulle") << '\n';
    return os.str();
}

std::vector<TeloMove> moves(const State& st) {
    std::vector<TeloMove> v;
    if (has_line(st.w) || has_line(st.b)) return v;
    for_each_move(st.w, st.b, st.stm, [&](int from, int to) {
        State c = st;
        apply(c.w, c.b, st.stm, from, to);
        c.stm = ~st.stm;
        c.quiet = from < 0 ? 0 : st.quiet + 1;
        ++c.ply;
        v.push_back({notation(from, to), c});
    });
    return v;
}

std::string status(const State& st, const std::vector<Key>& history) {
    if (has_line(st.w)) return "white wins";
    if (has_line(st.b)) return "black wins";
    if (moves(st).empty()) return st.stm == WHITE ? "black wins" : "white wins";
    if (st.quiet >= Rules::noCaptureLimit) return "draw (move limit)";
    if (std::count(history.begin(), history.end(), st.key()) >= 3) return "draw (threefold repetition)";
    return "ongoing";
}

namespace {

uint64_t nodes = 0;

// Heuristique des niveaux faibles : lignes « ouvertes » (2 pions à soi et un point libre).
int heuristic(const State& st) {
    int us = pieces_of(st.w, st.b, st.stm), them = pieces_of(st.w, st.b, ~st.stm), s = 0;
    for (int l : LINES) {
        if (!(l & them) && __builtin_popcount(l & us) == 2) s += 10;
        if (!(l & us) && __builtin_popcount(l & them) == 2) s -= 10;
    }
    return s;
}

int negamax(const State& st, int depth, int ply, int alpha, int beta) {
    ++nodes;
    if (has_line(pieces_of(st.w, st.b, ~st.stm))) return mated_in(ply);
    auto ms = moves(st);
    if (ms.empty()) return mated_in(ply);
    if (depth <= 0) return heuristic(st);
    for (const auto& m : ms) {
        int v = -negamax(m.child, depth - 1, ply + 1, -beta, -alpha);
        if (v > alpha) alpha = v;
        if (alpha >= beta) break;
    }
    return alpha;
}

std::string score_str(int v) {
    if (std::abs(v) >= VALUE_MATE_IN_MAX_PLY) {
        int plies = VALUE_MATE - std::abs(v);
        return "mate " + std::to_string(v > 0 ? (plies + 1) / 2 : -(plies / 2));
    }
    return "cp " + std::to_string(v);
}

}  // namespace

void go(const State& st, int depth) {
    auto ms = moves(st);
    if (ms.empty()) {
        std::cout << "bestmove (none)" << std::endl;
        return;
    }
    nodes = 0;
    std::string best = ms.front().notation;
    int bestScore = std::numeric_limits<int>::min();
    if (depth <= 0) {
        // Jeu parfait : gagner au plus vite, perdre au plus tard, sinon garder la nulle.
        auto rank = [](const State& child) {
            int c = solved(child);  // valeur pour l'adversaire
            // Adversaire perdant en n = -c - 1 : nous gagnons en n + 1 = -c (au plus vite).
            // Adversaire gagnant en c : nous perdons en c + 1 (au plus tard).
            return c < 0 ? 100000 + c : c > 0 ? -100000 + c : 0;
        };
        for (const auto& m : ms) {
            ++nodes;
            int r = rank(m.child);
            if (r > bestScore) bestScore = r, best = m.notation;
        }
        int v = solved(st);
        int score = v > 0 ? mate_in(v) : v < 0 ? mated_in(-v - 1) : 0;
        std::cout << "info depth " << (v > 0 ? v : v < 0 ? -v - 1 : 1) << " seldepth 0 score " << score_str(score)
                  << " nodes " << nodes << " nps 0 hashfull 0 time 0 pv " << best << std::endl;
    } else {
        for (const auto& m : ms) {
            int v = -negamax(m.child, depth - 1, 1, -VALUE_INFINITE, VALUE_INFINITE);
            if (v > bestScore) bestScore = v, best = m.notation;
        }
        std::cout << "info depth " << depth << " seldepth " << depth << " score " << score_str(bestScore)
                  << " nodes " << nodes << " nps 0 hashfull 0 time 0 pv " << best << std::endl;
    }
    std::cout << "bestmove " << best << std::endl;
}

uint64_t perft(const State& st, int depth) {
    auto ms = moves(st);
    if (depth <= 1) return depth == 1 ? ms.size() : 1;
    uint64_t n = 0;
    for (const auto& m : ms) n += perft(m.child, depth - 1);
    return n;
}

}  // namespace fanorona::Telo
