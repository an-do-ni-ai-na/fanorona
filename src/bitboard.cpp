#include "bitboard.h"

namespace fanorona {

int Neighbor[SQUARE_NB][DIR_NB];
Bitboard ShiftFrom[DIR_NB];

namespace {
constexpr int DX[DIR_NB] = {1, 1, 0, -1, -1, -1, 0, 1};
constexpr int DY[DIR_NB] = {0, 1, 1, 1, 0, -1, -1, -1};
}  // namespace

void Board::set(int f, int r) {
    files = f, ranks = r, mask = 0;
    for (int s = 0; s < SQUARE_NB; ++s)
        if (contains(file_of(s), rank_of(s))) mask |= square_bb(s);
    for (int d = 0; d < DIR_NB; ++d) ShiftFrom[d] = 0;
    for (int s = 0; s < SQUARE_NB; ++s)
        for (int d = 0; d < DIR_NB; ++d) {
            int x = file_of(s) + DX[d], y = rank_of(s) + DY[d];
            bool onBoard = contains(file_of(s), rank_of(s)) && contains(x, y);
            bool lineExists = !is_diagonal(d) || (STRONG_BB & square_bb(s));
            Neighbor[s][d] = onBoard && lineExists ? make_square(x, y) : SQ_NONE;
            if (Neighbor[s][d] != SQ_NONE) ShiftFrom[d] |= square_bb(s);
        }
}

void init_bitboards() { Board::set(FILE_NB, RANK_NB); }

Bitboard capturers(Bitboard us, Bitboard them) {
    Bitboard empty = ~(us | them) & Board::mask;
    Bitboard r = 0;
    for (int d = 0; d < DIR_NB; ++d) {
        int od = opposite(d);
        // Approche : p -> p+d vide, p+2d adverse.
        r |= shift(shift(them, od) & empty, od);
        // Retrait : p+d vide, p-d adverse.
        r |= shift(empty, od) & shift(them, d);
    }
    return r & us;
}

std::string square_to_string(int sq) {
    return std::string(1, char('a' + file_of(sq))) + char('1' + rank_of(sq));
}

int string_to_square(const std::string& s) {
    if (s.size() != 2) return SQ_NONE;
    int x = s[0] - 'a', y = s[1] - '1';
    if (!Board::contains(x, y)) return SQ_NONE;
    return make_square(x, y);
}

}  // namespace fanorona
