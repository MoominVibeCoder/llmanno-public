# LLM-Annotation (working title)

*Deutsch: [README.md](README.md)*

A desktop application for **deductive coding of qualitative data with large language models**: tabular data (XLSX or CSV) with one text unit per row, a free-text codebook, and one model or a panel of models (local via Ollama or any OpenAI-compatible API, including academic services such as GWDG/KISSKI and KI:connect.NRW). For each unit the models return the top-k codes with confidence, a short rationale and signal words that are checked verbatim against the text.

The point of the tool is **validation and reporting**, not just coding:

- evaluation against human coding with the usual agreement and classification measures (Cohen's κ, Krippendorff's α, Gwet's AC1, Scott's π, PABAK, F1, MCC, top-k), bootstrap intervals (clustered by transcript), human–human baseline and the alternative annotator test
- a dev/test split with an instrument freeze, a hashed instrument fingerprint and a preregistration export (Markdown or Word)
- a review queue with blind reviews as gold, bias-corrected prevalence (PPI++) and columns for the `dsl` R package
- calibration of model confidence (ECE, AUROC, AURC, reliability diagram, risk–coverage curve), prompt sensitivity, a differential measurement-error test across subgroups
- several code systems per project (each with its own codebook, calls and validation) and replacement of names by pseudonyms before anything is sent
- exports to XLSX, SPSS, Stata, R and REFI-QDA (MAXQDA, ATLAS.ti, QualCoder, NVivo), a methods report (Word or HTML) with a prefilled GUIDE-LLM checklist, and a replication package that `llmanno-rerun` recodes headless and compares call by call

API keys live in the operating system's keyring, never in the project. Runs against external providers require explicit consent, which is logged.

The user interface is available in German and English (menu *Sprache*). Methods report, preregistration, GUIDE-LLM checklist and replication package can be written in German or English (menu *Sprache* → report language); the user guide is German.

## Installation

The Windows build needs no Python: unzip it and start `llmanno.exe`. From source, with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

```bash
uv run llmanno
```

```bash
uv run pytest
```

Recode from a replication package:

```bash
uv run llmanno-rerun replication.zip --out rerun
```

**Open demo** (project tab or menu *Datei*) creates an example project with 200 synthetic classroom turns and simulated answers (no LLM, no data of your own needed), so every tab can be explored. The same example for trying it yourself is in `src/llmanno/demo/` (folder `Beispiel` in the Windows build).

## Documentation

- [docs/MANUAL.md](docs/MANUAL.md): user guide with methodological recommendations (German original: [docs/ANLEITUNG.md](docs/ANLEITUNG.md)); in the program under Help or F1
- [docs/PLAN.md](docs/PLAN.md): design and acceptance runs (German)

## Versions used in the paper

This repository starts from a cleaned state without the development history. The versions used in the
studies (commits `9182f86` and `4b7df4f` of the development repository) are archived as source in the
replication packages: https://doi.org/10.5281/zenodo.23911470

## License

GNU Affero General Public License, version 3 or (at your option) any later version ([LICENSE](LICENSE)). The Windows build bundles Qt and PySide6 under the LGPL-3.0 and further packages under their own licenses ([packaging/licenses/HINWEISE.txt](packaging/licenses/HINWEISE.txt)).

## Citation

See [CITATION.cff](CITATION.cff). Please cite the version used for coding; the methods report states it together with all model parameters.
