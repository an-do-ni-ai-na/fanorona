#include "bitboard.h"

namespace fanorona {

int Neighbor[SQUARE_NB][DIR_NB];

namespace {
constexpr int DX[DIR_NB] = {1, 1, 0, -1, -1, -1, 0, 1};
constexpr int DY[DIR_NB] = {0, 1, 1, 1, 0, -1, -1, -1};
}  // namespace

void init_bitboards() {
    for (int s = 0; s < SQUARE_NB; ++s)
        for (int d = 0; d < DIR_NB; ++d) {
            int x = file_of(s) + DX[d], y = rank_of(s) + DY[d];
            bool onBoard = x >= 0 && x < FILE_NB && y >= 0 && y < RANK_NB;
            bool lineExists = !is_diagonal(d) || (STRONG_BB & square_bb(s));
            Neighbor[s][d] = onBoard && lineExists ? make_square(x, y) : SQ_NONE;
        }
}

Bitboard capturers(Bitboard us, Bitboard them) {
    Bitboard empty = ~(us | them) & ALL_SQUARES;
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
    if (x < 0 || x >= FILE_NB || y < 0 || y >= RANK_NB) return SQ_NONE;
    return make_square(x, y);
}

}  // namespace fanorona
