# DialectRanger

**DialectRanger** is an intelligent testing platform for multi-dialect database systems. It aims to unify dialect-specific knowledge modeling, test-case generation, and automated validation across heterogeneous DBMSs.

At its foundation, DialectRanger is organized around two complementary knowledge bases:

- **Feature Knowledge Base** — models what a DBMS supports and how its dialect features are used by extracting structured knowledge from official documentation, including functions, operators, data types, system_variable, syntax, and statement.
- **Bug Knowledge Base** — models historical DBMS failures and bug-triggering behaviors from bug reports, crash-triggering inputs, and related evidence. **Under Development.**

Based on dialect-specific knowledge, our current work has explored two complementary DBMS fuzzing directions:

- **SmartFuzz** — high-quality initial seed synthesis for **mutation-based DBMS fuzzing**, guided by the **Feature Knowledge Base** and **Bug Knowledge Base**.
- **FMU** — **Feature Knowledge Base-guided cross-dialect feature mapping** for **generation-based DBMS fuzzing**.

These two directions focus on how dialect knowledge can improve test-case generation and adapt existing fuzzing techniques to heterogeneous DBMSs.

Looking forward, DialectRanger will further extend this knowledge-driven testing framework to support multiple validation strategies, including:

- **Regression Testing** — replaying and validating known failures across DBMS versions.
- **Differential Testing** — comparing behaviors across DBMSs to identify inconsistencies.
- **Metamorphic Testing** — detecting semantic inconsistencies through metamorphic relations without requiring traditional test oracles.

The long-term goal is to build an integrated multi-dialect database testing platform covering the complete workflow of:

> **Knowledge Modeling → Test-Case Generation → Bug Discovery → Bug Knowledge Accumulation → Regression Validation**

Together, these components form a complete testing loop:

```text
                  DialectRanger
                       │
          ┌────────────┴────────────┐
          │                         │
          ▼                         ▼
 Feature Knowledge Base      Bug Knowledge Base
          │                  (Under Development)
          └────────────┬────────────┘
                       ▼
             Knowledge-Guided Testing
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
    SmartFuzz         FMU       Multi-Strategy
Mutation-based   Generation-based    Validation
   Fuzzing          Fuzzing       ┌────┼────┐
                                  ▼    ▼    ▼
                                RT    DT   MT
                                  \    │    /
                                   \   │   /
                                    ▼  ▼  ▼
                             Bug Discovery
                                    │
                                    ▼
                          Regression Validation
```

The long-term goal is to provide a reusable infrastructure covering the full workflow of:

> **Knowledge-driven analysis → Test-case generation → Bug discovery → Regression validation**

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
@article{lin2026smartfuzz,
  title   = {SmartFuzz: Leveraging Large Language Models and Feature Composition to Generate High-Quality Seeds for Database Fuzzing},
  author  = {Li Lin and Jintai Hong and Yanlin Zhuang and Rongxin Wu},
  journal = {Proceedings of the ACM on Programming Languages},
  note    = {OOPSLA 2026},
  year    = {2026},
  url     = {https://github.com/SmartFuzz/SmartFuzz}
}
```

---

## 🗺️ TODO List

### Feature Knowledge Base

#### P1 — Generalize Feature Extraction

- [ ] Reduce DBMS-specific profile dependencies in feature detection.
- [ ] Improve automatic discovery of operators and data types from heterogeneous documentation structures.
- [ ] Extend token mining beyond table-based documentation to headings, code blocks, lists, and paragraphs.
- [ ] Add support for additional feature types such as `system_variable`, `syntax`, and `statement`.

**Goal:** make new DBMSs usable with minimal manual profile configuration.

#### P2 — Automate Knowledge Base Updates

- [ ] Add scheduled Feature Crawler runs with GitHub Actions.
- [ ] Automatically rebuild frontend data after KB updates.
- [ ] Automatically deploy the latest Knowledge Base to GitHub Pages.
- [ ] Support incremental crawling so unchanged documentation does not need to be processed again.

**Goal:** keep the Feature Knowledge Base continuously synchronized with evolving DBMS documentation.

#### P3 — Improve Knowledge Quality

- [ ] Improve semantic descriptions extracted from documentation evidence.
- [ ] Introduce stronger evidence validation for generated semantic claims.
- [ ] Normalize feature names, signatures, and syntax representations.
- [ ] Reduce noisy or incorrectly extracted feature candidates.
- [ ] Improve automated quality checks for curated KB snapshots.

**Goal:** improve the accuracy, consistency, and usability of structured feature records.

#### P4 — Expand Coverage

- [ ] Improve extraction from complex or JavaScript-rendered documentation.
- [ ] Extend support to additional DBMSs.
- [ ] Improve coverage of symbolic and irregular operators.
- [ ] Refine function classification, including aggregate and window functions.
- [ ] Expand beyond the current function/operator/datatype feature space.

Potential DBMS targets include:

- TiDB
- MonetDB
- BigQuery
- Redshift
- additional actively maintained DBMSs

**Goal:** build a broader and more representative DBMS feature knowledge base.

#### P5 — Improve Knowledge Base Presentation

- [ ] Introduce a more consistent feature category system.
- [ ] Add explicit extraction provenance to KB records.
- [ ] Improve evidence and source-section visualization in the web interface.
- [ ] Improve browsing and inspection of incomplete or review-required features.
- [ ] Continue refining the GitHub Pages Knowledge Base browser.

**Goal:** make the extracted knowledge easier to inspect, understand, and reuse.

### Bug Knowledge Base

> **Status: Planned / Under Development**

#### Initial Development

- [ ] Design the Bug Knowledge Base schema.
- [ ] Collect historical DBMS bug reports and crash-triggering SQL inputs.
- [ ] Normalize bug metadata across different DBMS issue trackers.
- [ ] Reduce complex crash-triggering inputs into smaller reproducible cases.
- [ ] Extract reusable syntactic and semantic bug features.
- [ ] Preserve links between bug features and their original evidence.
- [ ] Record DBMS version, affected feature, trigger conditions, and bug status where available.
- [ ] Build automated validation and curation for bug records.
- [ ] Develop a browser for inspecting Bug Knowledge Base entries.

#### Feature–Bug Integration

- [ ] Link bug-related knowledge to corresponding entries in the Feature Knowledge Base.
- [ ] Identify DBMS features frequently involved in historical bugs.
- [ ] Represent bug-triggering feature combinations and interactions.
- [ ] Support downstream feature-aware and bug-aware SQL generation.
- [ ] Provide structured knowledge for database fuzzing and testing systems.

**Goal:** combine:

```text
Feature Knowledge
"What does this DBMS support and how is it used?"

                    +

Bug Knowledge
"What feature behaviors and combinations have historically exposed bugs?"

                    ↓

Feature-aware / Bug-aware DBMS Testing
```

### Testing Platform Integration

- [ ] Integrate **SmartFuzz** for mutation-based fuzzing seed synthesis.
- [ ] Integrate **FMU** for adapting feature-aware generation to generation-based fuzzers.
- [ ] Add regression testing workflows for replaying and validating known DBMS bugs.
- [ ] Add differential testing across heterogeneous DBMS dialects.
- [ ] Add metamorphic testing for oracle-free consistency validation.
- [ ] Build a unified execution and result-management layer across testing strategies.
- [ ] Connect newly discovered bugs back into the Bug Knowledge Base.
- [ ] Support regression validation of fixed bugs across DBMS versions.

**Goal:** establish an end-to-end closed loop:

```text
Knowledge Modeling
       ↓
Test-Case Generation
       ↓
Multi-Strategy Validation
       ↓
Bug Discovery
       ↓
Bug Knowledge Accumulation
       ↓
Regression Validation
```
