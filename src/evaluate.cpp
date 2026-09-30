#include "evaluate.h"

#include "bitboard.h"
#include "nnue.h"

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

int mobility(Bitboard pieces, Bitboard empty) {
    int m = 0;
    for (int d = 0; d < DIR_NB; ++d) m += popcount(shift(pieces, d) & empty);
    return m;
}

int side_score(Bitboard us, Bitboard them, Bitboard empty) {
    int s = popcount(us) * PieceValue;
    s += popcount(us & STRONG_BB) * StrongPointBonus;
    // Connectivité : nombre de lignes partant de chaque pièce (= appartenance aux masques de décalage).
    for (int d = 0; d < DIR_NB; ++d) s += popcount(us & ShiftFrom[d]);
    s += mobility(us, empty) * MobilityWeight;
    s += popcount(capturers(us, them)) * ThreatWeight;
    return s;
}

}  // namespace

Value evaluate(const Position& pos) {
    // Le réseau est entraîné sur le Fanoron-Tsivy : pas d'NNUE sur un autre plateau.
    if (NNUE::enabled() && Rules::variant == Variant::Tsivy) return NNUE::evaluate(pos);

    Color us = pos.sideToMove;
    Bitboard u = pos.pieces(us), t = pos.pieces(~us), e = pos.empty();
    int nu = popcount(u), nt = popcount(t);

    int v = side_score(u, t, e) - side_score(t, u, e);
    // Quand on mène au matériel, moins il reste de pièces, mieux c'est.
    v += (nu - nt) * (start_pieces() - nu - nt) * TradeBonus;
    v += Tempo;

    return clamp_eval(v);
}

}  // namespace fanorona
