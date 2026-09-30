// Fanorona engine - représentation de la position
#pragma once

#include <string>

#include "bitboard.h"
#include "types.h"

namespace fanorona {

namespace Zobrist {
extern Key psq[COLOR_NB][SQUARE_NB];
extern Key side;
void init();
}  // namespace Zobrist

// Variantes : Fanoron-Tsivy (9 x 5, le jeu complet), Fanoron-Dimy (5 x 5, mêmes règles), Fanoron-Telo (3 x 3,
// jeu d'alignement sans capture : module séparé, voir telo.h).
enum class Variant { Tsivy, Dimy, Telo };

// Paramètres de règles (modifiables par "setoption"). Globales statiques : ne pas les changer pendant une recherche.
struct Rules {
    // Si vrai, une séquence de captures doit être poursuivie tant que possible.
    // Par défaut (règle usuelle) le joueur peut s'arrêter quand il veut.
    static inline bool mandatoryContinuation = false;
    // Nulle après ce nombre de demi-coups consécutifs sans capture.
    static inline int noCaptureLimit = 100;
    static inline Variant variant = Variant::Tsivy;
    // Partie « vela » (handicap, Fanoron-Tsivy) : camp bénéficiaire, qui commence (COLOR_NB = pas de vela).
    // Phase 1, tant que l'autre camp a plus de VELA_REMAINING pièces : le bénéficiaire DOIT capturer exactement
    // une pièce par tour (la plus proche de la ligne, sans enchaînement) ; l'autre camp ne fait que des paika, en
    // laissant si possible une capture au bénéficiaire. Le bénéficiaire sans capture possible en phase 1 a perdu
    // (il n'a aucun coup légal). Ensuite (phase 2), règles normales.
    static inline Color vela = COLOR_NB;
    static constexpr int VELA_REMAINING = 5;
};

// Position de départ de la variante courante (le bénéficiaire d'une vela a le trait).
std::string start_fen();
// Nombre total de pièces au départ (22 + 22 en Tsivy, 12 + 12 en Dimy).
int start_pieces();
// Applique la variante : géométrie du plateau et tables qui en dépendent.
void set_variant(Variant v);

// La position tient dans 32 octets : on utilise la stratégie "copy-make".
struct Position {
    Bitboard byColor[COLOR_NB];
    Color sideToMove;
    Key key;
    int rule50;   // demi-coups depuis la dernière capture
    int gamePly;

    bool set(const std::string& fen);
    std::string fen() const;
    std::string pretty() const;

    Bitboard pieces(Color c) const { return byColor[c]; }
    Bitboard empty() const { return ~(byColor[WHITE] | byColor[BLACK]) & Board::mask; }
    // Vela, phase 1 en cours ?
    bool vela_phase1() const {
        return Rules::vela != COLOR_NB && popcount(byColor[~Rules::vela]) > Rules::VELA_REMAINING;
    }
    Key compute_key() const;

    void do_move(Move m);
    void do_null_move();
};

}  // namespace fanorona
