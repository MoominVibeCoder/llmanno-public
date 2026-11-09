"""Klassifikation, Multi-Label, Kalibrierung, Prävalenz, Bootstrap und Alt-Test."""

import numpy as np
import pytest
from sklearn import metrics as sk
from statsmodels.stats.contingency_tables import SquareTable
from statsmodels.stats.contingency_tables import mcnemar as sm_mcnemar

from llmanno.core.metrics.alt_test import alt_test
from llmanno.core.metrics.bootstrap import bootstrap
from llmanno.core.metrics.calibration import auroc, calibration_report
from llmanno.core.metrics.classification import (
    confusion_matrix,
    multi_label_report,
    selective_report,
    single_label_report,
    top_k_accuracy,
)
from llmanno.core.metrics.prevalence import mcnemar, shares, stuart_maxwell

LABELS = ["K1", "K2", "K3", "K4"]


def _sample(seed=1, n=300, noise=0.35):
    rng = np.random.default_rng(seed)
    gold = rng.choice(LABELS, n, p=[0.4, 0.3, 0.2, 0.1]).tolist()
    pred = [g if rng.random() > noise else str(rng.choice(LABELS)) for g in gold]
    return gold, pred


def test_single_label_report_matches_sklearn():
    gold, pred = _sample()
    report = single_label_report(gold, pred, LABELS)
    assert (report.confusion == sk.confusion_matrix(gold, pred, labels=LABELS)).all()
    assert report.accuracy == pytest.approx(sk.accuracy_score(gold, pred))
    assert report.balanced_accuracy == pytest.approx(sk.balanced_accuracy_score(gold, pred))
    for average in ("macro", "micro", "weighted"):
        expected = sk.f1_score(gold, pred, labels=LABELS, average=average, zero_division=0)
        assert getattr(report, f"{average}_f1") == pytest.approx(expected)
    assert report.mcc == pytest.approx(sk.matthews_corrcoef(gold, pred))
    p, r, f, s = sk.precision_recall_fscore_support(gold, pred, labels=LABELS, zero_division=0)
    for i, label in enumerate(LABELS):
        scores = report.per_class[label]
        assert (scores.precision, scores.recall, scores.f1, scores.support) == pytest.approx(
            (p[i], r[i], f[i], s[i])
        )


def test_never_predicted_code_has_zero_precision_not_an_error():
    report = single_label_report(["K1", "K2"], ["K1", "K1"], ["K1", "K2", "K3"])
    assert report.per_class["K2"].precision == 0 and report.per_class["K3"].support == 0


def test_top_k_and_selective():
    assert top_k_accuracy(["K1", "K2"], [["K2", "K1"], ["K3", "K4"]], 2) == 0.5
    report = selective_report(["K1", "K2", "K3"], ["K1", "X", "K2"], abstain="X")
    assert (report.coverage, report.abstain_rate, report.selective_accuracy) == pytest.approx(
        (2 / 3, 1 / 3, 0.5)
    )


def test_multi_label_report_matches_sklearn():
    rng = np.random.default_rng(2)
    gold = [frozenset(c for c in LABELS if rng.random() < 0.35) for _ in range(200)]
    pred = [frozenset(c for c in LABELS if (c in g) != (rng.random() < 0.15)) for g in gold]
    report = multi_label_report(gold, pred, LABELS)
    y_true = np.array([[c in g for c in LABELS] for g in gold])
    y_pred = np.array([[c in p for c in LABELS] for p in pred])
    assert report.micro_f1 == pytest.approx(sk.f1_score(y_true, y_pred, average="micro"))
    assert report.macro_f1 == pytest.approx(
        sk.f1_score(y_true, y_pred, average="macro", zero_division=0)
    )
    assert report.hamming_loss == pytest.approx(sk.hamming_loss(y_true, y_pred))
    assert report.exact_match == pytest.approx(sk.accuracy_score(y_true, y_pred))
    expected_jaccard = sk.jaccard_score(y_true, y_pred, average="samples", zero_division=1)
    assert report.jaccard == pytest.approx(expected_jaccard)
    assert report.kappa_per_class["K1"] == pytest.approx(
        sk.cohen_kappa_score(y_true[:, 0], y_pred[:, 0])
    )


def test_calibration_and_auroc():
    rng = np.random.default_rng(4)
    confidence = rng.random(500)
    correct = rng.random(500) < confidence  # perfekt kalibriert im Erwartungswert
    report = calibration_report(confidence, correct)
    assert report.ece < 0.08 and len(report.bins) == 10 and sum(b.n for b in report.bins) == 500
    assert report.auroc == pytest.approx(sk.roc_auc_score(correct, confidence))
    overconfident = calibration_report(np.full(100, 0.95), [True] * 60 + [False] * 40)
    assert overconfident.ece == pytest.approx(0.35)
    assert auroc([0.5, 0.5], [True, False]) == 0.5
    assert np.isnan(auroc([0.9], [True]))


def test_stuart_maxwell_matches_statsmodels():
    gold, pred = _sample(seed=5, noise=0.5)
    pred = ["K1" if p == "K2" and i % 3 == 0 else p for i, p in enumerate(pred)]  # Verschiebung
    ours = stuart_maxwell(gold, pred, LABELS)
    table = confusion_matrix(gold, pred, LABELS)
    reference = SquareTable(table, shift_zeros=False).homogeneity(method="stuart_maxwell")
    assert ours.statistic == pytest.approx(reference.statistic)
    assert ours.p_value == pytest.approx(reference.pvalue)
    assert ours.df == reference.df


def test_mcnemar_exact_and_asymptotic_match_statsmodels():
    for b, c in [(3, 9), (30, 12)]:
        gold = [True] * b + [False] * c + [True] * 20
        pred = [False] * b + [True] * c + [True] * 20
        table = [[20, b], [c, 0]]
        exact = b + c < 25
        reference = sm_mcnemar(table, exact=exact, correction=True)
        assert mcnemar(gold, pred).p_value == pytest.approx(reference.pvalue)


def test_shares():
    result = shares(["K1", "K1", "K2", "K2"], ["K1", "K1", "K1", "K2"], ["K1", "K2"])
    assert result["K1"].difference == pytest.approx(0.25)


def test_bootstrap_is_reproducible_and_covers_the_estimate():
    values = np.random.default_rng(0).normal(size=200)

    def mean(idx):
        return float(values[idx].mean())

    first = bootstrap(mean, 200, seed=1)
    assert first == bootstrap(mean, 200, seed=1)
    assert first.low < first.value < first.high
    assert first.high - first.low == pytest.approx(2 * 1.96 / np.sqrt(200), rel=0.2)
    clustered = bootstrap(mean, 200, clusters=[i // 20 for i in range(200)], seed=1)
    assert clustered.resamples == 1000


def _humans(llm_noise, seed=0, n=120):
    rng = np.random.default_rng(seed)
    truth = rng.choice(LABELS, n).tolist()

    def noisy(rate):
        return [t if rng.random() > rate else str(rng.choice(LABELS)) for t in truth]

    humans = {name: noisy(0.2) for name in ("A", "B", "C", "D")}
    return noisy(llm_noise), humans


def test_alt_test_passes_for_a_good_llm_and_fails_for_a_bad_one():
    good, humans = _humans(llm_noise=0.15)
    result = alt_test(good, humans, epsilon=0.1)
    assert result.passed and result.winning_rate >= 0.5
    assert all(a.test == "t-Test" for a in result.annotators)
    bad, humans = _humans(llm_noise=0.9)
    result = alt_test(bad, humans, epsilon=0.1)
    assert not result.passed and result.advantage_probability < 0.6


def test_alt_test_needs_three_humans_and_uses_wilcoxon_for_small_samples():
    with pytest.raises(ValueError, match="mindestens 3"):
        alt_test(["K1"], {"A": ["K1"], "B": ["K1"]})
    llm, humans = _humans(llm_noise=0.15, n=20)
    assert {a.test for a in alt_test(llm, humans).annotators} == {"Wilcoxon"}
