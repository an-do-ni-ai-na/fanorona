"""Test séquentiel SPRT (Sequential Probability Ratio Test) pour comparer deux moteurs.

Modèle : chaque partie donne un score individuel x in {0, 0.5, 1} (perte/nulle/victoire du
moteur testé, "engine1"). On teste H0 : Elo réel <= elo0  contre H1 : Elo réel >= elo1, en
approximant la moyenne des scores par une gaussienne de variance estimée sur l'échantillon
(approche standard des testeurs de moteurs, cf. fishtest/cutechess-cli) :

    LLR(n) = n * (mu1 - mu0) / var * (xbar - (mu0 + mu1) / 2)

où mu(elo) = 1 / (1 + 10^(-elo/400)) est le score attendu correspondant à un écart d'Elo.
LLR(n) est la log-vraisemblance de n observations gaussiennes iid de variance `var` sous
H1 : N(mu1, var) contre H0 : N(mu0, var), évaluée à la moyenne empirique xbar.
On arrête dès que LLR sort de [log(beta/(1-alpha)), log((1-beta)/alpha)] (bornes de Wald).

Mode PENTANOMIAL (par défaut dans match.py, comme fishtest) : chaque ouverture est jouée deux fois, couleurs
inversées, et les deux parties ne sont PAS indépendantes. Au Fanorona l'avantage du trait est énorme (les blancs
gagnent ~54 % des parties entre réseaux proches, les noirs ~12 %) : ~58 % des paires finissent 1-1, chaque moteur
gagnant avec les blancs. Le modèle par parties surestime alors la variance d'un facteur ~8 et le test réclame ~7x
trop de parties (rejoué sur les journaux : net_v6 contre net_v3, H1 en 9823 parties, l'aurait été en ~1400 par
paires). Même formule, appliquée aux scores de paire p = (x1 + x2) / 2 in {0, 1/4, 1/2, 3/4, 1}, n = nombre de
paires terminées et var = variance des scores de paire.
"""
import math


def elo_to_score(elo):
    return 1.0 / (1.0 + 10.0 ** (-elo / 400.0))


class Sprt:
    # Pas de décision en mode paires avant ce nombre de paires : sur quelques paires, la variance estimée peut être
    # minuscule par hasard et faire exploser le LLR (l'approximation gaussienne ne vaut que pour n assez grand).
    MIN_PAIRS = 50

    def __init__(self, elo0, elo1, alpha=0.05, beta=0.05, pentanomial=True):
        self.elo0 = elo0
        self.elo1 = elo1
        self.mu0 = elo_to_score(elo0)
        self.mu1 = elo_to_score(elo1)
        self.lower = math.log(beta / (1 - alpha))
        self.upper = math.log((1 - beta) / alpha)
        self.wins = 0
        self.draws = 0
        self.losses = 0
        self.pentanomial = pentanomial
        self.pairs = [0, 0, 0, 0, 0]  # paires de score 0, 1/4, 1/2, 3/4, 1 (LL, LD, DD ou WL, WD, WW)

    @property
    def n(self):
        return self.wins + self.draws + self.losses

    def add_result(self, result):
        """result: 'win', 'draw' ou 'loss' du point de vue d'engine1."""
        if result == "win":
            self.wins += 1
        elif result == "draw":
            self.draws += 1
        elif result == "loss":
            self.losses += 1
        else:
            raise ValueError(result)

    def add_pair(self, x1, x2):
        """Scores (0, 0.5 ou 1) d'engine1 dans les deux parties d'une même ouverture."""
        self.pairs[round(2 * (x1 + x2))] += 1

    @property
    def n_pairs(self):
        return sum(self.pairs)

    def pair_stats(self):
        n = self.n_pairs
        if n == 0:
            return 0.0, 0.0, 0
        mean = sum(k / 4 * c for k, c in enumerate(self.pairs)) / n
        var = sum(c * (k / 4 - mean) ** 2 for k, c in enumerate(self.pairs)) / n
        return mean, var, n

    def stats(self):
        n = self.n
        if n == 0:
            return 0.0, 0.0, 0
        xbar = (self.wins + 0.5 * self.draws) / n
        var = (
            self.wins * (1 - xbar) ** 2 + self.draws * (0.5 - xbar) ** 2 + self.losses * (0 - xbar) ** 2
        ) / n
        return xbar, var, n

    def llr(self):
        xbar, var, n = self.pair_stats() if self.pentanomial else self.stats()
        if n < 2 or var <= 1e-9:
            return 0.0
        return n * (self.mu1 - self.mu0) / var * (xbar - (self.mu0 + self.mu1) / 2)

    def decision(self):
        """'h0' (rejeter engine1, Elo <= elo0), 'h1' (accepter, Elo >= elo1) ou None (continuer)."""
        if self.pentanomial and self.n_pairs < self.MIN_PAIRS:
            return None
        llr = self.llr()
        if llr <= self.lower:
            return "h0"
        if llr >= self.upper:
            return "h1"
        return None
