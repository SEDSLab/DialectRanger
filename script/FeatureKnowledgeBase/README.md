# Feature Knowledge Base — DialectRanger integration

The crawler runtime is self-contained here. `crawler_cli.py` and `agents/` are
the copied generic pipeline; `run_feature_crawler.py` adds repository output and
static-web publication. No parent FeatureCrawler source is imported at runtime.

From the DialectRanger root (the current local checkout is `src/`):

```powershell
python -m pip install -r script/FeatureKnowledgeBase/requirements.txt
$env:FEATURE_CRAWLER_HTTP_TRUST_ENV = '0'
python script/FeatureKnowledgeBase/run_feature_crawler.py build --dbms sqlite --semantic-provider deterministic
python script/FeatureKnowledgeBase/run_feature_crawler.py serve --port 8771
```

Open http://127.0.0.1:8771/ . This is the existing DialectRanger web UI, not
the old FeatureCrawler API viewer. GitHub Pages only needs the `web/` directory.

`--docs-manifest` and `--profile` can override the defaults under this tool's
`inputs/<db>/`. Explicit paths resolve from the caller's current directory;
default paths resolve from the tool directory. `--cache-only --cache-dir PATH`
replays existing frozen HTML without network requests.

Builds use local deterministic Semantic, with no hosted-model fallback. This
integration entry deliberately accepts only `--semantic-provider deterministic`.
The lower-level crawler CLI is retained for engine maintenance.

Successful output:

```
data/FeatureKnowledgeBase/<db>/final.json
data/FeatureKnowledgeBase/<db>/review.json
data/FeatureKnowledgeBase/<db>/summary.json
data/FeatureKnowledgeBase/catalog.json
web/js/data.js
```

`catalog.json` lists the authoritative new datasets. Existing per-type legacy
artifacts are preserved but never scanned into a registered new dataset.
The exporter regenerates **all** Final/Review web rows exclusively from the
registered datasets. Historical `data.js` rows are never imported. Only DBs
listed in `catalog.json` appear after export; unregistered legacy files on
disk are left untouched. Remove a DB from the catalog and re-export to hide it.

Descriptions retain ordered distinct paragraphs. Only semantic syntax claims
populate web Syntax; identity variants and aliases remain separate. Missing
fields stay empty. Official source links come from field evidence first.
Raw Final and Review data are retained unchanged; machine-specific runtime
pointers are omitted from the portable summary. Caches and execution evidence
stay under `.runtime/` and are not committed.

Re-export data or publish an existing successful crawler output:

```powershell
python script/FeatureKnowledgeBase/run_feature_crawler.py export-web
python script/FeatureKnowledgeBase/run_feature_crawler.py publish --dbms sqlite --public-root PATH_TO_THREE_FILE_OUTPUT
```

Offline adapter tests (from this directory):

```powershell
python -m pytest tests/test_dialectranger_integration.py -q
```
