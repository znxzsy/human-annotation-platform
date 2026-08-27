# Recheck Correction and Auto-Advance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a reviewer to replace an inaccurate first-pass label with a final reviewed label, keep unresolved inaccuracies out of training exports, and automatically advance after each completed review.

**Architecture:** Extend `slot_rechecks` with optional final-label fields while preserving the immutable first-pass `slot_reviews` row. Treat an inaccurate recheck without a final label as quarantined; exporters resolve each SLOT through the recheck first and only emit corrected or accepted data. The browser saves one-click accurate decisions immediately, reveals a correction editor for inaccurate decisions, and advances only after a decision is complete.

**Tech Stack:** Python standard library, SQLite, vanilla JavaScript, `unittest`.

---

### Task 1: Persist final recheck labels

**Files:**
- Modify: `annotation_platform/schema.sql`
- Modify: `annotation_platform/store.py`
- Test: `tests/test_recheck_corrections.py`

- [ ] **Step 1: Write failing store tests**

Create a test database with one submitted group, save an inaccurate recheck with `final_verdict`, `final_r`, `final_h`, and `final_reason_code`, then assert the returned `rechecks` row contains the correction. Add validation cases for wrong-without-result and unsure-without-reason.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python -m unittest tests.test_recheck_corrections -v`

Expected: failure because `save_recheck` does not accept final-label fields.

- [ ] **Step 3: Add schema migration and validation**

Add nullable `final_verdict`, `final_r`, `final_h`, and `final_reason_code` columns. Update `save_recheck` so accurate decisions clear final fields, inaccurate decisions may remain unresolved, wrong corrections require a transcription, and unsure corrections require one of the three Unknown reason codes.

- [ ] **Step 4: Run focused tests**

Run: `python -m unittest tests.test_recheck_corrections -v`

Expected: all store tests pass.

### Task 2: Make training exports recheck-aware

**Files:**
- Modify: `annotation_platform/exporter.py`
- Test: `tests/test_recheck_corrections.py`

- [ ] **Step 1: Write failing export tests**

Cover three paths: accepted first-pass label is exported, unresolved inaccurate label is quarantined, and corrected label replaces the first-pass label in KTO/DPO/SFT outputs without modifying the audit source row.

- [ ] **Step 2: Run the export tests and verify failure**

Run: `python -m unittest tests.test_recheck_corrections -v`

Expected: current exporter incorrectly uses the first-pass label.

- [ ] **Step 3: Resolve an effective label per SLOT**

Build each output from the final correction when present. Skip candidate output and mark the group incomplete when an inaccurate recheck remains unresolved. Keep raw first-pass and recheck JSONL files unchanged for auditability.

- [ ] **Step 4: Run focused tests**

Run: `python -m unittest tests.test_recheck_corrections -v`

Expected: all export assertions pass.

### Task 3: Add correction controls and automatic advance

**Files:**
- Modify: `annotation_platform/server.py`
- Modify: `annotation_platform/static/app.js`
- Modify: `annotation_platform/static/styles.css`
- Test: `tests/test_smoke.py`

- [ ] **Step 1: Extend the POST contract**

Forward final-label fields from `/api/items/{event_id}/recheck` to the store and return the refreshed item.

- [ ] **Step 2: Render the correction editor**

When “发现人工标注不准” is selected, show final buttons for correct, wrong, and Unknown. Show a corrected-result input for wrong, and Unknown reason buttons for unsure. Keep the original annotation read-only.

- [ ] **Step 3: Implement completion-aware auto-advance**

After an accurate click or a successfully saved final correction, focus and scroll to the next unresolved eligible SLOT in the group. If none remains, load the next eligible group within the selected batch. Do not advance after merely marking a row inaccurate without resolving it.

- [ ] **Step 4: Add static-contract smoke assertions**

Assert the served JavaScript contains the correction labels and auto-advance status text, then run the complete test suite.

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass.

### Task 4: Document, verify, and publish

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the final-label precedence**

Explain that first-pass rows are preserved, unresolved inaccuracies are quarantined, and final reviewed labels take precedence in training exports.

- [ ] **Step 2: Run repository checks**

Run: `python -m unittest discover -s tests -v`

Run: `git diff --check`

Expected: tests pass and no whitespace errors are reported.

- [ ] **Step 3: Commit and push**

Commit the correction workflow and push it to the existing `main` branch after verifying only intended files changed.
