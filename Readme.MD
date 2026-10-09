# Support Triage Pipeline

A resilient, replayable LLM pipeline for categorizing, prioritizing, and responding to customer support tickets.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Design Specs & Architecture Notes

This pipeline is designed around a core AI engineering principle: **separate non-deterministic LLM generations from deterministic business logic.**

To ensure safety, reliability, and strict adherence to routing rules, the LLM is treated as an untrusted reasoning engine, while Python enforces all system boundaries.

### 1. Deterministic Normalization

Data normalization happens entirely in Python prior to the LLM call. The system deterministically computes `text_for_model` and `char_count`. This prevents the LLM from hallucinating data transformations, saves token costs, and ensures the schema is rigid.

### 2. Strict Boundary Enforcement

The LLM is prompted with dynamic `allowed_categories` and `allowed_priorities` injected from `triage_config.json`. However, the pipeline does not trust the output blindly:

- **Constrained Values:** If the LLM hallucinates an invalid category or priority, deterministic fallback logic immediately catches it and assigns the ticket to `other` / `normal`.
- **Config-Derived Routing:** The LLM's suggested route is ignored during final queue generation. `final_route_to` is strictly derived from the config map based on the finalized category.

### 3. Resilience and Graceful Degradation

To handle the unpredictability of batch API calls, the pipeline implements a **Batch Resilience** pattern. If the LLM returns malformed JSON for a specific ticket, or omits a ticket entirely, the system does not crash. Instead, it generates a fallback entry for the missing ticket, routing it safely to the `manual_review_queue` while allowing the rest of the batch to succeed.

### 4. Human-in-the-Loop (HITL) Checkpoint

The pipeline implements an interactive blocking step for human review. Overrides are mapped in memory and deterministically applied to the final payload. When a human overrides a category, the system automatically recalculates the `route_to` destination based on the configuration rules rather than relying on stale data.

### 5. Escalation Rules & Auditability

- **Confidence Escalation:** The pipeline utilizes the LLM's self-evaluated confidence score to flag tickets where `confidence < 0.60` or `category == "other"` into an `escalations.json` file.
- **Audit Logging:** Every LLM invocation is logged to `llm_calls.jsonl` with timestamps, model used, prompt hashes, and artifact lineage, ensuring total reproducibility and observability for every run.
- **Automated Validation:** A standalone `validate.py` script verifies that all downstream constraints (word limits, route matching, artifact generation) are strictly met.
