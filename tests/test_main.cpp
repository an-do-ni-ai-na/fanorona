// Tests des règles, de la génération des coups et de la recherche.
#include <iostream>
#include <random>
#include <string>

#include "bitboard.h"
#include "movegen.h"
#include "position.h"
#include "search.h"
#include "telo.h"
#include "tt.h"

using namespace fanorona;

static int failures = 0;
#define CHECK(cond)                                                                   \
    do {                                                                              \
        if (!(cond)) {                                                                \
            std::cerr << __FILE__ << ":" << __LINE__ << ": CHECK failed: " #cond "\n"; \
            ++failures;                                                               \
        }                                                                             \
    } while (0)

static Position from_fen(const std::string& fen) {
    Position p;
    bool ok = p.set(fen);
    CHECK(ok);
    return p;
}

static int count_moves(const Position& p) {
    MoveList l;
    generate_moves(p, l);
    return l.size;
}

static Bitboard bb(std::initializer_list<const char*> squares) {
    Bitboard b = 0;
    for (auto s : squares) b |= square_bb(string_to_square(s));
    return b;
}

// Rotation de 180° + échange des couleurs : position équivalente.
static Position mirrored(const Position& p) {
    Position m{};
    for (int c = 0; c < COLOR_NB; ++c)
        for (Bitboard b = p.byColor[c]; b;) m.byColor[1 - c] |= square_bb(SQUARE_NB - 1 - pop_lsb(b));
    m.sideToMove = ~p.sideToMove;
    m.rule50 = p.rule50;
    m.gamePly = p.gamePly;
    m.key = m.compute_key();
    return m;
}

static void test_perft_startpos() {
    Position p = from_fen(start_fen());
    const uint64_t expected[] = {1, 5, 39, 724, 18026, 431830};
    for (int d = 1; d <= 5; ++d) CHECK(perft(p, d) == expected[d]);
}

static void test_opening_moves() {
    Position p = from_fen(start_fen());
    CHECK(parse_move(p, "d3e3A") != MOVE_NONE);
    CHECK(parse_move(p, "d3e3W") != MOVE_NONE);
    CHECK(parse_move(p, "e2e3A") != MOVE_NONE);
    CHECK(parse_move(p, "d3e3") == MOVE_NONE);  // ambigu (approche ou retrait)
    CHECK(parse_move(p, "e2e3") == parse_move(p, "e2e3A"));
    Move m = parse_move(p, "d3e3A");
    CHECK(move_captured(m) == bb({"f3"}));
    m = parse_move(p, "d3e3W");
    CHECK(move_captured(m) == bb({"c3"}));
    m = parse_move(p, "e2e3A");
    CHECK(move_captured(m) == bb({"e4", "e5"}));  // la ligne entière est capturée
}

static void test_approach_and_withdrawal() {
    // c3 noir, d3 blanc, f3 noir : d3-e3 peut capturer par approche (f3) OU retrait (c3).
    Position p = from_fen("9/9/2BW1B3/9/9 w");
    CHECK(count_moves(p) == 2);
    CHECK(move_captured(parse_move(p, "d3e3A")) == bb({"f3"}));
    CHECK(move_captured(parse_move(p, "d3e3W")) == bb({"c3"}));

    // Capture d'une ligne contiguë, arrêtée par une case vide.
    p = from_fen("9/9/1B1W1BB2/9/9 w");
    CHECK(move_captured(parse_move(p, "d3e3A")) == bb({"f3", "g3"}));
    CHECK(count_moves(p) == 2);  // d3e3A et d3c3A
}

static void test_capture_chain() {
    // c1 blanc, e1 noir, d3 noir : c1-d1 (approche e1) puis d1-d2 (approche d3).
    Position p = from_fen("9/9/3B5/9/2W1B4 w");
    CHECK(count_moves(p) == 2);  // on peut s'arrêter après la première capture
    Move m = parse_move(p, "c1d1Ad2A");
    CHECK(m != MOVE_NONE && move_captured(m) == bb({"e1", "d3"}) && move_to(m) == string_to_square("d2"));

    Rules::mandatoryContinuation = true;
    CHECK(count_moves(p) == 1);
    Rules::mandatoryContinuation = false;
}

static void test_same_direction_forbidden() {
    // b1 blanc recule vers c1 en capturant a1 ; continuer vers d1 (même
    // direction) capturerait e1 par approche, mais c'est interdit.
    Position p = from_fen("9/9/9/9/BW2B4 w");
    CHECK(count_moves(p) == 1);
    CHECK(parse_move(p, "b1c1W") != MOVE_NONE);
}

static void test_visited_forbidden() {
    // b2 blanc : b2-c2 (retrait, capture a2) puis c2-c1 (approche impossible),
    // on vérifie que la pièce ne peut jamais revenir sur b2.
    Position p = from_fen("9/9/9/B1W1B4/9 w");
    for (const auto& dm : generate_detailed(p)) {
        std::string s = dm.notation;
        CHECK(s.find("c2", 2) == std::string::npos);  // c2 est la case de départ
    }
}

static void test_paika_only_without_capture() {
    Position p = from_fen("9/9/9/9/W7B w");
    CHECK(count_moves(p) == 3);  // a1 -> a2, b1, b2
    MoveList l;
    generate_moves(p, l);
    for (Move m : l) CHECK(!is_capture(m));

    // Dès qu'une capture existe, les déplacements simples sont interdits.
    p = from_fen("9/9/9/9/W1B6 w");
    generate_moves(p, l);
    CHECK(l.size == 1 && is_capture(l.moves[0]));
}

static void test_diagonals_only_on_strong_points() {
    Position p = from_fen("9/9/9/9/1W7 w");  // b1 : point faible
    CHECK(count_moves(p) == 3);              // a1, c1, b2 : pas de diagonale
    p = from_fen("9/9/4W4/9/9 w");           // e3 : point fort, 8 voisins
    CHECK(count_moves(p) == 8);
}

static void test_symmetry_and_keys() {
    std::mt19937_64 rng(42);
    for (int game = 0; game < 30; ++game) {
        Position p = from_fen(start_fen());
        for (int ply = 0; ply < 40; ++ply) {
            CHECK(p.key == p.compute_key());
            Position q = from_fen(p.fen());
            CHECK(q.key == p.key && q.fen() == p.fen());
            if (ply % 10 == 5) CHECK(perft(p, 3) == perft(mirrored(p), 3));
            MoveList l;
            generate_moves(p, l);
            if (!l.size) break;
            for (Move m : l) CHECK(parse_move(p, move_to_string(p, m)) == m);
            p.do_move(l.moves[rng() % l.size]);
        }
    }
}

static void test_search() {
    // Gain immédiat : a1-b1 capture la dernière pièce noire.
    Position p = from_fen("9/9/9/9/W1B6 w");
    SearchLimits l;
    l.depth = 6;
    auto r = Search::think(p, {p.key}, l, false);
    CHECK(r.bestMove == parse_move(p, "a1b1A"));
    CHECK(r.score == VALUE_MATE - 1);

    // Plus de pièces : défaite, aucun coup.
    p = from_fen("9/9/9/9/W8 b");
    r = Search::think(p, {p.key}, l, false);
    CHECK(r.bestMove == MOVE_NONE);

    // La recherche doit préférer la grosse capture (2 pièces) à la petite.
    p = from_fen("9/9/1B1W1BB2/9/9 w");
    l.depth = 4;
    r = Search::think(p, {p.key}, l, false);
    CHECK(move_captured(r.bestMove) == bb({"f3", "g3"}));

    // Recherche depuis la position initiale : coup légal renvoyé.
    p = from_fen(start_fen());
    l.depth = 6;
    r = Search::think(p, {p.key}, l, false);
    MoveList ml;
    generate_moves(p, ml);
    CHECK(ml.contains(r.bestMove));
}


// ---------- Fanoron-Dimy (5 x 5) ----------
static Position mirrored5(const Position& p) {
    Position m{};
    for (int c = 0; c < COLOR_NB; ++c)
        for (Bitboard b = p.byColor[c]; b;) {
            int s = pop_lsb(b);
            m.byColor[1 - c] |= square_bb(make_square(4 - file_of(s), 4 - rank_of(s)));
        }
    m.sideToMove = ~p.sideToMove;
    m.key = m.compute_key();
    return m;
}

static void test_dimy() {
    set_variant(Variant::Dimy);
    Position p = from_fen(start_fen());
    CHECK(popcount(p.pieces(WHITE)) == 12 && popcount(p.pieces(BLACK)) == 12);
    CHECK(p.fen() == start_fen());
    CHECK((p.empty() & ~Board::mask) == 0);
    CHECK(string_to_square("f1") == SQ_NONE && string_to_square("e5") != SQ_NONE);
    // Symétrie : la position de départ est son propre miroir (rotation 180° + couleurs).
    Position m = mirrored5(p);
    for (int d = 1; d <= 4; ++d) CHECK(perft(p, d) == perft(m, d));
    // Aucun coup ne sort du 5 x 5, sur des parties aléatoires.
    std::mt19937_64 rng(5);
    for (int g = 0; g < 50; ++g) {
        Position q = p;
        for (int ply = 0; ply < 60; ++ply) {
            MoveList l;
            generate_moves(q, l);
            if (!l.size) break;
            for (Move mv : l) CHECK((square_bb(move_to(mv)) | move_captured(mv)) & Board::mask);
            q.do_move(l.moves[rng() % l.size]);
            CHECK(((q.pieces(WHITE) | q.pieces(BLACK)) & ~Board::mask) == 0);
        }
    }
    std::cout << "perft Dimy :";
    for (int d = 1; d <= 5; ++d) std::cout << ' ' << perft(p, d);
    std::cout << '\n';
    set_variant(Variant::Tsivy);
}

// ---------- Vela ----------
static void test_vela() {
    set_variant(Variant::Tsivy);
    Rules::vela = WHITE;
    // Départ : le bénéficiaire a le trait, chaque coup capture exactement une pièce.
    Position p = from_fen(start_fen());
    MoveList l;
    generate_moves(p, l);
    CHECK(l.size > 0);
    for (Move m : l) CHECK(popcount(move_captured(m)) == 1);

    // Une seule pièce prise, la plus proche, même si la ligne continue ; pas d'enchaînement.
    p = from_fen("BBBBBB3/9/9/9/W1BB5 w");
    CHECK(count_moves(p) == 1);
    CHECK(parse_move(p, "a1b1A") != MOVE_NONE && move_captured(parse_move(p, "a1b1A")) == bb({"c1"}));

    // Bénéficiaire sans capture : aucun coup, donc perdu.
    p = from_fen("BBBBBB3/9/9/9/W8 w");
    CHECK(count_moves(p) == 0);

    // Camp handicapé : paika seulement (même si une capture existe), en laissant une capture au bénéficiaire.
    p = from_fen("BBBBBB3/9/9/9/W1B6 b");
    generate_moves(p, l);
    CHECK(l.size > 0);
    for (Move m : l) {
        CHECK(!is_capture(m));
        Position c = p;
        c.do_move(m);
        CHECK(capturers(c.pieces(WHITE), c.pieces(BLACK)) != 0);
    }

    // Phase 2 (5 pièces restantes) : règles normales, toute la ligne est prise.
    p = from_fen("BBB6/9/9/9/W1BB5 w");
    CHECK(move_captured(parse_move(p, "a1b1A")) == bb({"c1", "d1"}));
    Rules::vela = COLOR_NB;
}

// ---------- Fanoron-Telo (3 x 3) ----------
static void test_telo() {
    Telo::init();
    Telo::State s;
    CHECK(Telo::set_fen(s, "3/3/3 w 0 1"));
    const uint64_t expected[] = {1, 9, 72, 504, 3024, 15120};
    for (int d = 1; d <= 5; ++d) CHECK(Telo::perft(s, d) == expected[d]);
    // Alignement pendant la pose : gain en 1.
    CHECK(Telo::set_fen(s, "WW1/BB1/3 w"));
    CHECK(Telo::solved(s) == 1);
    CHECK(Telo::set_fen(s, "WWW/BB1/3 b"));
    CHECK(Telo::status(s, {s.key()}) == "white wins");
    // Déplacements : diagonales seulement depuis les points forts (a3 : 1 coup, b2 : 3, b1 point faible : 0).
    CHECK(Telo::set_fen(s, "WB1/1W1/BWB w"));
    CHECK(Telo::moves(s).size() == 4);
    CHECK(Telo::set_fen(s, "3/3/3 w 0 1"));
    std::cout << "Fanoron-Telo, valeur exacte du départ : " << Telo::solved(s) << '\n';
}

int main() {
    init_bitboards();
    Zobrist::init();
    Search::init();
    TT.resize(16);

    test_perft_startpos();
    test_opening_moves();
    test_approach_and_withdrawal();
    test_capture_chain();
    test_same_direction_forbidden();
    test_visited_forbidden();
    test_paika_only_without_capture();
    test_diagonals_only_on_strong_points();
    test_symmetry_and_keys();
    test_search();
    test_dimy();
    test_vela();
    test_telo();

    if (failures) {
        std::cerr << failures << " test(s) en échec\n";
        return 1;
    }
    std::cout << "Tous les tests passent.\n";
    return 0;
}
