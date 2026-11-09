"""GUIDE-LLM-Checkliste (Feuerriegel et al. 2026), vorausgefüllt aus Projekt und Audit-Log.

Feuerriegel, S. et al. (2026). A reporting checklist for large language models in behavioural
science. Nature Human Behaviour, doi:10.1038/s41562-026-02492-7; https://llm-checklist.com.
14 Kernpunkte (A.1–G.1) und empfohlene Zusatzangaben. Was das Programm nicht wissen kann, ist
mit „→ ergänzen“ markiert. Sprache: die aktuelle Ausgabesprache (i18n.output_language).
"""

from __future__ import annotations

from importlib.metadata import version

from llmanno.core.demo import DEMO_PROVIDER, demo_note, is_demo
from llmanno.core.engine import prepare_prompts
from llmanno.core.evaluation import EvaluationReport
from llmanno.core.export.prereg import thinking_label, unit_mode_label
from llmanno.core.export.report import (
    _n,
    _reference,
    _served,
    _temperature,
    model_size,
    output_modes,
    primary,
    privacy_label,
    pseudonym_note,
    run_models,
    server_note,
)
from llmanno.core.i18n import current, output_language, t
from llmanno.core.project import Project
from llmanno.core.prompt import TEMPLATE_VERSION, render_system
from llmanno.core.store import sha256_text
from llmanno.core.validation import freeze_intact

CITATION = (
    "Feuerriegel et al. (2026), A reporting checklist for large language models in behavioural "
    "science, Nature Human Behaviour, doi:10.1038/s41562-026-02492-7"
)


def todo() -> str:
    """Markierung für Angaben, die nur die Forschenden machen können."""
    return t("→ ergänzen", "→ to complete")


def guide_rows(project: Project, report: EvaluationReport | None) -> list[tuple[str, str, str]]:
    """(Punkt, Thema, Angabe); Punkte ohne Nummer sind empfohlene Zusatzangaben."""
    cfg = project.config
    assert cfg.data is not None and cfg.codebook is not None
    spec, items = prepare_prompts(project)
    runs = run_models(project)
    coding = runs[0].coding if runs else cfg.coding
    audit = project.store.audit()
    reviews = project.store.reviews()
    gold = cfg.data.mapping.gold
    de = current() == "de"
    todo_ = todo()

    mode = (
        "Single-Label"
        if cfg.mode == "single"
        else t(f"Multi-Label, bis zu {cfg.k} Codes je Einheit", f"multi-label, up to {cfg.k} codes "
               "per unit")
    )  # fmt: skip
    unit, n_codes = unit_mode_label(coding.units.mode), len(cfg.codebook.active_codes())
    purpose = t(
        f"Deduktive Kodierung von {len(items)} Einheiten ({unit}) mit festem Codebuch aus "
        f"{n_codes} Codes, {mode}. Das LLM bildete keine Kategorien.",
        f"Deductive coding of {len(items)} units ({unit}) with a fixed codebook of {n_codes} "
        f"codes, {mode}. The LLM did not develop categories.",
    )
    blind = sum(r.mode == "blind" for r in reviews)
    assisted = len(reviews) - blind
    listed = f" ({', '.join(gold)})" if gold else ""
    humans = t(
        f"Kodierung automatisiert. Menschlich: {len(gold)} Referenzkodierung(en){listed}, {blind} "
        f"blinde und {assisted} LLM-gestützte Reviews; als Referenz zählen nur blinde Reviews.",
        f"Coding automated. Human: {len(gold)} reference coding(s){listed}, {blind} blind and "
        f"{assisted} LLM-assisted reviews; only blind reviews count as reference.",
    )

    models, access, params = [], [], []
    for run in runs:
        m, provider, fact = run.config, run.provider, run.fact
        digest = (
            f"Digest {fact.digest}"
            if fact and fact.digest
            else t(f"Version und Größe {todo_}", f"version and size {todo_}")
        )
        started = (
            t(", Laufbeginn ", ", run started ") + f"{fact.started:%Y-%m-%d}"
            if fact and fact.started
            else ""
        )
        served = (
            t(", vom Anbieter gemeldet: ", ", reported by the provider: ") + _served(fact.served)
            if fact and fact.served
            else ""
        )
        size = model_size(fact)
        size = f", {size}" if size != "–" else ""
        models.append(
            f"{m.model} {t('über', 'via')} {provider.name} ({provider.base_url}"
            f"{server_note(fact)}); {digest}{size}{served}{started}"
        )
        if provider.key == DEMO_PROVIDER.key:
            kind = t("Demo-Simulator, kein LLM", "demo simulator, no LLM")
        elif provider.kind == "ollama":
            kind = t("Ollama, native API, lokal", "Ollama, native API, local")
        else:
            kind = t("OpenAI-kompatible API", "OpenAI-compatible API")
        access.append(f"{m.model}: {kind}")
        effort = run.effort
        params.append(
            f"{m.model}: "
            + t("Temperatur", "temperature")
            + f" {_temperature(run.temperature)}, "
            + t(f"max. {m.max_tokens} Tokens", f"max {m.max_tokens} tokens")
            + f", Thinking {thinking_label(m.thinking)}"
            + (t(", Reasoning-Aufwand ", ", reasoning effort ") + effort if effort else "")
            + (
                t(", Ausgabeformat ", ", output format ") + output_modes(fact)
                if fact and fact.output_modes
                else ""
            )
        )
    agree = coding.adaptive_min_agree
    adaptive = (
        t(f", adaptiv: Abbruch nach {agree} übereinstimmenden", f", adaptive: stop after {agree} "
          "agreeing")
        if any(r.adaptive for r in runs)
        else ""
    )  # fmt: skip
    n_runs = max((r.n_runs for r in runs), default=coding.n_runs)
    params.append(
        t(
            f"Seeds {coding.base_seed} + Laufindex; bis zu {n_runs} Läufe je Einheit{adaptive}",
            f"Seeds {coding.base_seed} + run index; up to {n_runs} runs per unit{adaptive}",
        )
    )

    consents = [e for e in audit if e.event == "remote_consent"]
    providers = {r.provider.key: r.provider for r in runs}
    privacy = "; ".join(f"{p.name}: {privacy_label(p.privacy_level)}" for p in providers.values())
    if consents:
        last = f"{consents[-1].ts:%Y-%m-%d}"
        consent = t(
            f"Zustimmung zur Übermittlung an externe Anbieter {len(consents)}-mal protokolliert, "
            f"zuletzt {last}. ",
            f"Consent to transfer data to external providers logged {len(consents)} time(s), "
            f"last on {last}. ",
        )
    else:
        consent = t(
            "Keine Übermittlung an externe Anbieter protokolliert. ",
            "No transfer to external providers logged. ",
        )
    data = (
        t("Verarbeitung: ", "Processing: ")
        + f"{privacy or '–'}. "
        + consent
        + (
            f"{names} " + t(f"Einwilligungen {todo_}.", f"Consent {todo_}.")
            if (names := pseudonym_note(runs))
            else t(f"Pseudonymisierung und Einwilligungen {todo_}.",
                   f"Pseudonymization and consent {todo_}.")
        )
    )  # fmt: skip

    calls = [r.fact for r in runs if r.fact]
    repaired = sum(f.repaired for f in calls)
    unsupported = sum(f.unsupported for f in calls)
    invalid = sum(f.status["invalid"] + f.status["error"] for f in calls)
    panel = len(runs) > 1
    post = t(
        "Antworten gegen ein JSON-Schema geprüft und Signalwörter im Text verortet; "
        f"{repaired} Aufrufe repariert, {invalid} ungültig oder fehlerhaft (nicht gewertet), "
        f"{unsupported} mit nicht belegten Signalwörtern. Final ist der häufigste Code "
        "(Self-Consistency)" + (", gleich gewichtet über das Panel." if panel else "."),
        "Responses checked against a JSON schema and signal words located in the text; "
        f"{repaired} calls repaired, {invalid} invalid or failed (not counted), {unsupported} "
        "with unsupported signal words. The final code is the most frequent one "
        "(self-consistency)" + (", equally weighted across the panel." if panel else "."),
    )

    validation = t(f"Keine Auswertung berechnet {todo_}.", f"No evaluation computed {todo_}.")
    bias = comparison = todo_
    if report is not None and (main := primary(report)) is not None:
        a = main.agreement
        name = _reference(main.reference, de)
        kappa = f"{_n(a.kappa.value)} [{_n(a.kappa.low)}; {_n(a.kappa.high)}]"
        validation = t(
            f"Gegen {name} (n = {a.n}): Cohens κ {kappa}, Krippendorffs α {_n(a.alpha.value)}",
            f"Against {name} (n = {a.n}): Cohen's κ {kappa}, Krippendorff's α {_n(a.alpha.value)}",
        )
        if report.baseline is not None:
            validation += t("; Mensch-Mensch-α ", "; human–human α ") + _n(report.baseline.alpha)
        if cfg.freeze is not None:
            hash12 = cfg.freeze.instrument_hash[:12]
            if freeze_intact(project):
                validation += t(
                    f"; Instrument vor der Test-Auswertung eingefroren ({hash12}…)",
                    f"; instrument frozen before the test evaluation ({hash12}…)",
                )
            else:
                validation += t(
                    f"; eingefroren ({hash12}…), danach verändert: Freeze gebrochen",
                    f"; frozen ({hash12}…), changed afterwards: freeze broken",
                )
        validation += "."
        if report.differential:
            flagged = ", ".join(d.variable for d in report.differential if d.p_holm < 0.05)
            bias = t(
                "Fehlerrate je Subgruppe getestet (Wald-Test, Holm): "
                + (f"Hinweise für {flagged}." if flagged else "kein Hinweis."),
                "Error rate tested per subgroup (Wald test, Holm): "
                + (f"evidence for {flagged}." if flagged else "no evidence."),
            )
        parts = []
        if report.baseline is not None:
            parts.append(t("Mensch-Mensch-Baseline", "human–human baseline"))
        if report.alt_test is not None:
            passed = report.alt_test.passed
            parts.append(
                t("Alt-Test", "alternative annotator test")
                + f" ({t('bestanden', 'passed') if passed else t('nicht bestanden', 'not passed')})"
            )
        if panel:
            parts.append(t(f"{len(runs)} Modelle im Panel", f"{len(runs)} models in the panel"))
        comparison = ", ".join(parts) or todo_

    hashes = {e.details.get("config_hash") for e in audit if e.event == "run_started"}
    frozen = cfg.freeze.frozen_at if cfg.freeze is not None else None
    before = {
        e.details.get("config_hash")
        for e in audit
        if e.event == "run_started" and frozen is not None and e.ts <= frozen
    }
    design = t(
        f"Codebuch wörtlich im Prompt, Template-Version {TEMPLATE_VERSION}. {len(hashes)} Lauf-"
        "Identität(en) ausgeführt (jede Änderung an Prompt, Codebuch oder Parametern ergibt eine "
        "neue)" + (f", davon {len(before)} vor dem Freeze" if frozen is not None else "") + ".",
        f"Codebook verbatim in the prompt, template version {TEMPLATE_VERSION}. {len(hashes)} run "
        "identities executed (any change to prompt, codebook or parameters creates a new one)"
        + (f", {len(before)} of them before the freeze" if frozen is not None else "")
        + ".",
    )
    if report is not None and report.sensitivity:
        s = report.sensitivity[0]
        n = len(s.wording_rows)
        design += t(
            f" Prompt-Sensitivität: {n} Formulierungen auf {s.n_items} Einheiten, Krippendorffs α "
            f"über die Formulierungen {_n(s.alpha)}.",
            f" Prompt sensitivity: {n} wordings on {s.n_items} units, Krippendorff's α across "
            f"wordings {_n(s.alpha)}.",
        )
    else:
        design += t(f" Prompt-Sensitivität nicht geprüft {todo_}.", f" Prompt sensitivity not "
                    f"tested {todo_}.")  # fmt: skip
    resources = "; ".join(
        t(
            f"{f.key}: {f.calls} Aufrufe, {f.tokens_in} Token ein, {f.tokens_out} aus, "
            f"{f.seconds / 60:.1f} min",
            f"{f.key}: {f.calls} calls, {f.tokens_in} tokens in, {f.tokens_out} out, "
            f"{f.seconds / 60:.1f} min",
        )
        for f in calls
    ) or "–"  # fmt: skip

    system = render_system(spec, coding.base_seed, example_texts=False) if items else ""
    context = (
        t(
            " Im Kontextmodus stehen benachbarte Zeilen im Prompt, keine früheren Antworten.",
            " In context mode, neighbouring rows are in the prompt, no earlier responses.",
        )
        if coding.units.mode == "context"
        else ""
    )
    order = t(" Code-Reihenfolge je Lauf gemischt.", " Code order permuted per run.")
    if is_demo(project):
        purpose = f"{demo_note()} {purpose}"
    return [
        ("A.1", t("Zweck", "Purpose"), purpose),
        ("A.2", t("Automatisierungsgrad", "Degree of automation"), humans),
        ("B.1", t("Modelle", "Models"), " · ".join(models) or "–"),
        (
            "B.2",
            t("Zugang", "Access"),
            "; ".join(access)
            + t(
                ". Jede Einheit und jeder Lauf ist ein eigener Aufruf.",
                ". Each unit and run is a separate call.",
            ),
        ),  # fmt: skip
        ("B.3", t("Parameter", "Parameters"), "; ".join(params)),
        (
            "B.4",
            t("Anpassung", "Adaptation"),
            t(
                "Kein Fine-Tuning; angepasst nur über den Prompt.",
                "No fine-tuning; adapted only through the prompt.",
            )
            + (order if coding.permute_codes else ""),
        ),  # fmt: skip
        (
            "B.5",
            t("Zustand", "State"),
            t(
                "Keiner: Anfragen sind unabhängig, ohne Gesprächsverlauf.",
                "None: requests are independent, without conversation history.",
            )
            + context,
        ),  # fmt: skip
        (
            "C.1",
            "Prompts",
            t(
                "Exakter Prompt im Anhang des Methoden-Reports; je Aufruf SHA-256 von "
                "System- und User-Nachricht im Replikationspaket (calls.jsonl), im Volltext nur "
                "mit Rohtexten.",
                "Exact prompt in the appendix of the methods report; SHA-256 of system and user "
                "message per call in the replication package (calls.jsonl), full text only with "
                "raw texts.",
            ),
        ),
        (
            "C.2",
            t("System-Anweisungen", "System instructions"),
            t(
                f"System-Nachricht mit Codebuch und Regeln, SHA-256 {sha256_text(system)[:16]}…; "
                "im Anhang und in prompts/system_lauf0.txt.",
                f"System message with codebook and rules, SHA-256 {sha256_text(system)[:16]}…; in "
                "the appendix and in prompts/system_lauf0.txt.",
            )
            if not spec.examples
            else t(
                f"System-Nachricht mit Codebuch, Regeln und {len(spec.examples)} Beispielen aus "
                f"Dev; SHA-256 {sha256_text(system)[:16]}… der Fassung im Anhang, die statt der "
                "Beispieltexte nur ihre IDs zeigt. Den gesendeten Wortlaut enthält "
                "prompts/system_lauf0.txt nur im Paket mit Rohtexten.",
                f"System message with codebook, rules and {len(spec.examples)} examples from "
                f"dev; SHA-256 {sha256_text(system)[:16]}… of the version in the appendix, which "
                "shows only the IDs instead of the example texts. The sent wording is in "
                "prompts/system_lauf0.txt only in a package with raw texts.",
            ),
        ),
        ("D.1", t("Datensensibilität", "Data sensitivity"), data),
        ("E.1", t("Validierung", "Validation"), validation),
        ("E.2", t("Nachbearbeitung", "Post-processing"), post),
        (
            "F.1",
            "Code",
            t(
                f"llmanno {version('llmanno')}; Replikationspaket mit llmanno-rerun. Repository "
                f"oder DOI {todo_}.",
                f"llmanno {version('llmanno')}; replication package with llmanno-rerun. "
                f"Repository or DOI {todo_}.",
            ),
        ),
        ("G.1", t("Interessenkonflikte", "Conflicts of interest"), todo_),
        ("", t("Begründung der Modellwahl", "Rationale for model choice"), todo_),
        ("", t("Prompt-Design", "Prompt design"), design),
        ("", t("Vergleich mit Alternativen", "Comparison with alternatives"), comparison),
        (
            "",
            t("Trainingsdaten-Leakage", "Training data leakage"),
            t(
                f"Hinweis im Report (Ludwig et al. 2024); ob Daten oder Codebuch öffentlich "
                f"waren, {todo_}.",
                f"Noted in the report (Ludwig et al. 2024); whether data or codebook were "
                f"public: {todo_}.",
            ),
        ),
        ("", t("Verzerrungen", "Biases"), bias),
        (
            "",
            t("Gesprächsprotokolle", "Conversation logs"),
            t(
                "Alle Anfragen und Antworten sind im Projekt gespeichert.",
                "All requests and responses are stored in the project.",
            ),
        ),
        ("", t("Ethik", "Ethics"), todo_),
        ("", t("Rechenressourcen", "Compute resources"), resources),
    ]


def guide_markdown(project: Project, report: EvaluationReport | None, lang: str = "de") -> str:
    def cell(text: str) -> str:
        return text.replace("|", "\\|").replace("\n", " ")

    with output_language(lang):
        lines = [
            t("# GUIDE-LLM-Checkliste", "# GUIDE-LLM checklist"),
            "",
            t(
                f"Vorausgefüllt von llmanno nach {CITATION}. Zeilen mit „{todo()}“ selbst "
                "ausfüllen; Punkte ohne Nummer sind empfohlene Zusatzangaben.",
                f"Prefilled by llmanno following {CITATION}. Complete the rows marked “{todo()}”; "
                "items without a number are recommended additions.",
            ),
            "",
            t("| Punkt | Thema | Angabe |", "| Item | Topic | Entry |"),
            "|---|---|---|",
        ]
        lines += [
            f"| {p} | {cell(topic)} | {cell(a)} |" for p, topic, a in guide_rows(project, report)
        ]
    return "\n".join(lines) + "\n"
