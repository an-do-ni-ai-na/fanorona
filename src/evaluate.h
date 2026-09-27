// Fanorona engine - évaluation statique
#pragma once

#include "position.h"

namespace fanorona {

// Évaluation du point de vue du camp au trait, en centi-pions
// (100 = une pièce d'avance à matériel égal par ailleurs).
Value evaluate(const Position& pos);

}  // namespace fanorona
