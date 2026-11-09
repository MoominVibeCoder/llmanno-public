"""Dawid-Skene (1979): Trefferquoten der Kodierenden ohne Gold schätzen, Labels danach gewichten.

Ein-Münz-Variante (eine Trefferquote je Modell, Fehler gleich auf die anderen Codes verteilt;
in Crowd-Kit „OneCoinDawidSkene“): Das volle Modell mit einer Konfusionsmatrix je Modell fand in
Simulationen mit drei Modellen schlechtere lokale Optima, die Ein-Münz-Variante lag nahe am
Bayes-optimalen Ergebnis. EM über alle Einheiten, Start mit den Stimmanteilen.

Annahme: Die Modelle irren unabhängig voneinander, gegeben den wahren Code. Bei LLMs ist sie
meist verletzt (ähnliche Trainingsdaten, ähnliche Fehler); dann überschätzt das Verfahren seine
Sicherheit. Deshalb nur als Gegenprobe zur gleichgewichteten Mittelung.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DawidSkene:
    labels: list[str]  # geschätzter Code je Einheit
    accuracy: dict[str, float]  # geschätzte Trefferquote je Modell


def dawid_skene(
    votes: Sequence[Mapping[str, Mapping[str, float]]],
    classes: Sequence[str],
    *,
    max_iterations: int = 1000,
    tolerance: float = 1e-8,
) -> DawidSkene:
    """`votes`: je Einheit Modell → Anteil der Läufe je Code (wie die Self-Consistency; eine
    einzelne Stimme ist {Code: 1}). Modelle ohne Stimme fehlen einfach. Mit Anteilen und gleichen
    Trefferquoten entscheidet es wie das gleich gewichtete Panel, bis auf die Code-Häufigkeit."""
    models = sorted({m for row in votes for m in row})
    index = {c: k for k, c in enumerate(classes)}
    k = len(classes)
    seen = np.zeros((len(votes), len(models), k))  # Einheit × Modell × Code
    for i, row in enumerate(votes):
        for j, model in enumerate(models):
            for code, share in row.get(model, {}).items():
                seen[i, j, index[code]] = share
    posterior = seen.sum(axis=1) + 1e-9
    posterior /= posterior.sum(axis=1, keepdims=True)
    for _ in range(max_iterations):
        prior = posterior.mean(axis=0) + 1e-12
        hits = np.einsum("ik,ijk->j", posterior, seen)
        accuracy = np.clip(hits / seen.sum(axis=(0, 2)), 1e-3, 1 - 1e-3)
        # Gewicht einer Stimme: log a(K−1)/(1−a); fehlende Stimmen ändern nichts
        weight = np.log(accuracy * (k - 1) / (1 - accuracy))
        log_post = np.log(prior) + np.einsum("ijk,j->ik", seen, weight)
        new = np.exp(log_post - log_post.max(axis=1, keepdims=True))
        new /= new.sum(axis=1, keepdims=True)
        change = np.abs(new - posterior).max()
        posterior = new
        if change < tolerance:
            break
    labels = [classes[int(c)] for c in posterior.argmax(axis=1)]
    return DawidSkene(labels, dict(zip(models, map(float, accuracy), strict=True)))
