// Fanorona engine - livre d'ouvertures (Fanoron-Tsivy, règles standard)
#pragma once

#include <string>

#include "position.h"

namespace fanorona::Book {

// Charge un livre texte (tools/book/export_book.py) : une ligne par position,
//   <coups depuis la position initiale, ou "-">\t<coup> <poids> [<coup> <poids> ...]
// Chaque ligne est rejouée depuis la position initiale : la position est retrouvée par sa clé (transpositions
// comprises). Renvoie le nombre de positions chargées (0 si erreur ; le livre précédent est alors vidé).
size_t load(const std::string& path);
size_t size();

// Coup du livre pour cette position (tirage pondéré), ou MOVE_NONE : livre vide, position inconnue, ou règles
// autres que le Fanoron-Tsivy standard (variante, vela, continuation obligatoire).
Move probe(const Position& pos);

}  // namespace fanorona::Book
