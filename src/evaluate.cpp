#include "evaluate.h"

#include "bitboard.h"

namespace fanorona {

namespace {

// Poids de l'évaluation "à la main" (HCE). Ils sont volontairement simples :
// l'étape suivante naturelle est un réglage automatique (Texel tuning) ou un
// réseau NNUE entraîné par auto-jeu, comme dans Stockfish.
constexpr int PieceValue = 100;
constexpr int StrongPointBonus = 4;   // pièce sur un point fort (8 lignes)
constexpr int MobilityWeight = 2;     // par déplacement simple possible
constexpr int ThreatWeight = 12;      // par pièce capable de capturer
constexpr int TradeBonus = 3;         // pousse le camp en avance à simplifier
constexpr int Tempo = 10;

int Connectivity[SQUARE_NB];  // nombre de lignes partant de la case

struct Init {
    Init() {
        for (int s = 0; s < SQUARE_NB; ++s) {
            int x = file_of(s), y = rank_of(s);
            int c = 0;
            // Calcul autonome (indépendant de l'ordre d'initialisation).
            for (int dx = -1; dx <= 1; ++dx)
                for (int dy = -1; dy <= 1; ++dy) {
                    if (!dx && !dy) continue;
                    if (dx && dy && (x + y) % 2) continue;
                    int nx = x + dx, ny = y + dy;
                    if (nx >= 0 && nx < FILE_NB && ny >= 0 && ny < RANK_NB) ++c;
                }
            Connectivity[s] = c;
        }
    }
} init;

int mobility(Bitboard pieces, Bitboard empty) {
    int m = 0;
    for (int d = 0; d < DIR_NB; ++d) m += popcount(shift(pieces, d) & empty);
    return m;
}

int side_score(Bitboard us, Bitboard them, Bitboard empty) {
    int s = popcount(us) * PieceValue;
    s += popcount(us & STRONG_BB) * StrongPointBonus;
    for (Bitboard b = us; b;) s += Connectivity[pop_lsb(b)];
    s += mobility(us, empty) * MobilityWeight;
    s += popcount(capturers(us, them)) * ThreatWeight;
    return s;
}

}  // namespace

Value evaluate(const Position& pos) {
    Color us = pos.sideToMove;
    Bitboard u = pos.pieces(us), t = pos.pieces(~us), e = pos.empty();
    int nu = popcount(u), nt = popcount(t);

    int v = side_score(u, t, e) - side_score(t, u, e);
    // Quand on mène au matériel, moins il reste de pièces, mieux c'est.
    v += (nu - nt) * (44 - nu - nt) * TradeBonus;
    v += Tempo;

    if (v >= VALUE_MATE_IN_MAX_PLY) v = VALUE_MATE_IN_MAX_PLY - 1;
    if (v <= -VALUE_MATE_IN_MAX_PLY) v = -VALUE_MATE_IN_MAX_PLY + 1;
    return v;
}

}  // namespace fanorona
