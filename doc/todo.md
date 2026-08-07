# DialectRanger TODO

This document tracks the major development directions of DialectRanger.

The project currently focuses on two complementary knowledge bases:

- **Feature Knowledge Base** — structured DBMS features extracted from official documentation.
- **Bug Knowledge Base** — structured bug-related knowledge extracted from historical DBMS bugs and crash-triggering inputs.

---

## Feature Knowledge Base

### P1 — Generalize Feature Extraction

- [ ] Reduce DBMS-specific profile dependencies in feature detection.
- [ ] Improve automatic discovery of operators and data types from heterogeneous documentation structures.
- [ ] Extend token mining beyond table-based documentation to headings, code blocks, lists, and paragraphs.
- [ ] Add support for additional feature types such as `system_variable`, `syntax`, and `statement`.

**Goal:** make new DBMSs usable with minimal manual profile configuration.

---

### P2 — Automate Knowledge Base Updates

- [ ] Add scheduled Feature Crawler runs with GitHub Actions.
- [ ] Automatically rebuild frontend data after KB updates.
- [ ] Automatically deploy the latest Knowledge Base to GitHub Pages.
- [ ] Support incremental crawling so unchanged documentation does not need to be processed again.

**Goal:** keep the Feature Knowledge Base continuously synchronized with evolving DBMS documentation.

---

### P3 — Improve Knowledge Quality

- [ ] Improve semantic descriptions extracted from documentation evidence.
- [ ] Introduce stronger evidence validation for generated semantic claims.
- [ ] Normalize feature names, signatures, and syntax representations.
- [ ] Reduce noisy or incorrectly extracted feature candidates.
- [ ] Improve automated quality checks for curated KB snapshots.

**Goal:** improve the accuracy, consistency, and usability of structured feature records.

---

### P4 — Expand Coverage

- [ ] Improve extraction from complex or JavaScript-rendered documentation.
- [ ] Extend support to additional DBMSs.
- [ ] Improve coverage of symbolic and irregular operators.
- [ ] Refine function classification, including aggregate and window functions.
- [ ] Expand beyond the current function/operator/datatype feature space.

Potential DBMS targets include:

- TiDB
- MonetdB
- BigQuery
- Redshift
- additional actively maintained DBMSs

**Goal:** build a broader and more representative DBMS feature knowledge base.

---

### P5 — Improve Knowledge Base Presentation

- [ ] Introduce a more consistent feature category system.
- [ ] Add explicit extraction provenance to KB records.
- [ ] Improve evidence and source-section visualization in the web interface.
- [ ] Improve browsing and inspection of incomplete or review-required features.
- [ ] Continue refining the GitHub Pages Knowledge Base browser.

**Goal:** make the extracted knowledge easier to inspect, understand, and reuse.

---

## Bug Knowledge Base

> Status: **Planned / Under Development**

The Bug Knowledge Base will complement the existing Feature Knowledge Base by capturing historical DBMS bug knowledge.

### Initial Development

- [ ] Design the Bug Knowledge Base schema.
- [ ] Collect historical DBMS bug reports and crash-triggering SQL inputs.
- [ ] Normalize bug metadata across different DBMS issue trackers.
- [ ] Reduce complex crash-triggering inputs into smaller reproducible cases.
- [ ] Extract reusable syntactic and semantic bug features.
- [ ] Preserve links between bug features and their original evidence.
- [ ] Record DBMS version, affected feature, trigger conditions, and bug status where available.
- [ ] Build automated validation and curation for bug records.
- [ ] Develop a browser for inspecting Bug Knowledge Base entries.

### Feature–Bug Integration

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