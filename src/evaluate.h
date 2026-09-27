// Fanorona engine - évaluation statique
#pragma once

#include "position.h"

namespace fanorona {

// Évaluation du point de vue du camp au trait, en centi-pions
// (100 = une pièce d'avance à matériel égal par ailleurs).
// Bascule automatiquement vers NNUE::evaluate() si NNUE::enabled() (voir nnue.h/uci.cpp).
Value evaluate(const Position& pos);

// Ramène une valeur brute dans les bornes valides (hors zone réservée aux scores de mat).
// Partagé entre evaluate() (HCE) et NNUE::evaluate().
constexpr Value clamp_eval(int v) {
    if (v >= VALUE_MATE_IN_MAX_PLY) return VALUE_MATE_IN_MAX_PLY - 1;
    if (v <= -VALUE_MATE_IN_MAX_PLY) return -VALUE_MATE_IN_MAX_PLY + 1;
    return Value(v);
}

}  // namespace fanorona
