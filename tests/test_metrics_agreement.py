"""Übereinstimmungsmaße gegen veröffentlichte Referenzwerte."""

import numpy as np
import pytest
from sklearn.metrics import cohen_kappa_score

from llmanno.core.metrics.agreement import (
    brennan_prediger,
    cohen_kappa,
    fleiss_kappa,
    gwet_ac1,
    krippendorff_alpha,
    majority,
    masi_distance,
    percent_agreement,
    prevalence_bias_index,
    scott_pi,
)

# irrCAC-Datensatz cac.raw4raters (Gwet 2014); identisch mit Krippendorffs (2011) Beispiel.
RAW4RATERS = [
    [1, 1, None, 1],
    [2, 2, 3, 2],
    [3, 3, 3, 3],
    [3, 3, 3, 3],
    [2, 2, 2, 2],
    [1, 2, 3, 4],
    [4, 4, 4, 4],
    [1, 1, 2, 1],
    [2, 2, 2, 2],
    [None, 5, 5, 5],
    [None, None, 1, 1],
    [None, None, 3, None],
]


def test_irrcac_reference_values():
    # Werte aus der irrCAC-Vignette „overview“ (CRAN).
    assert percent_agreement(RAW4RATERS) == pytest.approx(0.8181818, abs=1e-6)
    assert gwet_ac1(RAW4RATERS) == pytest.approx(0.77544, abs=1e-5)
    assert fleiss_kappa(RAW4RATERS) == pytest.approx(0.76117, abs=1e-5)
    assert brennan_prediger(RAW4RATERS) == pytest.approx(0.77273, abs=1e-5)
    assert krippendorff_alpha(RAW4RATERS) == pytest.approx(0.74342, abs=1e-5)


def test_krippendorff_levels_reference_values():
    # Krippendorff (2011), Abschnitt C: α_ordinal = .815, α_interval = .849, α_ratio = .797.
    assert krippendorff_alpha(RAW4RATERS, "ordinal") == pytest.approx(0.815, abs=1e-3)
    assert krippendorff_alpha(RAW4RATERS, "interval") == pytest.approx(0.849, abs=1e-3)
    assert krippendorff_alpha(RAW4RATERS, "ratio") == pytest.approx(0.797, abs=1e-3)


def test_fleiss_wikipedia_example():
    # Fleiss' Kappa, Wikipedia-Rechenbeispiel: 10 Fälle, 14 Kodierende, κ = 0.210.
    table = [
        [0, 0, 0, 0, 14], [0, 2, 6, 4, 2], [0, 0, 3, 5, 6], [0, 3, 9, 2, 0], [2, 2, 8, 1, 1],
        [7, 7, 0, 0, 0], [3, 2, 6, 3, 0], [2, 5, 3, 2, 2], [6, 5, 2, 1, 0], [0, 2, 2, 3, 7],
    ]  # fmt: skip
    ratings = [[k for k, n in enumerate(row) for _ in range(n)] for row in table]
    assert fleiss_kappa(ratings) == pytest.approx(0.210, abs=1e-3)


# Cohen-Beispiel: 50 Fälle, 20 ja/ja, 5 ja/nein, 10 nein/ja, 15 nein/nein → κ = 0.4.
A = ["ja"] * 25 + ["nein"] * 25
B = ["ja"] * 20 + ["nein"] * 5 + ["ja"] * 10 + ["nein"] * 15


def test_cohen_scott_and_pabak_two_by_two():
    assert cohen_kappa(A, B) == pytest.approx(0.4)
    assert scott_pi(A, B) == pytest.approx((0.7 - 0.505) / 0.495)
    assert brennan_prediger(list(zip(A, B, strict=True))) == pytest.approx(0.4)  # PABAK = 2·p_o − 1


@pytest.mark.parametrize("weights", [None, "linear", "quadratic"])
def test_cohen_matches_sklearn_including_weights(weights):
    rng = np.random.default_rng(3)
    a = rng.integers(0, 4, 200).tolist()
    b = [x if rng.random() < 0.6 else int(rng.integers(0, 4)) for x in a]
    expected = cohen_kappa_score(a, b, weights=weights)
    assert cohen_kappa(a, b, weights=weights, categories=[0, 1, 2, 3]) == pytest.approx(expected)


def test_missing_values_are_skipped_pairwise():
    assert cohen_kappa(["a", None, "b", "a"], ["a", "b", None, "a"]) != cohen_kappa(["a"], ["a"])
    assert np.isnan(cohen_kappa([None], ["a"]))


def test_ac1_uses_all_possible_categories_when_given():
    ratings = [["K1", "K1"], ["K1", "K1"], ["K1", "K2"]]
    assert gwet_ac1(ratings, ["K1", "K2", "K3"]) != gwet_ac1(ratings)
    with pytest.raises(ValueError, match="außerhalb"):
        gwet_ac1(ratings, ["K1"])


def test_kappa_paradox_is_explained_by_prevalence_index():
    a = [True] * 95 + [False] * 5
    b = [True] * 94 + [False] + [True] * 4 + [False]
    prevalence, bias = prevalence_bias_index(a, b)
    # 95 % Übereinstimmung, aber p_e = .95·.98 + .05·.02 = .932, also κ = .018/.068 ≈ .265.
    assert percent_agreement(list(zip(a, b, strict=True))) == pytest.approx(0.95)
    assert cohen_kappa(a, b) == pytest.approx(0.018 / 0.068)
    assert prevalence == pytest.approx(0.93) and bias == pytest.approx(-0.03)


def test_masi_and_alpha_for_sets():
    k1, k2, k12 = frozenset({"K1"}), frozenset({"K2"}), frozenset({"K1", "K2"})
    assert masi_distance(k1, k1) == 0
    assert masi_distance(k1, k12) == pytest.approx(1 - 0.5 * 2 / 3)
    assert masi_distance(k1, k2) == 1
    perfect = [[k1, k1], [k12, k12], [k2, k2]]
    assert krippendorff_alpha(perfect, "masi") == pytest.approx(1)
    partial = [[k1, k12], [k12, k12], [k2, k2], [k1, k1]]
    assert 0 < krippendorff_alpha(partial, "masi") < krippendorff_alpha(perfect, "masi")
    assert krippendorff_alpha(partial, "masi") > krippendorff_alpha(partial, "nominal")


def test_majority():
    assert majority(["K1", "K1", "K2"]) == "K1"
    assert majority(["K1", "K2"]) is None
    assert majority([None, None]) is None
