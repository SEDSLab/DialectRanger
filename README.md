# DialectRanger

**DialectRanger** is a DBMS knowledge infrastructure for collecting, structuring, and organizing dialect-specific knowledge from heterogeneous sources.

The system is designed around two complementary knowledge bases:

- **Feature Knowledge Base** — extracts structured DBMS feature knowledge from official documentation, including functions, operators, data types, syntax, semantics, examples, evidence, and provenance.
- **Bug Knowledge Base** — organizes structured knowledge from historical DBMS bugs and crash-triggering inputs. **Under Development.**

Together, the two knowledge bases aim to capture both **what a DBMS supports and how its features are used**, and **what feature behaviors or interactions have historically been associated with failures**, providing reusable knowledge for downstream database research and testing.

---

## Contents

- [🧩 Feature Knowledge Base](#-feature-knowledge-base)
  - [🌐 Website](#-website)
  - [📊 Feature Counts](#-feature-counts)
  - [📚 Supported Research](#-supported-research)
  - [🔧 Extraction Pipeline](#-extraction-pipeline)
- [🐞 Bug Knowledge Base](#-bug-knowledge-base)
- [📁 Project Structure](#-project-structure)
- [🧪 Testing](#-testing)
- [⚠️ Limitations](#-limitations)

---

## 🧩 Feature Knowledge Base

Automated extraction of structured feature knowledge bases from official DBMS documentation — driven by profiles rather than hardcoded DBMS-specific rules.

Feature Crawler discovers official documentation pages, classifies them by feature type, parses documentation into structured sections, extracts function/operator/datatype features, validates evidence references, and produces curated Feature Knowledge Base snapshots for downstream database research and tooling.

**10 DBMS · 5,762 Features · 3 Feature Types**

The goal of the Feature Knowledge Base is to provide a unified, structured representation of DBMS-specific SQL capabilities extracted directly from official documentation.

Database systems expose thousands of functions, operators, and data types, but this information is typically scattered across heterogeneous documentation pages and represented using different formats and terminology.

Feature Crawler converts this documentation into a machine-readable knowledge base.

Each feature record may contain structured information such as:

- feature name
- DBMS
- feature type
- signature
- syntax patterns
- semantic descriptions
- usage examples
- expected results
- source sections
- evidence references
- provenance metadata

The resulting knowledge base can support downstream tasks including:

- **DBMS feature discovery** — systematically identify supported functions, operators, and data types
- **Cross-DBMS feature analysis** — compare feature availability and syntax across database systems
- **SQL generation and fuzzing** — provide DBMS-aware feature information for SQL generators and testing systems
- **Feature mapping** — support function/type/operator mapping between different DBMSs
- **Feature-aware SQL instantiation** — provide syntax and semantic constraints for generating valid SQL instances
- **Database compatibility analysis** — identify equivalent, missing, or DBMS-specific capabilities
- **Research tooling** — provide structured DBMS knowledge for automated database testing and analysis

The knowledge base is designed as an intermediate representation between heterogeneous DBMS documentation and downstream automated systems:

```text
Official DBMS Documentation
            │
            ▼
      Feature Crawler
            │
            ▼
Structured Feature Knowledge Base
            │
     ┌──────┼───────────┬──────────────┐
     ▼      ▼           ▼              ▼
 SQL Fuzzing   Feature Mapping   SQL Generation   DBMS Analysis
```

### 🌐 Website

🔗 **Online Knowledge Base Browser:** *link to be added after deployment*

The browser provides an interactive view of the extracted Feature Knowledge Base.

Current functionality includes:

- full-text search over feature names, signatures, syntax, descriptions, and review reasons
- dynamic filtering by DBMS and feature type
- DBMS → Feature Type sidebar navigation
- hierarchical expand/collapse browsing
- expandable feature cards
- syntax and signature visualization
- description and example inspection
- source documentation references
- metadata inspection
- review queue browsing for flagged features
- extraction pipeline visualization

The website is implemented as a dependency-free static application using:

```text
HTML + CSS + JavaScript
```

and is designed for direct deployment through GitHub Pages.

### 📊 Feature Counts

#### MySQL (v8.0)

🔗 [Official Documentation](https://dev.mysql.com/doc/refman/8.0/en/)

| Feature Type | Count |
| ------------ | ----: |
| Function | 485 |
| Operator | 46 |
| Data Type | 38 |
| **Total** | **569** |

#### PostgreSQL (current)

🔗 [Official Documentation](https://www.postgresql.org/docs/current/)

| Feature Type | Count |
| ------------ | ----: |
| Function | 651 |
| Operator | 77 |
| Data Type | 39 |
| **Total** | **767** |

#### SQLite (current)

🔗 [Official Documentation](https://www.sqlite.org/)

| Feature Type | Count |
| ------------ | ----: |
| Function | 80 |
| Operator | 41 |
| Data Type | 6 |
| **Total** | **127** |

#### DuckDB (stable)

🔗 [Official Documentation](https://duckdb.org/docs/)

| Feature Type | Count |
| ------------ | ----: |
| Function | 553 |
| Operator | 39 |
| Data Type | 32 |
| **Total** | **624** |

#### CockroachDB (stable)

🔗 [Official Documentation](https://www.cockroachlabs.com/docs/stable/)

| Feature Type | Count |
| ------------ | ----: |
| Function | 700 |
| Operator | 58 |
| Data Type | 20 |
| **Total** | **778** |

#### MariaDB (current)

🔗 [Official Documentation](https://mariadb.com/docs/)

| Feature Type | Count |
| ------------ | ----: |
| Function | 338 |
| Operator | 43 |
| Data Type | 35 |
| **Total** | **416** |

#### ClickHouse (current)

🔗 [Official Documentation](https://clickhouse.com/docs/)

| Feature Type | Count |
| ------------ | ----: |
| Function | 1,275 |
| Operator | 53 |
| Data Type | 41 |
| **Total** | **1,369** |

#### Oracle Database (v26)

🔗 [Official Documentation](https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/)

| Feature Type | Count |
| ------------ | ----: |
| Function | 316 |
| Operator | 21 |
| Data Type | 30 |
| **Total** | **367** |

#### SQL Server (current)

🔗 [Official Documentation](https://learn.microsoft.com/en-us/sql/t-sql/functions/)

| Feature Type | Count |
| ------------ | ----: |
| Function | 156 |
| Operator | 19 |
| Data Type | 36 |
| **Total** | **211** |

#### Trino (current)

🔗 [Official Documentation](https://trino.io/docs/)

| Feature Type | Count |
| ------------ | ----: |
| Function | 464 |
| Operator | 23 |
| Data Type | 18 |
| **Total** | **505** |

### 📚 Supported Research

#### SmartFuzz: Leveraging Large Language Models and Feature Composition to Generate High-Quality Seeds for Database Fuzzing

**OOPSLA 2026**

[![Code](https://img.shields.io/badge/Code-GitHub-181717?logo=github)](https://github.com/SmartFuzz/SmartFuzz)
[![Conference](https://img.shields.io/badge/Conference-OOPSLA%202026-2563EB)](https://2026.splashcon.org/track/oopsla-2026)

SmartFuzz leverages documentation-derived DBMS features from the Feature Knowledge Base to guide LLM-based synthesis of diverse, executable, and feature-rich SQL seeds for database fuzzing.

### 🔧 Extraction Pipeline

Feature Crawler follows a five-stage extraction pipeline:

| Step | Stage | Description |
| :--: | ----- | ----------- |
| 01 | **Documentation** | Fetch official DBMS reference documentation |
| 02 | **Discovery** | Crawl documentation from seed pages and classify URLs by feature type |
| 03 | **Extraction** | Detect function, operator, and datatype candidates from structured documentation |
| 04 | **Validation** | Validate evidence references, schemas, and extraction quality |
| 05 | **Knowledge Base** | Deduplicate, canonicalize, and export structured KB snapshots |

```text
Official Documentation
        │
        ▼
┌──────────────────┐
│  Documentation   │
└────────┬─────────┘
         ▼
┌──────────────────┐
│    Discovery     │
└────────┬─────────┘
         ▼
┌──────────────────┐
│    Extraction    │
└────────┬─────────┘
         ▼
┌──────────────────┐
│    Validation    │
└────────┬─────────┘
         ▼
┌──────────────────┐
│  Knowledge Base  │
└──────────────────┘
```

DBMS-specific behavior is configured through profile files rather than large numbers of hardcoded DBMS-specific branches.

The design goal is:

> Adapt to a new DBMS primarily through configuration and profile tuning rather than modifying the core extraction pipeline.

---

## 🐞 Bug Knowledge Base

![Status](https://img.shields.io/badge/Status-Under%20Development-orange)

The Bug Knowledge Base is the second knowledge component of DialectRanger and is currently under development.

It is intended to organize structured knowledge extracted from historical DBMS bug reports and crash-triggering inputs, complementing the documentation-derived Feature Knowledge Base with reusable bug-related behaviors, triggering conditions, evidence, and provenance.

Detailed development plans are tracked in [TODO.md](TODO.md).

---

## 📁 Project Structure

DialectRanger is organized around two complementary knowledge bases:

- **Feature Knowledge Base** — currently implemented
- **Bug Knowledge Base** — under development

The repository keeps the data and implementation of the two knowledge bases separated, while sharing a common web interface and project-level documentation.

```text
DialectRanger/
│
├── web/
│   ├── index.html
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── data.js
│       └── app.js
│
├── data/
│   ├── feature_knowledge_base/
│   │   ├── mysql/
│   │   ├── postgresql/
│   │   ├── sqlite/
│   │   ├── duckdb/
│   │   ├── cockroachdb/
│   │   ├── mariadb/
│   │   ├── clickhouse/
│   │   ├── oracle/
│   │   ├── sqlserver/
│   │   └── trino/
│   │
│   └── bug_knowledge_base/
│       └── ...
│
├── script/
│   ├── feature_knowledge_base/
│   │   └── ...
│   │
│   └── bug_knowledge_base/
│       └── ...
│
├── doc/
│   └── ...
│
├── .github/
│   └── workflows/
│       └── deploy-pages.yml
│
├── README.md
├── TODO.md
└── ...
```

### `web/`

Contains the static web interface for browsing DialectRanger knowledge bases.

The current website primarily exposes the Feature Knowledge Base, while the Bug Knowledge Base interface will be added as its implementation matures.

```text
web/
├── index.html
├── css/
│   └── style.css
└── js/
    ├── data.js
    └── app.js
```

Responsibilities:

- `index.html` — main web application structure
- `style.css` — page layout and visual design
- `data.js` — frontend-ready knowledge base data
- `app.js` — navigation, search, filtering, feature cards, and interaction logic

The web application is dependency-free and requires no runtime backend.

### `data/`

Stores curated knowledge base snapshots and intermediate data used by DialectRanger.

The data is separated by knowledge base:

```text
data/
├── feature_knowledge_base/
└── bug_knowledge_base/
```

#### `data/feature_knowledge_base/`

Stores structured DBMS feature knowledge extracted from official documentation.

Data is organized by DBMS so that each database can be independently crawled, validated, updated, and published.

```text
data/feature_knowledge_base/
├── mysql/
├── postgresql/
├── sqlite/
├── duckdb/
├── cockroachdb/
├── mariadb/
├── clickhouse/
├── oracle/
├── sqlserver/
└── trino/
```

#### `data/bug_knowledge_base/`

Reserved for structured knowledge extracted from historical DBMS bugs, bug reports, crash-triggering inputs, and related evidence.

This component is currently under development.

### `script/`

Contains the implementation and supporting scripts for constructing the two knowledge bases.

```text
script/
├── feature_knowledge_base/
└── bug_knowledge_base/
```

#### `script/feature_knowledge_base/`

Contains the current Feature Crawler implementation, including components for:

- documentation discovery
- document parsing and structuring
- feature extraction
- evidence validation
- knowledge base curation
- data normalization
- frontend data generation

#### `script/bug_knowledge_base/`

Reserved for the Bug Knowledge Base pipeline.

Planned responsibilities include:

- historical bug collection
- crash-triggering input processing
- bug feature extraction
- evidence and provenance preservation
- bug knowledge validation
- Bug Knowledge Base construction

### `doc/`

Contains project documentation for DialectRanger and its knowledge base components.

This directory can include:

- system architecture
- knowledge base schemas
- extraction design
- development notes
- usage documentation
- experimental records

### `.github/workflows/`

Contains repository automation.

`deploy-pages.yml` publishes the contents of:

```text
web/
```

to GitHub Pages so that DialectRanger can be browsed directly online.


---


## ⚠️ Limitations

Current limitations of the Feature Knowledge Base include:

- JavaScript-rendered documentation may require additional browser-based fetching support
- only `function`, `operator`, and `datatype` are currently modeled as Feature Types
- new DBMS documentation structures may still require profile-level selector or classifier tuning
- semantic descriptions are evidence-bound but are not equivalent to formal semantic specifications
- documentation structure can limit extraction completeness for some DBMSs
- complex or irregular documentation pages may require additional normalization before reliable extraction
- feature equivalence across different DBMSs is outside the responsibility of the crawler itself and should be handled by downstream mapping components
