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

// Format FNU2 (tools/nnue/train.py, classe NNUE2) : après le même accumulateur, deux petites couches denses
// hidden -> l2 -> l3 -> 1, en `nb` exemplaires choisis par le nombre total de pièces (bucket = nombre de
// seuils <= nb_pièces). Matrices transposées au chargement ([entrée][sortie]) : chaque neurone d'entrée
// non nul ajoute une ligne contiguë aux sorties (vectorisée), et les neurones nuls après ClippedReLU, très
// nombreux, sont simplement sautés.
struct Stack {
    std::vector<float> w2t, b2;  // [hidden][l2], [l2]
    std::vector<float> w3t, b3;  // [l2][l3], [l3]
    std::vector<float> w4;       // [l3]
    float b4 = 0.0f;
};
bool g_stacked = false;
int g_l2 = 0, g_l3 = 0;
std::vector<int> g_thresholds;
std::vector<Stack> g_stacks;
std::vector<float> g_h1, g_h2, g_h3;  // tampons de travail (état global comme l'accumulateur, pas thread-safe)

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

// Lit une matrice [rows][cols] (ordre de nn.Linear) et la renvoie transposée [cols][rows].
bool read_transposed(std::ifstream& f, std::vector<float>& dst, size_t rows, size_t cols) {
    std::vector<float> m(rows * cols);
    if (!read_exact(f, m.data(), m.size() * sizeof(float))) return false;
    dst.assign(rows * cols, 0.0f);
    for (size_t r = 0; r < rows; ++r)
        for (size_t c = 0; c < cols; ++c) dst[c * rows + r] = m[r * cols + c];
    return true;
}

bool read_vec(std::ifstream& f, std::vector<float>& dst, size_t n) {
    dst.assign(n, 0.0f);
    return read_exact(f, dst.data(), n * sizeof(float));
}

// Suite du chargement pour le format FNU2 (le magic est déjà lu). Ne touche à l'état global qu'une fois
// tout le fichier lu et validé.
bool load_stacked(std::ifstream& f, const std::string& path) {
    int32_t hdr[4];
    if (!read_exact(f, hdr, sizeof(hdr)) || hdr[0] <= 0 || hdr[0] > 65536 || hdr[1] <= 0 || hdr[1] > 1024 ||
        hdr[2] <= 0 || hdr[2] > 1024 || hdr[3] <= 0 || hdr[3] > 64) {
        std::cout << "info string nnue: en-tête FNU2 invalide dans " << path << std::endl;
        return false;
    }
    const size_t h = size_t(hdr[0]), l2 = size_t(hdr[1]), l3 = size_t(hdr[2]), nb = size_t(hdr[3]);
    std::vector<int32_t> thr(nb - 1);
    std::vector<float> w1t, b1;
    std::vector<Stack> stacks(nb);
    bool ok = (nb == 1 || read_exact(f, thr.data(), thr.size() * sizeof(int32_t))) &&
              read_transposed(f, w1t, h, INPUT_SIZE) && read_vec(f, b1, h);
    for (size_t b = 0; ok && b < nb; ++b) {
        Stack& s = stacks[b];
        std::vector<float> b4;
        ok = read_transposed(f, s.w2t, l2, h) && read_vec(f, s.b2, l2) && read_transposed(f, s.w3t, l3, l2) &&
             read_vec(f, s.b3, l3) && read_vec(f, s.w4, l3) && read_vec(f, b4, 1);
        if (ok) s.b4 = b4[0];
    }
    if (!ok) {
        std::cout << "info string nnue: fichier tronqué " << path << std::endl;
        return false;
    }

    g_hidden = int(h);
    g_w1t = std::move(w1t);
    g_b1 = std::move(b1);
    g_l2 = int(l2);
    g_l3 = int(l3);
    g_thresholds.assign(thr.begin(), thr.end());
    g_stacks = std::move(stacks);
    g_h1.assign(h, 0.0f);
    g_h2.assign(l2, 0.0f);
    g_h3.assign(l3, 0.0f);
    g_stacked = true;
    g_loaded = true;
    g_cache.valid = false;
    std::cout << "info string nnue: " << path << " chargé (hidden=" << h << ", couches " << l2 << "x" << l3
              << ", " << nb << " buckets)" << std::endl;
    return true;
}

// out[0..n) += x * w[0..n) : ligne contiguë, vectorisée par le compilateur.
inline void axpy(float* __restrict out, const float* __restrict w, float x, int n) {
    for (int j = 0; j < n; ++j) out[j] += x * w[j];
}

// Couches denses du format FNU2, à partir de l'accumulateur du camp au trait.
float forward_stacked(const float* acc, const Position& pos) {
    const int pieces = popcount(pos.pieces(WHITE) | pos.pieces(BLACK));
    int b = 0;
    for (int t : g_thresholds) b += pieces >= t;
    const Stack& s = g_stacks[size_t(b)];

#if defined(__AVX2__) && defined(__FMA__)
    // Chemin rapide pour la forme utilisée (l2 = 16, l3 = 32). Une boucle naïve enchaîne des
    // multiplications-additions dépendantes les unes des autres (4 cycles de latence chacune) : ici, quatre
    // neurones d'entrée à la fois vont dans quatre paires d'accumulateurs indépendantes, sans branchement.
    if (g_l2 == 16 && g_l3 == 32 && g_hidden % 8 == 0) {
        const __m256 zero = _mm256_setzero_ps(), one = _mm256_set1_ps(1.0f);
        // Dense volontairement : ~70 % des neurones sont nuls après ClippedReLU, mais ne traiter que les non nuls
        // (indices relevés par masque AVX2) a été mesuré PLUS LENT (~900k contre ~1,08M nœuds/s, net_s3) : le
        // parcours des bits du masque coûte plus en erreurs de prédiction que les multiplications évitées.
        float* h1 = g_h1.data();
        for (int i = 0; i < g_hidden; i += 8)
            _mm256_storeu_ps(h1 + i, _mm256_min_ps(_mm256_max_ps(_mm256_loadu_ps(acc + i), zero), one));

        const float* w = s.w2t.data();
        __m256 a[8];
        a[0] = _mm256_loadu_ps(s.b2.data());
        a[1] = _mm256_loadu_ps(s.b2.data() + 8);
        for (int k = 2; k < 8; ++k) a[k] = zero;
        for (int i = 0; i < g_hidden; i += 4, w += 64)
            for (int u = 0; u < 4; ++u) {
                const __m256 x = _mm256_broadcast_ss(h1 + i + u);
                a[2 * u] = _mm256_fmadd_ps(_mm256_loadu_ps(w + 16 * u), x, a[2 * u]);
                a[2 * u + 1] = _mm256_fmadd_ps(_mm256_loadu_ps(w + 16 * u + 8), x, a[2 * u + 1]);
            }
        float h2[16];
        _mm256_storeu_ps(h2, _mm256_min_ps(_mm256_max_ps(_mm256_add_ps(_mm256_add_ps(a[0], a[2]), _mm256_add_ps(a[4], a[6])), zero), one));
        _mm256_storeu_ps(h2 + 8, _mm256_min_ps(_mm256_max_ps(_mm256_add_ps(_mm256_add_ps(a[1], a[3]), _mm256_add_ps(a[5], a[7])), zero), one));

        const float* w3 = s.w3t.data();
        __m256 c[8];
        for (int k = 0; k < 4; ++k) c[k] = _mm256_loadu_ps(s.b3.data() + 8 * k), c[k + 4] = zero;
        for (int j = 0; j < 16; j += 2, w3 += 64)
            for (int u = 0; u < 2; ++u) {
                const __m256 x = _mm256_broadcast_ss(h2 + j + u);
                for (int k = 0; k < 4; ++k)
                    c[4 * u + k] = _mm256_fmadd_ps(_mm256_loadu_ps(w3 + 32 * u + 8 * k), x, c[4 * u + k]);
            }
        __m256 o = zero;
        for (int k = 0; k < 4; ++k) {
            const __m256 h3 = _mm256_min_ps(_mm256_max_ps(_mm256_add_ps(c[k], c[k + 4]), zero), one);
            o = _mm256_fmadd_ps(_mm256_loadu_ps(s.w4.data() + 8 * k), h3, o);
        }
        float lanes[8];
        _mm256_storeu_ps(lanes, o);
        float out = s.b4;
        for (float l : lanes) out += l;
        return out;
    }
#endif

    float* h2 = g_h2.data();
    std::copy(s.b2.begin(), s.b2.end(), h2);
    for (int i = 0; i < g_hidden; ++i) {
        float x = std::min(std::max(acc[i], 0.0f), 1.0f);
        if (x != 0.0f) axpy(h2, s.w2t.data() + size_t(i) * size_t(g_l2), x, g_l2);
    }
    float* h3 = g_h3.data();
    std::copy(s.b3.begin(), s.b3.end(), h3);
    for (int j = 0; j < g_l2; ++j) {
        float x = std::min(std::max(h2[j], 0.0f), 1.0f);
        if (x != 0.0f) axpy(h3, s.w3t.data() + size_t(j) * size_t(g_l3), x, g_l3);
    }
    float out = s.b4;
    for (int k = 0; k < g_l3; ++k) out += s.w4[size_t(k)] * std::min(std::max(h3[k], 0.0f), 1.0f);
    return out;
}

}  // namespace

bool load(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) {
        std::cout << "info string nnue: impossible d'ouvrir " << path << std::endl;
        return false;
    }

    char magic[4];
    if (!read_exact(f, magic, 4)) magic[0] = 0;
    if (std::string(magic, 4) == "FNU2") return load_stacked(f, path);
    if (std::string(magic, 4) != "FNUE") {
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
    g_stacked = false;
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
    if (g_stacked) return clamp_eval(int(std::lround(double(forward_stacked(acc, pos)) * SCORE_SCALE)));
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
