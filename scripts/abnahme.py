"""Abnahmetest Phase 1: Beispielprojekt kodieren, mit Abbruch und Fortsetzen.

Lokal:
    uv run python scripts/abnahme.py --out C:/temp/abnahme --model qwen3:4b

Über GWDG/KISSKI (API-Key vorher im Tab Modelle im Schlüsselbund speichern):
    uv run python scripts/abnahme.py --out C:/temp/abnahme --provider gwdg \
        --model openai-gpt-oss-120b --max-tokens 4096 --consent

Über KI:connect.NRW genauso mit --provider kiconnect --model "OpenAI GPT OSS 120B"
--provider-temperature (der Anbieter ignoriert die Temperatur bei gpt-oss).

Panel: --model mehrfach angeben, etwa
    --model "OpenAI GPT OSS 120B" --model qwen3.8-27b \
    --provider-temperature-for "OpenAI GPT OSS 120B" --reasoning-effort-for qwen3.8-27b=low

Reasoning-Modelle (gpt-oss) brauchen ein großes --max-tokens, sonst schneidet das Denken die
Antwort ab.

Prüft: Pilot mit Hochrechnung, Anhalten nach 15 Aufrufen, Fortsetzen ohne Verlust und ohne
Duplikate, jede Antwort gültig oder markiert. Schreibt am Ende eine XLSX und die Kennwerte
gegen die beiden Gold-Kodierungen des Beispiels.
"""

from __future__ import annotations

import argparse
import collections
import difflib
import shutil
import sys
import time
from pathlib import Path

from llmanno.core.codebook import suggest_auto
from llmanno.core.config import (
    GWDG_CHAT_AI,
    KI_CONNECT_NRW,
    Code,
    ColumnMapping,
    ModelConfig,
    UnitSettings,
)
from llmanno.core.demo import DEMO_DIR
from llmanno.core.engine import Engine, results
from llmanno.core.evaluation import evaluate
from llmanno.core.export.evaluation import export_evaluation
from llmanno.core.export.prereg import export_preregistration
from llmanno.core.export.refi import export_qdpx
from llmanno.core.export.replication import export_replication
from llmanno.core.export.report import export_report
from llmanno.core.export.xlsx import export_xlsx
from llmanno.core.power import keep_awake
from llmanno.core.project import Project
from llmanno.core.providers import make_provider, probe

EXAMPLE = DEMO_DIR  # das Beispiel, das auch „Demo öffnen“ nutzt
REMOTE = {"gwdg": GWDG_CHAT_AI, "kiconnect": KI_CONNECT_NRW}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="Arbeitsordner (wird geleert)")
    parser.add_argument("--provider", choices=["ollama", *REMOTE], default="ollama")
    parser.add_argument("--model", required=True, action="append", help="mehrfach: Panel")
    parser.add_argument("--consent", action="store_true", help="Daten dürfen an den Anbieter")
    parser.add_argument("--no-evidence-repair", action="store_true")
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--parallel", type=int, default=1, help="gleichzeitige Anfragen je Modell")
    parser.add_argument(
        "--provider-temperature",
        action="store_true",
        help="Temperatur nicht senden, der Anbieter legt sie fest (gpt-oss bei KI:connect)",
    )
    parser.add_argument(
        "--provider-temperature-for",
        action="append",
        default=[],
        help="wie --provider-temperature, nur für dieses Modell (mehrfach möglich)",
    )
    parser.add_argument("--reasoning-effort", help="etwa low/medium/high; sonst Anbieter-Vorgabe")
    parser.add_argument(
        "--reasoning-effort-for",
        action="append",
        default=[],
        metavar="MODELL=AUFWAND",
        help="Herleitungsaufwand nur für ein Modell, etwa qwen3.8-27b=low (mehrfach möglich)",
    )
    args = parser.parse_args()

    remote = REMOTE.get(args.provider)
    if remote:
        provider = make_provider(remote)
        names = [m.name for m in provider.list_models()]
        provider.close()
        for model in args.model:
            if model not in names:
                close = difflib.get_close_matches(model, names, n=5, cutoff=0.3)
                sys.exit(f"Modell {model} gibt es bei {remote.name} nicht. Ähnlich: {close}")
        # Fähigkeiten gelten je Anbieter: das, was alle Panel-Modelle können.
        found = []
        for model in args.model:
            capabilities, hints = probe(remote, model)
            print(f"Fähigkeiten {model}: {capabilities.model_dump()} {hints}", flush=True)
            found.append(capabilities)
        common = {}
        for key in found[0].model_dump():
            values = [c.model_dump()[key] for c in found]
            common[key] = False if False in values else None if None in values else True
        remote = remote.model_copy(
            update={"capabilities": type(found[0])(**common), "parallel": args.parallel}
        )

    efforts = dict(item.split("=", 1) for item in args.reasoning_effort_for)
    if args.out.exists():
        shutil.rmtree(args.out)
    project = Project.create(args.out / "projekt", name=f"Abnahme {' + '.join(args.model)}")
    project.import_data(
        EXAMPLE / "daten.xlsx",
        "Turns",
        ColumnMapping(
            id="ID",
            text=["Text"],
            group="Transkript",
            order="Nr",
            speaker="Sprecher",
            gold=["Coder A", "Coder B"],
        ),
    )
    project.load_codebook(EXAMPLE / "codebuch.md")
    codes = [Code(id=s.id, label=s.label) for s in suggest_auto(project.codebook_text or "")]
    assert [c.id for c in codes] == ["K1", "K2", "K3", "K4"], codes
    providers = [
        p.model_copy(update={"parallel": args.parallel}) if p.key == args.provider else p
        for p in project.config.providers
    ]
    if remote:
        providers = [*providers, remote]
    project.update(
        codebook=project.config.codebook.model_copy(update={"codes": codes}),
        coding=project.config.coding.model_copy(
            update={
                "units": UnitSettings(mode="context", n_before=2, n_after=1),
                "evidence_repair": not args.no_evidence_repair,
            }
        ),
        providers=providers,
        panel=[
            ModelConfig(
                provider=args.provider,
                model=model,
                max_tokens=args.max_tokens,
                provider_temperature=args.provider_temperature
                or model in args.provider_temperature_for,
                reasoning_effort=efforts.get(model, args.reasoning_effort),
            )
            for model in args.model
        ],
    )
    project.save()

    def progress(p):
        if p.done % 25 == 0:
            print(
                f"  {time.strftime('%H:%M:%S')} {p.done}/{p.planned} {p.item_id} {p.status}",
                flush=True,
            )

    with keep_awake():
        start = time.time()
        pilot = Engine(project).run(
            item_limit=20, remote_consent=args.consent, on_progress=progress
        )
        eta = pilot.eta_s(200 - 20)
        print(
            f"PILOT {pilot.calls} Aufrufe in {time.time() - start:.0f} s, gültig {pilot.ok}, "
            f"ungültig {pilot.invalid}, Fehler {pilot.errors}, repariert {pilot.repaired}, "
            f"Rest etwa {eta / 60:.0f} min"
            if eta
            else "PILOT ohne Hochrechnung",
            flush=True,
        )
        count = {"n": 0}

        def stop_soon() -> bool:
            count["n"] += 1
            return count["n"] > 15

        stopped = Engine(project).run(remote_consent=args.consent, should_stop=stop_soon)
        print(f"ANGEHALTEN nach {stopped.calls} Aufrufen: {stopped.stopped}", flush=True)
        full = Engine(project).run(remote_consent=args.consent, on_progress=progress)
        print(
            f"VOLLAUF {full.calls} Aufrufe, gültig {full.ok}, "
            f"ungültig {full.invalid}, Fehler {full.errors}"
        )

    calls = project.store.calls()
    keys = [(c.item_id, c.model_key, c.run, c.config_hash) for c in calls]
    print(
        "Duplikate:",
        len(keys) - len(set(keys)),
        "| Lauf-Identitäten:",
        len({c.config_hash for c in calls}),
    )
    print("Status Aufrufe:", dict(collections.Counter(c.status for c in calls)))
    print("Flags:", dict(collections.Counter(f for c in calls for f in c.flags)))
    units = [u.unit_id for u in project.store.units()]
    by_item = results(project, units)
    print("Status Einheiten:", dict(collections.Counter(r.status for r in by_item.values())))
    unit_flags = collections.Counter(f for r in by_item.values() for f in r.flags)
    print("Flags Einheiten:", dict(unit_flags))
    for model in project.config.panel:
        mine = [c for c in calls if c.model_key == model.key]
        status = dict(collections.Counter(c.status for c in mine))
        print(f"Modell {model.key}: {len(mine)} Aufrufe {status}", flush=True)
    export_xlsx(project, args.out / "ergebnisse.xlsx")
    report = evaluate(project, resamples=1000)
    export_evaluation(report, args.out / "kennwerte.xlsx")
    for c in report.comparisons:
        a = c.agreement
        kappa = f"κ {a.kappa.value:.3f} [{a.kappa.low:.3f}; {a.kappa.high:.3f}]"
        rest = f"α {a.alpha.value:.3f}, Accuracy {c.accuracy.value:.3f}"
        print(f"LLM vs. {c.reference}: {kappa}, {rest}")
    stability = report.stability
    print(f"Stabilität je Modell: {stability.intra_alpha}, zwischen Modellen: "
          f"{stability.inter_alpha}")  # fmt: skip
    if len(project.config.panel) > 1:
        for model in project.config.panel:
            single = evaluate(project, resamples=200, model=model.key)
            c = single.comparisons[0]
            print(f"  nur {model.key} vs. {c.reference}: κ {c.agreement.kappa.value:.3f}")
    export_qdpx(project, args.out / "abnahme.qdpx")
    export_report(project, report, args.out / "methoden.docx")
    export_report(project, report, args.out / "methoden.html")
    export_preregistration(project, args.out / "praeregistrierung.docx")
    export_replication(project, args.out / "replikation.zip", pseudonymize_ids=True, report=report)
    print(f"Exporte: {args.out}")
    project.close()


if __name__ == "__main__":
    main()
