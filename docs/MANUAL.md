# Guide

This guide describes how the program carries out deductive coding with LLMs in a way that can be reported and checked. The technical plan is in [PLAN.md](PLAN.md).

## What the program is for

- **Deductive coding** of text units (conversational turns, answers, paragraphs) with an existing codebook
- **Validation** against human codings with the metrics that are common in empirical research
- **Hand-over** to QDA software and statistics software

It is not intended for inductive category building, or as a replacement for human judgement without validation. LLM codings are only trustworthy after they have been checked against human gold.

It does not suit reflexive or interpretive approaches (such as reflexive thematic analysis): there, coding itself is the researchers' process of gaining insight, and an open letter by Jowsey, Braun, Clarke et al. (2025) considers GenAI unsuitable for it. The codebook should be developed by humans before the LLM codes (Dunivin 2025). The methods paragraph of the report therefore states the scope explicitly.

## Installation and start

**Windows program, without Python:** Unpack the ZIP, for example to `C:\Programme\llmanno` or into your own user folder, and start `llmanno.exe`. The programs are not signed, so Windows SmartScreen asks for confirmation at the first start (“More info” → “Run anyway”). The same folder contains `llmanno-rerun.exe` for the rerun in the console, the folder `Beispiel` with the example project, and the licenses (`LICENSE.txt`, `Lizenzen`). Windows stores settings (language, recently opened projects) per user; error logs go to `%LOCALAPPDATA%\llmanno\LLM-Annotation\fehler.log`.

**Trying it out without your own data:** **Open demo** (Project tab or File menu) creates the example project (200 synthetic conversational turns from math lessons, a codebook with four codes, two gold codings) and fills it with simulated answers from two “Demo-Simulator” models. These are not LLM results; they are derived from the coding “Coder A” with errors mixed in, but they go through the same processing as real answers. This lets you look at results, review, evaluation, split and exports without uploading data or setting up a model. The demo is stored in your user profile (`%LOCALAPPDATA%\llmanno\LLM-Annotation\Demo`); if one already exists, the program opens it again or, if you wish, creates a new one. For real runs, add a model to the panel in the Models tab.

**First steps in the program:** **File → New project…** (or the button on the start page) starts a wizard that asks for folder, data, codebook and model; **New empty project…** only chooses the folder. Above the tabs, the **Next step** bar shows what is done and what comes next, with a button to the matching tab (it can be hidden via **View → Show next step**). Every control explains itself in its tooltip; **F1** (Help → Guide for this tab) opens this guide at the matching section, **Help → Glossary** the terms. Expert settings whose defaults suit getting started are in collapsed **Advanced** sections; **View → Show advanced settings** opens all of them.

**From the source code** (with [uv](https://docs.astral.sh/uv/)):

```bash
uv sync
```

```bash
uv run llmanno
```

Local models run via [Ollama](https://ollama.com) (`ollama serve`). On computers with little graphics memory (around 4 GB), text models with up to about 4 billion parameters fit; larger ones run partly on the CPU and become very slow. For larger models, academic APIs such as GWDG/KISSKI or KI:connect.NRW are suitable.

### Academic APIs: GWDG/KISSKI and KI:connect.NRW

Both are preset in the Models tab (privacy level “academic / EU”). You create the API key in the provider's portal through your own university and save it with **Save to keyring**; **Test connection** then lists the models, and **Check capabilities** (under Advanced) determines whether the provider supports a fixed answer format (structured outputs) and reproducibility (seeds). A key that you have typed in but not yet saved is saved when you test the connection. The model names appear in the list as the provider lists them, for KI:connect for example “OpenAI GPT OSS 120B” or “qwen3.8-27b”.

- **Temperature:** Some models ignore it at the provider (at KI:connect gpt-oss and GPT-5). In that case, choose “do not send” in the panel; the report then honestly states that the temperature was set by the provider.
- **Reasoning models** (gpt-oss, qwen3, GPT-5) need a large `max_tokens` (about 4096); otherwise the thinking cuts off the answer and the call counts as truncated. When you add a model to the panel, the program sets 4096 for models that are recognizably thinking models.
- **Rate limits:** In the Models tab, you can set a maximum number of requests per minute for each provider. If the provider still reports “too many requests” (HTTP 429), the program waits (2, 5, 15, 30 s, or as long as the provider requests via Retry-After, at GWDG via ratelimit-reset) and tries again. If the provider requests a pause of more than 5 minutes, the quota is usually used up: the run stops and can be resumed later without losing anything.
- **Network failure:** After five connection errors in a row, the run waits (1, 2, 5, 10 min) instead of aborting, for up to 30 minutes in total; the Run tab shows this.
- **Usage and quota:** After each run, the Run tab shows the tokens as reported by the provider (input, output, of which reasoning, from the cache, as far as the provider reports them), the mean number of calls per unit and model, and the remaining quota if the provider sends it (GWDG: requests per minute, hour, day and month). Before the next run, the consent dialog estimates calls and tokens as a range (depending on the adaptive stop; when resuming, without the calls already completed), estimates the output from the models' previous answers, and warns if even the most favorable case exceeds the last reported daily or monthly quota. An aborted run, for example because the quota is used up, also records usage and quota in the log.
- **Parallel requests:** Large projects take a long time when requests are sent one after another (a panel of three models with 20,000 units takes several days). In the Models tab, you can set for each provider how many requests run at the same time. Use more than 1 only if the provider allows it (for Ollama, the server setting `OLLAMA_NUM_PARALLEL`). Prompts, seeds and the run identity stay the same; some servers nevertheless answer slightly differently under parallel load, which is why the report states the value. If the provider reports “too many concurrent requests” (HTTP 429), the run sends fewer requests at the same time (logged); KI:connect.NRW, for example, could not handle four concurrent requests to qwen3.8-27b during acceptance testing.

## Workflow

The tabs follow the workflow from left to right.

A new wave of data collection with the same, already validated instrument: **File → New project from this instrument…**. Codebook, code list, prompt and coding parameters, panel, review and gold rules are taken over; split, freeze, runs and reviews are not. The report names the source project and states whether the instrument has remained unchanged since.

**Several code systems** (such as Reference Move as a separate dimension next to the main codebook): at the top right, click **+ Code system…**, then choose name, codebook, gold columns, coding mode and k of the new code system.

- Each code system codes the same units with its own codebook in separate calls; the measurement is therefore the same as in a project with only this codebook.
- The **Code system** selection next to it determines what the Codebook, Prompt & units, Splits & freeze, Results, Review, Evaluation and Export tabs show. Each code system has its own code list, mode, prompt parameters, gold, results, review queue, metrics, freeze, report and replication package. **Code system…** changes gold columns, mode and k of the selected one (add new gold columns beforehand in the Data tab of the main project).
- Shared are the data, models, prompt language, unit of analysis including context and target column, and the list of names. They are set in the main project and taken over when you switch code systems and before every run. If a further code system is frozen, a change to these settings also breaks its freeze; the status line names frozen code systems. New texts can no longer be imported once any code system has runs.
- The **run** codes all code systems one after another, with one consent and one budget for all. An unfinished code system (for example one without active codes) is skipped and named in the log; a broken freeze stops only its own code system. Pilot, “dev only” and prompt sensitivity apply per code system; frozen further code systems code all units even with “dev only”.
- The split of a further code system takes over the split of the main project if the same units have gold; otherwise it splits according to its own gold and the split rules that came from the main project when it was created.
- XLSX and REFI-QDA can contain all code systems (switch “Export all code systems” in the Export tab): the XLSX file with separate sheets “Ergebnisse – Name” etc. and an info sheet, the REFI-QDA project with the texts once and a separate branch in the code system for each code system (codes, signal words, notes). A joint REFI-QDA export works only if all code systems code the same data; please export a code system that still codes the old data on its own. Code systems without confirmed codes are left out; the status line names them. The confirmation prompt about exporting test data before the freeze takes all of them into account. Report and replication package are produced per code system.
- Further code systems are stored as complete projects under `codesysteme/<Name>/` in the project folder. To remove a code system, delete this folder while the project is closed. **New project from this instrument** takes over only the main project.

### 1. Project

Create a project in an empty folder. The project folder contains `project.json` (configuration), `store.sqlite` (all calls and the log), the codebook and a copy of the data. An open project is locked (`project.lock`) so that no second program instance overwrites the configuration; after a crash, the program offers to lift the lock. Keep projects on a local drive: on network drives and in cloud folders (OneDrive, Sciebo, Dropbox …) the database can become corrupted; the Project tab then warns you. **File → Recent projects** lists the last eight projects; **File → Show log…** lists every event of the project (import, runs, freeze, exports, consents) with time and details, and can be filtered. **Help → About llmanno** gives the version for citation. The **Coding mode** is either “One code per unit (single-label)” or “Several codes per unit (multi-label)”; the number of codes per unit (k) is under Advanced. Changing language, mode or k after runs starts a new coding.

### 2. Data

Choose an XLSX or CSV file and map the columns. For CSV, the program detects the delimiter (comma, semicolon, tab; from the header row) and the encoding (UTF-8 or Windows) itself. Values only become numbers if they stay unchanged in the process: “17” and “1.5” do, while “007”, “1.10” or “3,5” stay text, so that IDs and codes do not collapse into each other. If a row has more fields than the header row (usually an unprotected delimiter in the text), the extra fields go into separate columns, and the import report warns you.
- If the data are in **several files or sheets** (such as one transcript per lesson), **Merge several…** combines them into one XLSX file. For workbooks with several sheets, the program asks which ones belong to the data. The merged file then starts with “Quelle” (source: the file, with the sheet if there are several sheets) and “Quelle_ID” (source and row); both are preselected as group and ID. Columns that do not occur in all sources stay empty there; the import report names them. The merged file is imported like any other, also for gold updates and the rerun. The file names thus become group and unit IDs: if they contain names, pseudonymize the IDs in the replication package.
- **ID** and **Text** are required.
- **Group** (such as a transcript) and **Order** are needed for context mode.
- **Gold** are the columns with human codings, one per person, optionally plus a consensus.
- Under **Advanced:** order, metadata, consensus, analysis variables and “Gold from QDA project…”; the section title names what is in use there.
- **Analysis variables** (such as gender or school type) never go into the prompt, but they are available in the evaluation as subgroups and in the measurement error test; they go into the replication package only with raw texts. They can also be changed after coding runs.

Once coding has taken place, the imported data are fixed. Gold can still be added or corrected afterwards, for example when a second person joins: choose the same file with the new gold columns, tick them, and click **Update gold**. The texts must stay the same. Gold is not part of the instrument; run identities and freeze remain, the change is recorded in the log, and the previous data copy stays in the project folder as a backup. If the reference on the test set changes after a test evaluation (gold added, mapping or delimiter changed), the evaluation points this out.

### 3. Codebook

Load a text file. It goes into the prompt unchanged. The program suggests the codes; check the **code list**, because the model assigns only these codes and only they are evaluated. Changes take effect immediately; there is no separate confirmation step. The residual category “no code fits” can be switched on. The pattern for **Suggest codes**, the level of measurement, and the code and description of the residual category are under Advanced.
- **Code groups:** In the “Group” column, you can assign codes to a higher-level category, for example eight transactivity codes to four discourse modes. The evaluation then also computes the metrics at the level of the groups (agreement, κ, α, AC1, human–human α), report and metrics XLSX give both levels, and the REFI-QDA export creates the groups as parent codes. The groups do not go into the prompt; they do not change any run identity. They are, however, part of the analysis plan: regrouping after the freeze breaks it.
- **Map gold values:** If the gold columns contain values that do not correspond to any code (such as “1” instead of “K1”), map or ignore them here, and do so before the split: otherwise these units are missing from dev and test. For multi-label, the separator is also set here; “Add from gold columns” uses it. The Splits & freeze tab warns about such gaps.
- **Check disagreements:** The button shows the code pairs where the models deviate from the gold standard or from each other, most frequent first, with the cases (text, gold, code and rationale per model). As soon as there is a split, only from the development part (dev); the test part and the example units in the prompt are always left out. **Suggest boundary (LLM)** sends the codebook and up to 12 cases of a pair to a model of the panel, for external providers only after consent and with names replaced as during coding. It suggests a sharper rule, anchor examples and questionable gold cases. The suggestion changes nothing by itself: you edit the codebook yourself and load it again, then code and evaluate the development part again. Suggestions are recorded in the project log, and every loaded version of the codebook appears in the report anyway. **Save as worksheet (XLSX)** stores pairs, latest suggestions and cases for discussion in the team. If a model reaches 0.81 on the development part, that is not yet evidence: only the evaluation of the test part after the freeze counts.

### 4. Models

Set up providers (Ollama, GWDG/KISSKI, KI:connect.NRW or a custom OpenAI-compatible provider) and add models to the panel. If a small local model (under 10 billion parameters) is added to the panel, the program switches off the **follow-up (repair) if signal words are not in the text**: for such models it costs a second call and rarely helps. You can switch it on again in the Prompt & units tab; a note is then shown there. API keys are stored in the operating system's keyring, never in the project. **Remove key** deletes the key from there; a key applies per base URL, so it also applies to other projects with the same provider. For the preset providers, type, base URL, authentication, rate limit, timeout, parallel requests and capabilities are under Advanced, as are the Temperature, Thinking and Reasoning effort columns in the panel. Connection errors name the usual cause (Ollama not started, key missing or invalid, model unknown, quota used up).

### 5. Prompt & units

- Choose the **Unit of analysis**: Line in isolation, Line with context window, or Whole group.
- **Code only where this column is yes:** rows with “no” (empty, 0, nein, no) appear in the prompt only as context, such as the teacher's turns, and are not coded.
- Set **Repetitions per unit (N)**: how often each model codes the same unit.
- Under **Advanced:** adaptive stop, seed, shuffling the code order, signal words and rationale, follow-up (repair) and examples from the development part.
- **Replace names…** (pseudonymization, see Privacy): names in text, speaker and metadata then go to the model as pseudonyms.
- **Examples from the development part (dev)** (few-shot, off by default): **Select examples** selects up to five dev units with gold per code (based on the reference of the split and the seed) and puts them with their codes into the system message; **Remove** switches them off. This is only possible after the split; test units are never eligible. The example units are excluded from all metrics, from gate calibration, the review queue and prompt sensitivity, because the model has seen their gold; dev therefore becomes smaller. What counts are the examples of the runs on which the results are based, and the examples currently set: if you remove or reselect examples after a run, this does not bring back the ones the model has already seen. XLSX and statistics exports mark them (`beispiel_im_prompt`, `beispiel`; the R script filters them out for dsl). The drawn IDs and codes are part of the instrument: reselecting or removing them starts a new run and breaks a freeze. A new split is only possible after you remove the examples; otherwise examples could slip into the test set. If the unit of analysis changes, the program removes the examples, also in further code systems; if they no longer match the codebook or mode, it reports this. A new wave of data collection and a new code system do not take over any examples; the report of the new wave says so. Without raw texts, the replication package contains no hash of the system message, because that hash covered the example texts. Report, preregistration and GUIDE name only the IDs; the texts are included only in the replication package with raw texts. Without examples, the prompt stays exactly as before, character for character. To weigh it up: examples from the data can help where the codebook remains abstract, but they anchor the model to a few cases; anchor examples in the codebook itself are the more neutral choice.
- The **Preview** shows the complete prompt as the model receives it: “Instructions and codebook (system message)” and “Text of the unit (user message)”. Below it, you see the total number of calls and, separately, what is still open.

### 6. Splits & freeze

See “Dev/test split and freeze” under Methodological recommendations. This step comes before the first real run: **Create split**, then code only the development part in the Run tab, check the evaluation and refine the codebook or instructions, and only then **Freeze instrument**. Before freezing, the program asks you to confirm with a short checklist.

### 7. Run

First the pilot, which extrapolates the duration, then the full run. A line at the top names the phase (no split yet, development phase, frozen) and how many units are already coded; with “code development part (dev) only”, the button reads “Code / resume development part (dev)”. Stopping loses nothing; a new start resumes. If the provider is overloaded, the run waits briefly and tries again; if the provider requests a pause of more than 5 minutes (usually the quota is then used up), the run aborts with a message. If the network fails, the run waits increasingly longer after five errors in a row and only aborts after 30 minutes; if five units in a row remain without a valid answer, it aborts (the model then does not follow the output format). The Run tab shows every error reason. A new start repeats the failed calls; valid and invalid answers are kept.

### 8. Results

LLM code, **Consistency (p_sc)** (share of repetitions with this code), **Self-assessment (p_verbal)** (the confidence the model states), alternatives and **Notes** in plain language (such as “Panel disagreement” or “signal words not in the text”); the switch “Show raw answers” shows the raw answers. In the detail view, the signal words are highlighted in the text. The **Reference** column shows the gold (consensus, otherwise majority, otherwise the first gold column); the filters “deviates from the reference”, “uncertain” and “dev only” help you refine the codebook. Test gold stays hidden until the freeze.

### 9. Review

Have humans code uncertain cases and a random sample: **Create review list**, then code unit by unit. If you switch to “assisted”, you confirm this first, because suggestions you have seen lock the blind review of these units.

### 10. Evaluation

Metrics against gold and reviews, methods report. At the top is a **Summary verdict** with traffic lights: agreement with the main reference (Krippendorff's α: green from 0.80 if the lower interval bound is at least 0.667, yellow from 0.667, red below), comparison with the agreement among the humans (green at most 0.05 below their α, yellow up to 0.15 below), weakest code (F1 among the codes with at least 20 reference cases: green from 0.70, yellow from 0.50), enough gold (at least 30 units and an α interval no wider than 0.20) and robustness (prompt sensitivity, measurement error by subgroup), plus “What now”. Without gold, the reference is the blind-reviewed random sample; the next step is then to review more or to report. Values that cannot be computed (such as α when only one code occurs) stay gray. These are rules of thumb, not laws. The main table first shows the core metrics and marks the main comparison; **Show all metrics** displays the others, and XLSX and report always contain all of them.

### 11. Export

Grouped into Analyze (XLSX), QDA software (REFI-QDA), Label Studio, and Publish (methods report, replication package). Label Studio exports without LLM suggestions by default. After saving, **Open folder** opens the target folder.

## Methodological recommendations

### Dev/test split and freeze

If you improve the codebook or prompt on the same data on which you measure quality, you overestimate quality (“LLM hacking”, Baumann et al. 2025). Therefore:

1. In the **Splits & freeze** tab, create a split (default 25% dev). With a group column (such as transcript), whole groups are drawn, also in isolated mode: in context mode, a prompt would otherwise see rows from the other part, and units of one transcript are not independent.
2. In the **Run** tab, code with “code development part (dev) only”, evaluate dev in the **Evaluation**, look at the confusions under Codebook → **Check disagreements**, refine codebook and prompt, and repeat.
3. **Freeze.** The instrument hash records codebook, prompt, models including digest, parameters, gate rules and split.
4. Export the **preregistration** and deposit it, for example at OSF.
5. Start the **full run** and evaluate the test set once.

**How much gold?** The calculator in the Splits & freeze tab estimates how many test units are needed so that the 95% interval for κ (LLM vs. gold) is, on average, about as wide as desired, for example ± 0.10 with an expected κ = 0.70, and how many units with gold this requires including the dev share. In an individual case, the interval can turn out wider (in simulations, in just over 40% of samples); if you want to be on the safe side, plan with a narrower target. The calculation is based on the large-sample variance of κ (Fleiss, Cohen & Everitt 1969) with the distribution of the split reference, for multi-label with the distribution of whole code sets, as for κ in the evaluation; without gold, it assumes equally frequent codes. Skewed distributions need considerably more units. The calculator gives at least 30 units, and enough units that about 10 lie outside the most frequent category; below that, the approximation does not hold. With a split, it also shows the width that the current test set can be expected to give. The number applies to independent units; if units are linked through groups, more are needed.

Every test evaluation is logged and counted in the report. Exports before the freeze leave out the test units by default (switch “Before the freeze export dev only” in the Export tab; the statistics export of the evaluation follows the test lock). If you export them anyway (XLSX, REFI-QDA, replication package), you are asked to confirm; the export is logged and named in the report, because computing metrics from it outside the program circumvents the test lock. If you change something after the freeze, you break it; the program then locks runs and shows this in the status line. Lifting the freeze requires a reason, which is recorded in the log.

### Prompt sensitivity

Small changes to the prompt can shift results and conclusions: in 37 annotation tasks, such choices led to wrong conclusions in about 31% of cases for state-of-the-art models (Baumann et al. 2025). Therefore, use **“Check prompt sensitivity”** in the Run tab:

- The same units (dev, without a split all units; if there are many, a fixed random sample, default 100) are coded with three rewordings of the instructions, and in context mode also without context. Task, rules for signal words and confidence, and the output format stay the same.
- Each variant runs once per unit, as a separate run identity. Main results and freeze are not affected.
- The evaluation shows Krippendorff's α across the original and the rewordings (inter-prompt, like the Prompt Stability Score of Barrie et al. 2024), and per variant the agreement with the original, α against the reference and the code shares, all on the units that are validly coded in all wordings. Below α 0.8, it warns: then report only conclusions that hold in all wordings. “Without context” does not count towards α, because it changes the unit of analysis, not the wording; it is shown as a separate row.
- **“Reasoning niedrig” (low reasoning):** For models that think before answering, the original additionally runs with reasoning effort “low”. With an OpenAI-compatible provider, a model counts as thinking if the effort is set (and not already low) or if its previous answers used clearly more output tokens than the visible text needs; this becomes apparent at the latest in the original run of the sensitivity check. This row does not count towards α. The evaluation gives the agreement with the original, α against the reference and, per variant, the mean output tokens and the response time (without waiting times due to rate limits). If agreement holds and the tokens drop, setting “low” in the Models tab saves quota and time; that is a new instrument, so switch before the freeze. If the tokens hardly drop, the provider may be ignoring the effort setting; the evaluation then says so. If the provider rejects the parameter, only this row aborts; the other variants remain valid.
- The variants run with temperature 0 so that α does not measure random variation; if the provider fixes the temperature, the report says so. After a change to codebook, prompt or panel, earlier sensitivity runs are no longer valid and must be run again.
- Ideally, check sensitivity on dev before the freeze, and do not keep tweaking the prompt until one variant fits: the number of run identities is reported in the GUIDE-LLM checklist.

### Runs, temperature and panel

- **Several repetitions** (default 5, adaptive, stopping after 2 agreeing repetitions; projects from before 2026-10-08 keep 3) provide the self-consistency confidence: the share of repetitions that assign the same code. It is usually better calibrated than the confidence the model states itself. Both are stored.
- With several repetitions, the automatic temperature is 0.7, otherwise 0.
- Some providers set the temperature themselves and silently ignore the value, at KI:connect.NRW for example gpt-oss-120b and the GPT-5 models (see the provider's model overview). In that case, choose **Temperature “do not send”** in the panel. The program does not send it, and report and preregistration state “set by the provider” instead of a number that did not apply. A single run is then no longer greedy.
- **Reasoning effort** (`reasoning_effort`) controls how long reasoning models (gpt-oss, GPT-5, Qwen) think. It changes results and run time and is therefore part of the run identity and the report. “do not send” means: the provider's default. The levels are listed in the provider's model overview; the field also accepts custom values. Only for OpenAI-compatible providers.
- **Model change:** If the provider reports different model names during a run, the evaluation warns, and the report lists the reported names. This can also be load balancing across several servers: during the acceptance run, KI:connect.NRW distributed gpt-oss-120b randomly across two vLLM servers with their own names. The “Läufe” sheet in the XLSX export gives the name per call. A provider can swap the model behind an API name without the name changing; no program can detect that.
- In the **panel**, each model counts equally. Mix models from different families. However, this helps only to a limited extent: models from different families also agree more strongly with each other than with human coding (Stolwijk et al. 2025). Agreement between models is therefore a signal of uncertainty, not a measure of quality.
- **Dawid-Skene as a cross-check** (single-label, three or more models): without gold, the evaluation estimates the accuracy of each model (Dawid & Skene 1979, one-coin variant: one rate per model, errors evenly distributed) and shows how many units a panel weighted accordingly would code differently, and which κ against the main reference it would have. As with the panel, the basis is the share of repetitions per model; unlike the panel, the estimated accuracy of each model and the estimated frequency of the codes also count. The panel itself remains equally weighted: the method assumes that the models err independently of each other, and this assumption is usually violated with LLMs. If the two values differ clearly, it is worth looking at the weakest model.
- If a model has no valid result for a unit (abort, invalid answers), the remaining models decide. The unit then carries the flag **panel incomplete** (filter in the Results tab), and evaluation and report give the number of such units.

### Costs and quota

At GWDG/KISSKI, the quota counts requests (per minute, hour, day and month). At KI:connect.NRW, each university sets limits for requests, tokens or costs. Commercial providers bill per token, with output usually considerably more expensive than input. In the KI:connect acceptance test (gpt-oss-120b, context mode with two lines before and one after), about 63% of a call consisted of the system prompt with the codebook, 23% of reasoning, 8% of the visible answer, and only 6% of target text and context. What helps:

- **Adaptive stop** (default: after 2 agreeing repetitions): a clear-cut unit needs 2 instead of 5 calls.
- **Low reasoning effort**, if the prompt sensitivity check shows that it yields the same codes.
- While refining, use the **pilot and “dev only”** and one model instead of the panel; use the panel only for the run after the freeze.
- The Run tab shows usage and remaining quota (see Academic APIs).

Shortening the context saves little. The codebook is sent with every call; codebook and instructions come first and are the same within a run, so providers with prompt caching (OpenAI, vLLM) can reuse them. Deliberately not built in: several units in one request (changes the instrument considerably and has not been tested for codebook-based analysis), several answers in one request (`n`: all repetitions would have the same code order, and GWDG and KI:connect do not document the parameter), batch APIs (−50%, only with commercial providers), and a panel as a cascade in which the second model codes only uncertain units (the metrics between the models would then apply only to the difficult units).

### Review

- The review list (queue) combines two routes. The **selection of uncertain cases (gate)** includes: low consistency (self-consistency), panel disagreement, residual category, unsupported signal words, invalid answers. The **random sample** draws from all units with a logged probability.
- **τ as accuracy:** With the switch “τ as estimated accuracy” (under Advanced), τ does not apply to the self-consistency itself but to the accuracy estimated from it. When you switch it on, the program learns, on the dev units with gold (without a split on all units, at least 20), an isotonic mapping from the self-consistency of the least certain final code to the rate of correct codings, measured against the reference of the split. τ = 0.8 then means: review if the estimated accuracy is below 80%. Test units are never used. The mapping is part of the instrument: the freeze records it, a new wave of data collection from this instrument takes it over, a new code system does not (it has its own codebook). To relearn the mapping, for example after adding more dev gold, switch the switch off and on again. If self-consistency hardly separates correct from incorrect codings (AUROC in the calibration part of the evaluation), many units receive the same value, and the gate selects all or none. The “uncertain” filter in the Results tab follows the same rule.
- In **blind** mode (default), the person sees only text and codebook. Only blind reviews count as gold, because visible LLM suggestions bias judgement. Anyone who has seen a unit in assisted mode can no longer review it blind; this applies as soon as the suggestion has been displayed, even without saving. Assisted mode therefore shows suggestions only after you enter your name.
- Work through the review list **completely**: skipped units of the random sample make it no longer random; the evaluation then warns.
- Only metrics against the random sample are **unbiased**. The gate deliberately selects difficult cases; metrics against “all reviews” therefore turn out too low and are marked accordingly.

### Reading the metrics

- Never report **percent agreement** alone.
- Report **Cohen's κ** alongside **Krippendorff's α** and **Gwet's AC1**. With a skewed distribution, κ can be low despite high agreement (kappa paradox); AC1 and PABAK help with interpretation.
- Krippendorff (2018) calls α ≥ 0.800 reliable and α ≥ 0.667 tentative. Such thresholds are conventions, not laws.
- **Human–human baseline:** How well do the human coders agree with each other? An LLM that comes close to the baseline is as good as another trained person.
- **Alt-Test** (Calderon et al. 2025, three or more coders): tests whether the LLM can replace a human coder. Choose ε according to the comparison group: experts 0.2, trained coders 0.15, crowd workers 0.1.
- **Per code:** precision, recall and F1. Codes with fewer than 20 reference cases are marked; their values fluctuate strongly. The confusion matrix can be shown as row percentages; the diagonal then shows the recall per reference code.
- **κ and ρ per code** (Evaluation tab, table “Details: LLM vs. … (per code)” at the bottom of the report; the “Details … for” choice on the left jumps there): κ for the code as yes/no, and **Shaffer's ρ** (Shaffer 2017; reimplemented following the R package rhoR): the share of simulated test sets of the same size, drawn from populations with κ between 0.40 and 0.65, that reach the observed κ. ρ < 0.05 indicates that κ across all units is above 0.65. The values match rhoR (checked against rhoR 1.3.1). Limits: the error rate of 5% holds on average over this range; ρ is not a test against exactly 0.65. If the true κ is just below 0.65, ρ wrongly says “above” considerably more often, and the more so the larger the test set (in simulations about 10–20% with 50 units, 30–50% with 500). ρ assumes independent units; with groups, it turns out too small. From 10,000 compared units upwards, there is no ρ (as in rhoR).
- **Copy methods paragraph** (German or English) copies the paragraph for the methods section to the clipboard, with metrics as soon as a current evaluation has been computed.
- **Calibration:** Is the confidence suitable for finding uncertain cases? Per signal (self-consistency, verbalized confidence, and in the panel the share of agreeing models):
  - **AUROC** separates correct from incorrect codings (0.5 = not at all); **AURC** is the mean error rate when units are accepted in order of descending confidence.
  - **ECE** measures the gap between confidence and accuracy; **ECE recalibrated** shows how good it would be after an isotonic recalibration (five-fold cross-validated). Verbalized confidence is often poorly calibrated, and filtering by it biases estimates (Wang, Deng & Yang 2026): never use it uncalibrated as a threshold.
  - For each signal, the evaluation also shows the **reliability diagram** (accuracy per confidence bin against the diagonal) and the **risk-coverage curve** (error rate of the accepted units; dashed, the error rate without selection). If the curve lies clearly below the line on the left, the confidence is useful as a filter.
  - **Agreement of runs:** accuracy per level of agreement, for example 3/3 next to 5/5. The adaptive stop gives both a self-consistency of 1.0, but they are not equally certain.
- **Intervals:** 95% bootstrap intervals, drawn by group if there is a group column (also in isolated mode), are available for agreement, Cohen's κ (also weighted), Krippendorff's α, Gwet's AC1, Scott's π, PABAK, accuracy, macro-F1, MCC, top-k, the values at the level of code groups, and the PPI prevalence. Point values without an interval are the per-code values, micro-F1, exact match, Jaccard, Hamming loss, abstain rate, coverage, calibration and stability.
- **Subgroups** (speaker, group, metadata) show whether the LLM codes worse for some parts of the data.
- The program always checks **measurement error by subgroup**, for every subgroup variable: does the error rate against the main reference depend on the variable (Wald test, cluster-robust, Holm-corrected)? If so, analyses by this variable with LLM codes would be biased, even with high overall quality (Egami et al. 2023). In that case, include human labels (PPI, dsl). Values with fewer than 5 units are dropped; the group itself is dropped when clustering is by group.

### Prevalence: PPI and dsl

If you want to report shares of codes, you should not simply count the LLM labels, because systematic errors shift the shares.
- **PPI++** (Angelopoulos, Duchi & Zrnic 2023) corrects the LLM shares using the random review sample. The result is unbiased. Power tuning λ weights the LLM labels so that the interval is asymptotically never wider than that of the sample alone (λ = 0) or that of PPI (λ = 1). With a group column, the interval is cluster-robust across transcripts, because units of a transcript are related. The evaluation shows PPI automatically as soon as blind reviews of the random sample are available, as a table and as a chart (point and 95% interval per estimator).
- **All reviews weighted** (confidence-driven inference, Gligorić et al. 2025): PPI++ uses only the random sample, because the gate deliberately selects difficult cases. The weighted estimate additionally uses the blind reviews from the gate and weights each one by its selection probability in the current queue (gate 1, random sample n/N), with power tuning as in PPI++. It is then unbiased and usually considerably narrower, because the reviews are where the LLM errs most often. Conditions that the program checks: a random share above 0 (otherwise the error outside the gate would remain uncorrected) and a **fully completed queue**; as long as units in the queue have no blind review, the column is missing, and the evaluation says how many are still open. Only units of the current queue count: if the queue is redrawn (different threshold, share or seed), reviews from earlier draws no longer belong to it. Reviews outside the queue do not count either.
- For both estimators: power tuning only from 20 randomly drawn, reviewed units onwards; below that, λ = 1 (otherwise the interval becomes too narrow). Cluster-robust intervals use the t quantile with G − 1 degrees of freedom (G = number of groups). The intervals refer to the prevalence in the population from which the units come; for exactly the coded units, they are somewhat too wide.
- For regressions, the export to R contains the columns for `dsl` (Egami et al. 2023). The included R script shows an example.

### Rationales

The model's rationales are post-hoc rationalizations, not an explanation of its decision (Turpin et al. 2023). What can be checked are the signal words: they must appear verbatim in the text; otherwise the answer is marked as “unsupported”.

## Privacy

- Local is the default. Before every run with an external provider, the program asks for consent and records it in the log. “Local” applies only if the address is this computer (localhost, 127.0.0.1); an Ollama server on the network counts as external, even if it is entered as local. If an API key would go unencrypted (http) to another computer, the Models tab and the consent dialog warn you.
- Check beforehand whether the data are pseudonymized and whether the provider is permitted for them.
- **Replace names before sending** (Prompt & units tab → Replace names…): a list of original → replacement, for example “Lena” → “Person 1”. “Add speakers” enters the values of the speaker column, except abbreviations of up to two characters such as “I” or “L” (roles; otherwise every such word in the text would be replaced). “From file…” reads a table (first column original, second column replacement) or the `pseudonyme.tsv` of another project. The Matches column shows how often a name occurs in text, speaker and metadata, and in parentheses the matches in a different spelling (“lena”, “LENA”), which are not replaced; create separate entries for these.
  - Names are replaced in text, speaker and metadata, as whole words and with a genitive s (“Lenas” → “Person 1s”); “Timo” is left untouched by “Tim”, and case matters. Unicode variants of the same name (for example from PDF or macOS) and multi-word names with a non-breaking space or line break are recognized. A replacement must not itself contain a name from the list.
  - **Codebook and IDs stay unchanged** and go to the model as they are: remove names from anchor examples and from unit or group IDs beforehand.
  - The preview shows the prompt with the names replaced. Data, results and exports keep the originals; signal words are highlighted in the original text, while rationales and the signal words themselves contain the pseudonyms.
  - The list is stored as `pseudonyme.tsv` in the project folder, not in `project.json`, and goes into the replication package only with the raw texts; without it, the rerun needs `--pseudonyme`. Earlier versions are kept in `pseudonyme_alt/` so that older packages can also be recomputed. If a data deletion policy requires deleting the mapping, remove both: evaluation, report and exports continue to work, only without an example of a user message; new runs and the preview then no longer start.
  - A change to the list starts a new run at the next coding and breaks a freeze; the dialog warns about this. If the file is changed or deleted outside the program, no run starts until the list has been saved again in the tab; this way, names do not go out unnoticed.
  - A list recognizes only the names it contains; nicknames, typos and other spellings must be added individually.
- Without the switch “Include raw texts”, the **replication package** contains only IDs, codes and metrics; texts, metadata, raw answers, rationales, signal words and review notes are then missing. Speakers and reviewers appear as pseudonyms (S01, R01 …) in the package, and error messages in the log lose the quoted answer text of the provider. Hashes of individual texts or prompts are left out, because short texts such as “Yes.” could be guessed; the joint checksum of all units is in `project.json`. Unit and group IDs stay unchanged unless you set the switch “Pseudonymize unit and group IDs”: then they appear as E01, G01 … in the package. The mapping is not in the package; `llmanno-rerun` reconstructs it from the original data (same rows, same order) and compares as usual.

## Exports

Report, preregistration, GUIDE-LLM checklist and the description in the replication package are available in German and English: **Language → Report, preregistration, replication package**. The choice applies immediately and independently of the interface language. The methods report always contains the methods paragraph in both languages, the selected one first; the prompt in the appendix is in the language of the prompt template. Notes in the evaluation appear in the interface language.

| Export | Where | Contents |
|---|---|---|
| Results (XLSX) | Export | one row per unit including split (dev/test) and review status (blind, assisted, open), all individual repetitions, codes, reviews, hashes |
| REFI-QDA (.qdpx) | Export | for MAXQDA, ATLAS.ti, QualCoder, NVivo (see below) |
| Code system (.qdc) | Export | the codes only |
| Label Studio (.json and .xml) | Export | one task per unit with target text and context, optionally with LLM suggestions (final codes, signal words as spans); paste the .xml under Settings → Labeling Interface and import the .json. Suggestions steer judgement like an assisted review. Gold is not included; before the freeze, with “dev only”, test units are exported without suggestions. INCEpTION is not built in: it expects its own layers and type systems; for QDA software, there is REFI-QDA. |
| Replication package (.zip) | Export | everything needed to retrace the coding and rerun it |
| Metrics (XLSX) | Evaluation | all metrics with intervals, per code, confusion, calibration, Alt-Test, subgroups, measurement error, PPI |
| Methods report (Word or HTML) | Evaluation or Export | report including the methods paragraph in German and English and a pre-filled GUIDE-LLM checklist; the HTML shows the reliability diagram, risk-coverage curve and prevalence as charts, Word gives the numbers as tables. Also works without an evaluation (pilot phase), then without metrics. |
| SPSS, Stata, R | Evaluation | one row per unit, codes with value labels, split and review status (for filtering, for example to the test set), with a review queue also the `dsl` columns |
| Preregistration (Markdown or Word) | Splits & freeze | frozen instrument and analysis plan; Word for OSF and ethics applications |

### REFI-QDA in QDA software

Programs that support the REFI-QDA standard import the project via their import function for REFI-QDA projects. The project contains:

- **Texts:** one text source per group, each row as “[ID] Speaker: Text”.
- **Persons:** “LLM-Panel”, each gold column and each reviewer appear as separate persons in the project. This lets the QDA software recompute the intercoder agreement of LLM vs. human with its own function.
- **Codes:** the codes of the codebook. The signal words are in a separate branch “LLM-Evidenz” so that they do not distort the comparison of the main codes.
- **Notes:** one note per unit with top-k, both confidences, rationale and flags; plus the codebook verbatim.

The import into QualCoder has been checked; MAXQDA, ATLAS.ti and NVivo have not been tested yet, and feedback is welcome.

**Getting gold back from the QDA software:** If you prefer to code in the QDA software, export the project as REFI-QDA, code the texts there (each person with their own user account), export again as `.qdpx` and choose **Gold from QDA project…** in the Data tab. New human coders are preselected, not the LLM and no existing gold column. Each selected person becomes a gold column “<Name> (QDA)”. A coding counts for every unit whose row it overlaps; for single-label, the code with the largest share applies. The program maps codes by name (“K1” or “K1 …”); the import report names codes without a counterpart in the codebook. The program finds the units via the “[ID]” markers in the text, so do not rewrite the texts in the QDA software. The gold is taken over as in a gold update: run identities and freeze remain, and the previous data copy stays as a backup. A second import of the same person replaces their column. The columns are named after the users in the QDA software and appear under these names in the replication package without raw texts too; so use initials there rather than real names. CSV projects get the data with the new columns back as TSV.

### GUIDE-LLM checklist

Report and replication package (`guide_llm.md`) contain the reporting checklist GUIDE-LLM (Feuerriegel et al. 2026, *Nature Human Behaviour*, [llm-checklist.com](https://llm-checklist.com)): 14 core items, from purpose through models, parameters, prompts and privacy to validation, plus recommended additional information. The program fills in what it knows from the project, the audit log and the evaluation, including the number of run identities (how often prompt, codebook or parameters were changed and the data coded again). Fill in the rows marked “→ to complete” yourself, for example conflicts of interest, ethics and the rationale for the choice of models.

## Replication

```bash
uv run llmanno-rerun replikation.zip --out rerun
```

The rerun creates a new project, codes with the same settings and seeds, and compares every decision with the original. If the model build has changed (different digest), this is reported. If the package contains no raw texts, provide the original data with `--data`; the program checks them against the unit hash. If the original replaced names, the list of names from the project folder is also needed (`--pseudonyme pseudonyme.tsv`); it must be the same list. External providers require `--consent`.

## Troubleshooting

| Message | Cause and remedy |
|---|---|
| “Ollama is not reachable” | Start `ollama serve` |
| “Model … is not installed in Ollama” | Load the model with `ollama pull …` or check the name |
| “Prompt probably truncated” | The run aborts because the prompt does not fit into the context window and Ollama would otherwise silently truncate it. Use a shorter codebook, fewer context lines or a model with a larger window |
| Very slow run | The model does not fit into the graphics memory. Choose a smaller model or use an API. For small models (under 10 billion parameters), the follow-up (repair) is switched off anyway as soon as they are added to the panel |
| “Freeze broken” | Something was changed after the freeze. Undo the change or lift the freeze with a reason |
| “The test set is locked until the freeze” | Evaluate only dev before that, then freeze |
| “The model only allows its own temperature” | Affects for example GPT-5 and OpenAI's o models. In the Models tab, choose “do not send” (lowest value) in the Temperature column. That such models require `max_completion_tokens` instead of `max_tokens` is handled by the program itself. |
| “… requests a pause of N min (Retry-After), the quota is probably used up” | The daily or hourly quota at the provider is used up. Resume later; limit the requests per minute in the Models tab |
| HTTP 401 or 403 | API key missing, expired or not valid for this model. In the Models tab, click **Remove key** and save the key again |
| “The project comes from a newer llmanno version” or “… settings that this llmanno version does not know” | The project was saved with a newer version. Update llmanno |
| Store migration | When a newer llmanno version opens an older project, it first backs up the database as `store.vor-migration-<n>.sqlite` in the project folder |

## Glossary

The interface gives the plain-language term first and, in parentheses, the technical term that is also used in the methods section and in the literature.

- **Unit of analysis:** what the model codes at once: a line, a line with context, or a whole group (such as a transcript).
- **Context (context window):** neighboring lines of the same group that the model reads but does not code.
- **Gold (reference coding):** the human codings against which the quality of the LLM is measured, one column per person, optionally plus a consensus.
- **Codebook and code list:** The codebook is the text with definitions, rules and anchor examples; it goes to the model unchanged. The code list determines which codes the model may assign and which are evaluated.
- **Residual category (KEIN_CODE):** allows the model to say that no code fits, instead of forcing one.
- **Coding mode (single-/multi-label):** one code per unit (others only as alternatives) or several codes per unit.
- **Instructions (prompt):** what the model receives: the system message with task and codebook (the same for all units) and the user message with the text of the unit.
- **Provider and model:** who runs the model (Ollama on your own computer, GWDG/KISSKI, KI:connect.NRW …) and which model codes.
- **Panel:** several models that code the same units; the decision is joint, and each model counts equally.
- **Repetitions (runs, N):** how often each model codes the same unit. A **run** in the Run tab (pilot, full run) codes all selected units with all repetitions.
- **Consistency (self-consistency, p_sc):** share of the repetitions of all models that yield the same code. The most important measure of a unit's uncertainty.
- **Self-assessment (p_verbal):** the confidence the model states itself; usually too optimistic.
- **Adaptive stop:** ends the repetitions for a unit as soon as enough of them yield the same code.
- **Seed:** makes randomness reproducible, for example the order of the codes or the drawing of a sample.
- **Temperature:** how much a model's answers vary; 0 means as consistent as possible.
- **Reasoning effort:** how long a thinking model (gpt-oss, GPT-5, Qwen3) thinks before answering.
- **Tokens:** pieces of text, mostly parts of words, by which providers bill and limit lengths.
- **Quota:** how many requests or tokens a provider allows per minute, day or month.
- **Signal words (evidence):** words from the text with which the model supports its code; the program checks whether they occur verbatim.
- **Follow-up (repair):** if an answer is unusable or the evidence is missing from the text, the program asks the model once more.
- **Development part (dev) and test part (test), split:** The codebook and prompt are improved on the development part, as often as you like. The test part stays locked until freezing and, at the end, measures independently how good the instrument is.
- **Instrument:** codebook, code list, prompt, models and their settings together.
- **Freezing (freeze):** fixes the instrument before the test part is evaluated. Changing anything afterwards breaks the freeze; this is stated in the report.
- **Preregistration:** the analysis plan deposited publicly (for example at OSF) before the result is known.
- **Pilot:** a short run with 20 units to test model and prompt and to extrapolate the duration.
- **Prompt sensitivity:** checks whether the codes depend on the wording of the instructions.
- **Review list (review queue):** units that humans review: uncertain cases and a random sample.
- **Selection of uncertain cases (gate), threshold τ:** proposes units for review whose consistency is below τ or that carry other warning signs.
- **Blind or assisted:** blind without an LLM suggestion (counts as gold), assisted with a visible suggestion (does not count as gold).
- **Agreement metrics:** percent agreement; **Cohen's κ** and **Krippendorff's α** correct for chance (α reliable from 0.80, tentative from 0.667); **Gwet's AC1** is robust when codes differ greatly in frequency; **F1** measures, per code, how well hits and misses are balanced.
- **Confidence interval:** the values in square brackets; the range in which the true value lies with 95% certainty.
- **Prevalence and PPI++:** how often a code occurs, corrected with the blind reviews of the random sample.
- **Code system:** a further codebook on the same data, with its own evaluation.
- **REFI-QDA:** exchange format for QDA software (MAXQDA, ATLAS.ti, NVivo, QualCoder).
- **Replication package:** ZIP file with everything others need to check and recompute the coding.
- **Log (audit log):** unchangeable list of all steps in the project; the basis of report and replication package.
