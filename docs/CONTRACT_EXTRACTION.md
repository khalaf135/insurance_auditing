# Rebuilding contract drafts

You do not need to rerun extraction to audit invoices. Current source-linked
drafts are in `contract_agent_final_v4/`; the frozen H1 input remains separately
in `structured_contracts/hospital_1.json`.

`contract_builder.py` reads each `contracts/hospital_N/` in isolation, including
multiple documents and amendments. It records source lines, original wording,
uncertainties and exact monetary values. Literal source-table checks are included
and enabled by default. Output is a review draft, not a guarantee of completeness.

Python 3.11+ is sufficient for the Python code. PDF extraction additionally needs
local Poppler (`pdftotext`); scanned pages without text equivalents need Poppler's
`pdftoppm` and Tesseract, enabled with `--ocr`.

Run these commands from the `insurance_auditing/` directory. The default is a dry
run that inventories sources without API calls. Use a fresh output directory:

```sh
python3 contract_builder.py --output-dir audit_output/contract_inventory_new
```

Re-extract using the saved original responses, without new API calls:

```sh
python3 contract_builder.py --offline --cache-dir contract_agent_output/api_cache --output-dir audit_output/contracts_replayed_new
```

The original extraction responses and usage are retained in
`contract_agent_output/api_cache/` and `contract_agent_output/usage.json`.
Exact offline replay needs that cache and unchanged source text, model and chunk
settings; do not remove the cache as an old draft folder. A cache miss stops
extraction without contacting the provider. Incomplete drafts use a
`.partial.json` filename.

Add `--hospital hospital_2` to select one hospital; repeat the option for several.
The default includes all hospital folders. Keep output outside source contracts
and `structured_contracts/`; existing complete hospital JSON files are preserved.

New paid extraction requires explicit `--execute` and `openrouter_api_key` in the
environment or local `.env`. Voyage is not required. `--budget-usd` defaults to
USD 1 and accepts USD 0.15–10. This uses `ai_client.API`, with a usage ledger in
the selected output directory; it is a local spending check, not a provider cap.
It is separate from the invoice runner's `ai_client.BudgetedAI` persistent USD 2
allowance. Extraction does not consume or enforce that invoice allowance.

Inspect the available extraction options without API calls:

```sh
python3 contract_builder.py --help
```

For library use, `contract_builder.convert_h1(text, source_name)` retains the
reviewed H1 text converter and rejects changed prose. `contracts.py` provides
`compile_contract`, effective-date pricing, service dependencies, and source-bound
unit review through `ClauseGraph`, `review_units`, and `load_and_preflight`.
These are library interfaces; `contract_builder.py` is the extraction entrypoint.

Review changed drafts and run `python3 test.py` before using them for invoices.
Changing contract facts invalidates old task-replay provenance; do not bypass
those checks by editing hashes. Compound units and amendment precedence require
actual source support and may remain unresolved. The frozen H1 input remains a
separate reviewed artifact.
