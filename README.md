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
  - [🔧 Extraction Pipeline](#-extraction-pipeline)
- [🐞 Bug Knowledge Base](#-bug-knowledge-base)
- [📚 Supported Research](#-supported-research)

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

## 📚 Supported Research

```bibtex
@article{lin2026smartfuzz,
  title   = {SmartFuzz: Leveraging Large Language Models and Feature Composition to Generate High-Quality Seeds for Database Fuzzing},
  author  = {Li Lin and Jintai Hong and Yanlin Zhuang and Rongxin Wu},
  journal = {Proceedings of the ACM on Programming Languages},
  note    = {OOPSLA 2026},
  year    = {2026},
  url     = {https://github.com/SmartFuzz/SmartFuzz}
}

---

