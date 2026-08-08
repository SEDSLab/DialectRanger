# DialectRanger

**DialectRanger** is an intelligent testing platform for multi-dialect database systems. The project is built around four core components:

- **Feature Knowledge Base** — models DBMS dialect features and their syntax, semantics, usage, and provenance from official documentation.
- **Bug Knowledge Base** — accumulates historical DBMS bugs, triggering inputs, affected features, and reusable bug-related knowledge.
- **SmartFuzz** — synthesizes high-quality initial seeds for mutation-based DBMS fuzzing using feature and bug knowledge.
- **FMU** — adapts generation-based DBMS fuzzers to different SQL dialects through cross-dialect feature mapping.

> **Current status:** The Feature Knowledge Base crawler and web browser have been implemented. SmartFuzz is planned for open-source release, while the Bug Knowledge Base and FMU remain under development.

**Testing Capabilities**

Built on the four components above, DialectRanger is designed to support:

- **Regression Testing** — replay known failures across DBMS versions.
- **Differential Testing** — compare equivalent behavior across DBMSs.
- **Metamorphic Testing** — detect semantic inconsistencies through metamorphic relations.

These are testing capabilities provided by the platform rather than separate projects.

---

## Contents

- [🧩 Feature Knowledge Base](#-feature-knowledge-base)
  - [🌐 Website](#-website)
  - [🔧 Extraction Pipeline](#-extraction-pipeline)
- [🐞 Bug Knowledge Base](#-bug-knowledge-base)
- [📚 Supported Research](#-supported-research)
- [🗺️ TODO List](#️-todo-list)

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

🔗 **Online Knowledge Base Browser:** 🔗 **[Online Knowledge Base Browser](https://sedslab.github.io/DialectRanger/)**

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
| :--: | --- | --- |
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

> **Status: Planned / Under Development**

The Bug Knowledge Base is the second knowledge component of DialectRanger.

It is intended to organize structured knowledge extracted from historical DBMS bug reports and crash-triggering inputs, complementing the documentation-derived Feature Knowledge Base with reusable bug-related behaviors, triggering conditions, evidence, and provenance.

The Bug Knowledge Base will eventually be linked with the Feature Knowledge Base to model bug-prone feature combinations and provide structured knowledge for downstream database testing and fuzzing.

---

## 📚 Supported Research

```bibtex
@inproceedings{lin2026smartfuzz,
  title     = {SmartFuzz: Leveraging Large Language Models and Feature Composition to Generate High-Quality Seeds for Database Fuzzing},
  author    = {Li Lin, intai Hong, Yanlin Zhuang and Rongxin Wu},
  booktitle = {Proceedings of the 2026 International Conference on Object-Oriented Programming Systems, Languages, and Applications (OOPSLA 2026)},
  month     = {October},
  year      = {2026}
}

@inproceedings{lin2025dlbench,
  title     = {{DLBENCH}: A Comprehensive Benchmark for {SQL} Translation with Large Language Models},
  author    = {Li Lin, Hongqiao Chen, Qinglin Zhu, Liehang Chen, Linlong Tang and Rongxin Wu},
  booktitle = {Proceedings of the 40th IEEE/ACM International Conference on Automated Software Engineering (ASE 2025)},
  month     = {November},
  year      = {2025}
}

@inproceedings{lin2025qtran,
  title     = {{QTRAN}: Extending Metamorphic-Oracle based Logical Bug Detection Techniques for Multiple-{DBMS} Dialect Support},
  author    = {Li Lin, Qinglin Zhu, Hongqiao Chen, Zhuangda Wang, Rongxin Wu and Xiaoheng Xie},
  booktitle = {Proceedings of the 34th ACM SIGSOFT International Symposium on Software Testing and Analysis (ISSTA 2025)},
  month     = {June},
  year      = {2025}
}
```

---

## 🗺️ TODO List

- [x] **Feature Knowledge Base** — automate feature extraction from official DBMS documentation and provide a web-based browser.
- [ ] **Bug Knowledge Base** — build structured bug collection, normalization, validation, and browsing capabilities.
- [ ] **SmartFuzz** — integrate knowledge-guided seed synthesis for mutation-based fuzzing.
- [ ] **FMU** — implement and integrate cross-dialect adaptation for generation-based fuzzers.
