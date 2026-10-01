#include "nnue.h"

#include <algorithm>
#if defined(__AVX2__)
#include <immintrin.h>
#endif
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <vector>

#include "evaluate.h"

namespace fanorona::NNUE {

namespace {

constexpr int INPUT_SIZE = 2 * SQUARE_NB;  // 90 : cases du camp au trait (0..44) + adverses (45..89)
// Doit correspondre EXACTEMENT à SCORE_SCALE dans tools/nnue/train.py : la sortie du réseau
// est un logit en unités score/SCORE_SCALE, reconverti ici en centipions.
constexpr double SCORE_SCALE = 400.0;

bool g_loaded = false;
bool g_wantEnabled = false;
int g_hidden = 0;
// W1 rangée par entrée : g_w1t[col * hidden + i] = poids (neurone i, entrée col). Le fichier stocke la matrice de
// nn.Linear (neurone par neurone) ; on la transpose au chargement pour que l'ajout ou le retrait d'une entrée (une
// case) parcoure 256 flottants contigus, vectorisés par le compilateur, au lieu de sauter de 90 en 90.
std::vector<float> g_w1t;
std::vector<float> g_b1;  // [hidden]
std::vector<float> g_w2;  // [hidden]        (= nn.Linear(hidden, 1).weight, une seule ligne)
float g_b2 = 0.0f;

// Accumulateur incrémental : accum[c] = b1 + W_own @ pieces(c) + W_opp @ pieces(~c), c'est-à-
// dire "la pré-activation telle qu'elle serait si c'était le tour de c". À l'évaluation, on
// lit directement accum[pos.sideToMove]. Pas d'accumulateur par nœud de recherche façon
// StateInfo (Position est copy-make, sans do/undo explicite) : à la place, on garde le DERNIER
// accum calculé et on le met à jour par diff XOR des bitboards à chaque appel — un coup
// (même une chaîne de captures) est une modification atomique des bitboards, donc le diff
// reconstruit exactement les cases qui ont changé, sans avoir besoin de connaître "le coup"
// lui-même. Coût proportionnel à la taille du diff : quasi gratuit entre deux positions
// adjacentes dans l'arbre de recherche (cas courant), dégrade proprement vers un coût
// équivalent au recalcul complet si les deux positions n'ont rien en commun (rare, ex. premier
// appel, ou saut entre branches très éloignées).
// Note : état global, donc PAS thread-safe. À revoir (un cache par thread) si Lazy SMP
// (feuille de route CLAUDE.md, point 5) est implémenté un jour.
struct AccumCache {
    bool valid = false;
    Bitboard byColor[COLOR_NB] = {0, 0};
    std::vector<float> acc[COLOR_NB];
};
AccumCache g_cache;

bool read_exact(std::ifstream& f, void* dst, size_t bytes) {
    f.read(reinterpret_cast<char*>(dst), std::streamsize(bytes));
    return bool(f) && size_t(f.gcount()) == bytes;
}

// Ajoute (sign=+1) ou retire (sign=-1) la contribution de la colonne `col` (0..89) de W_own à
// l'accumulateur `acc`.
void add_col(std::vector<float>& acc, int col, float sign) {
    float* __restrict a = acc.data();
    const float* __restrict w = g_w1t.data() + size_t(col) * size_t(g_hidden);
    const int n = g_hidden;
    if (sign > 0)
        for (int i = 0; i < n; ++i) a[i] += w[i];
    else
        for (int i = 0; i < n; ++i) a[i] -= w[i];
}

void recompute_from(const Position& pos) {
    g_cache.acc[WHITE].assign(g_b1.begin(), g_b1.end());
    g_cache.acc[BLACK].assign(g_b1.begin(), g_b1.end());
    for (Bitboard b = pos.pieces(WHITE); b;) {
        int sq = pop_lsb(b);
        add_col(g_cache.acc[WHITE], sq, 1.0f);              // blanc = "own" dans accum[WHITE]
        add_col(g_cache.acc[BLACK], SQUARE_NB + sq, 1.0f);  // blanc = "opp" dans accum[BLACK]
    }
    for (Bitboard b = pos.pieces(BLACK); b;) {
        int sq = pop_lsb(b);
        add_col(g_cache.acc[BLACK], sq, 1.0f);              // noir = "own" dans accum[BLACK]
        add_col(g_cache.acc[WHITE], SQUARE_NB + sq, 1.0f);  // noir = "opp" dans accum[WHITE]
    }
    g_cache.byColor[WHITE] = pos.pieces(WHITE);
    g_cache.byColor[BLACK] = pos.pieces(BLACK);
    g_cache.valid = true;
}

void update_incremental(const Position& pos) {
    Bitboard newWhite = pos.pieces(WHITE), newBlack = pos.pieces(BLACK);
    Bitboard addedWhite = newWhite & ~g_cache.byColor[WHITE];
    Bitboard removedWhite = g_cache.byColor[WHITE] & ~newWhite;
    Bitboard addedBlack = newBlack & ~g_cache.byColor[BLACK];
    Bitboard removedBlack = g_cache.byColor[BLACK] & ~newBlack;

    for (Bitboard b = addedWhite; b;) {
        int sq = pop_lsb(b);
        add_col(g_cache.acc[WHITE], sq, 1.0f);
        add_col(g_cache.acc[BLACK], SQUARE_NB + sq, 1.0f);
    }
    for (Bitboard b = removedWhite; b;) {
        int sq = pop_lsb(b);
        add_col(g_cache.acc[WHITE], sq, -1.0f);
        add_col(g_cache.acc[BLACK], SQUARE_NB + sq, -1.0f);
    }
    for (Bitboard b = addedBlack; b;) {
        int sq = pop_lsb(b);
        add_col(g_cache.acc[BLACK], sq, 1.0f);
        add_col(g_cache.acc[WHITE], SQUARE_NB + sq, 1.0f);
    }
    for (Bitboard b = removedBlack; b;) {
        int sq = pop_lsb(b);
        add_col(g_cache.acc[BLACK], sq, -1.0f);
        add_col(g_cache.acc[WHITE], SQUARE_NB + sq, -1.0f);
    }

    g_cache.byColor[WHITE] = newWhite;
    g_cache.byColor[BLACK] = newBlack;
}

}  // namespace

bool load(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) {
        std::cout << "info string nnue: impossible d'ouvrir " << path << std::endl;
        return false;
    }

    char magic[4];
    if (!read_exact(f, magic, 4) || std::string(magic, 4) != "FNUE") {
        std::cout << "info string nnue: fichier invalide (magic) " << path << std::endl;
        return false;
    }
    int32_t hidden = 0;
    if (!read_exact(f, &hidden, sizeof(hidden)) || hidden <= 0 || hidden > 65536) {
        std::cout << "info string nnue: taille de couche cachée invalide dans " << path << std::endl;
        return false;
    }

    size_t h = size_t(hidden);  // évite le "most vexing parse" de vector<float> w2(size_t(hidden))
    std::vector<float> w1(h * INPUT_SIZE), b1(h), w2(h);
    float b2 = 0.0f;
    bool ok = read_exact(f, w1.data(), w1.size() * sizeof(float)) &&
              read_exact(f, b1.data(), b1.size() * sizeof(float)) &&
              read_exact(f, w2.data(), w2.size() * sizeof(float)) && read_exact(f, &b2, sizeof(b2));
    if (!ok) {
        std::cout << "info string nnue: fichier tronqué " << path << std::endl;
        return false;
    }

    g_hidden = hidden;
    g_w1t.assign(h * INPUT_SIZE, 0.0f);
    for (size_t i = 0; i < h; ++i)
        for (size_t c = 0; c < size_t(INPUT_SIZE); ++c) g_w1t[c * h + i] = w1[i * INPUT_SIZE + c];
    g_b1 = std::move(b1);
    g_w2 = std::move(w2);
    g_b2 = b2;
    g_loaded = true;
    g_cache.valid = false;  // les poids ont changé, l'accumulateur mis en cache ne vaut plus rien
    std::cout << "info string nnue: " << path << " chargé (hidden=" << hidden << ")" << std::endl;
    return true;
}

bool enabled() { return g_loaded && g_wantEnabled; }
void set_enabled(bool on) { g_wantEnabled = on; }
void new_game() { g_cache.valid = false; }

Value evaluate(const Position& pos) {
    if (!g_cache.valid) recompute_from(pos);
    else update_incremental(pos);

    const float* __restrict acc = g_cache.acc[pos.sideToMove].data();
    const float* __restrict w2 = g_w2.data();
    // Couche de sortie : 8 sommes partielles indépendantes (une par voie AVX2). Une réduction en un seul flottant
    // n'est pas vectorisée sans -ffast-math (l'ordre des additions changerait) : c'était une chaîne de 256
    // multiplications-additions dépendantes, l'essentiel du coût de l'évaluation.
    const int n = g_hidden;
    float lanes[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    int i = 0;
#if defined(__AVX2__) && defined(__FMA__)
    {
        const __m256 zero = _mm256_setzero_ps(), one = _mm256_set1_ps(1.0f);
        __m256 s0 = _mm256_setzero_ps(), s1 = _mm256_setzero_ps();
        for (; i + 16 <= n; i += 16) {
            __m256 x0 = _mm256_min_ps(_mm256_max_ps(_mm256_loadu_ps(acc + i), zero), one);
            __m256 x1 = _mm256_min_ps(_mm256_max_ps(_mm256_loadu_ps(acc + i + 8), zero), one);
            s0 = _mm256_fmadd_ps(_mm256_loadu_ps(w2 + i), x0, s0);
            s1 = _mm256_fmadd_ps(_mm256_loadu_ps(w2 + i + 8), x1, s1);
        }
        _mm256_storeu_ps(lanes, _mm256_add_ps(s0, s1));
    }
#endif
    for (; i + 8 <= n; i += 8)
        for (int j = 0; j < 8; ++j) {
            float x = acc[i + j];
            x = x < 0.0f ? 0.0f : x > 1.0f ? 1.0f : x;  // ClippedReLU
            lanes[j] += w2[i + j] * x;
        }
    float out = 0.0f;
    for (; i < n; ++i) out += w2[i] * std::min(std::max(acc[i], 0.0f), 1.0f);
    for (float l : lanes) out += l;
    return clamp_eval(int(std::lround((double(out) + double(g_b2)) * SCORE_SCALE)));
}

}  // namespace fanorona::NNUE
