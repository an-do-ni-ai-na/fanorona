#include "position.h"

#include <sstream>

#include "bitboard.h"

namespace fanorona {

namespace Zobrist {
Key psq[COLOR_NB][SQUARE_NB];
Key side;

void init() {
    uint64_t s = 0x9E3779B97F4A7C15ULL;
    auto rnd = [&]() {  // splitmix64
        uint64_t z = (s += 0x9E3779B97F4A7C15ULL);
        z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
        z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
        return z ^ (z >> 31);
    };
    for (auto& c : psq)
        for (auto& k : c) k = rnd();
    side = rnd();
}
}  // namespace Zobrist

Key Position::compute_key() const {
    Key k = sideToMove == BLACK ? Zobrist::side : 0;
    for (int c = 0; c < COLOR_NB; ++c)
        for (Bitboard b = byColor[c]; b;) k ^= Zobrist::psq[c][pop_lsb(b)];
    return k;
}

// Format FEN : rangées de 5 (haut) à 1 (bas) séparées par '/', 'W' / 'B'
// pour les pièces, chiffres pour les cases vides, puis le trait (w/b),
// puis optionnellement le compteur sans capture et le numéro de demi-coup.
bool Position::set(const std::string& fen) {
    std::istringstream is(fen);
    std::string board, stm;
    if (!(is >> board >> stm)) return false;

    Position p{};
    int x = 0, y = RANK_NB - 1;
    for (char ch : board) {
        if (ch == '/') {
            if (x != FILE_NB || y == 0) return false;
            x = 0, --y;
        } else if (ch >= '1' && ch <= '9') {
            x += ch - '0';
        } else if (ch == 'W' || ch == 'w' || ch == 'B' || ch == 'b') {
            if (x >= FILE_NB) return false;
            p.byColor[(ch == 'W' || ch == 'w') ? WHITE : BLACK] |= square_bb(make_square(x, y));
            ++x;
        } else
            return false;
        if (x > FILE_NB) return false;
    }
    if (x != FILE_NB || y != 0) return false;
    if (stm != "w" && stm != "b") return false;
    p.sideToMove = stm == "w" ? WHITE : BLACK;
    p.rule50 = 0;
    p.gamePly = 0;
    int fullmove = 1;
    if (is >> p.rule50) is >> fullmove;
    p.gamePly = 2 * (fullmove - 1) + (p.sideToMove == BLACK);
    p.key = p.compute_key();
    *this = p;
    return true;
}

std::string Position::fen() const {
    std::ostringstream os;
    for (int y = RANK_NB - 1; y >= 0; --y) {
        int emptyCnt = 0;
        for (int x = 0; x < FILE_NB; ++x) {
            Bitboard b = square_bb(make_square(x, y));
            char ch = byColor[WHITE] & b ? 'W' : byColor[BLACK] & b ? 'B' : 0;
            if (!ch) {
                ++emptyCnt;
                continue;
            }
            if (emptyCnt) os << emptyCnt, emptyCnt = 0;
            os << ch;
        }
        if (emptyCnt) os << emptyCnt;
        if (y) os << '/';
    }
    os << (sideToMove == WHITE ? " w " : " b ") << rule50 << ' ' << 1 + gamePly / 2;
    return os.str();
}

std::string Position::pretty() const {
    std::ostringstream os;
    for (int y = RANK_NB - 1; y >= 0; --y) {
        os << ' ' << y + 1 << "  ";
        for (int x = 0; x < FILE_NB; ++x) {
            Bitboard b = square_bb(make_square(x, y));
            os << (byColor[WHITE] & b ? 'W' : byColor[BLACK] & b ? 'B' : '.');
            if (x < FILE_NB - 1) os << " - ";
        }
        os << '\n';
        if (y) {
            os << "    ";
            for (int x = 0; x < FILE_NB; ++x) {
                os << '|';
                if (x < FILE_NB - 1) os << (((x + y) % 2 == 0) ? " \\ " : " / ");
            }
            os << '\n';
        }
    }
    os << "    a   b   c   d   e   f   g   h   i\n\n";
    os << "Fen: " << fen() << '\n';
    os << "Key: " << std::hex << key << std::dec << '\n';
    os << "Trait: " << (sideToMove == WHITE ? "blancs (W)" : "noirs (B)") << "  W=" << popcount(byColor[WHITE])
       << " B=" << popcount(byColor[BLACK]) << '\n';
    return os.str();
}

void Position::do_move(Move m) {
    Color us = sideToMove, them = ~us;
    int from = move_from(m), to = move_to(m);
    Bitboard cap = move_captured(m);

    byColor[us] ^= square_bb(from) | square_bb(to);
    key ^= Zobrist::psq[us][from] ^ Zobrist::psq[us][to];
    byColor[them] &= ~cap;
    for (Bitboard b = cap; b;) key ^= Zobrist::psq[them][pop_lsb(b)];

    rule50 = cap ? 0 : rule50 + 1;
    ++gamePly;
    sideToMove = them;
    key ^= Zobrist::side;
}

void Position::do_null_move() {
    sideToMove = ~sideToMove;
    key ^= Zobrist::side;
    ++rule50;
    ++gamePly;
}

}  // namespace fanorona
