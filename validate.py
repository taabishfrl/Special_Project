import os
import json
import sys

def run_validation():
    print("\n--- RUNNING VALIDATION SUITE ---")
    errors = []

    # 1. Check required files exist
    required_files = [
        "normalized_tickets.json",
        "triage_predictions.json",
        "review_overrides.json",
        "final_queue.json",
        "queue_summary.md"
    ]
    for f in required_files:
        if not os.path.exists(f):
            errors.append(f"Missing required artifact: {f}")

    if errors:
        for e in errors: print(f"❌ {e}")
        sys.exit(1)

    # 2. Load configurations and outputs
    with open("triage_config.json", "r") as f:
        config = json.load(f)
    with open("normalized_tickets.json", "r") as f:
        norm_tickets = json.load(f)
    with open("final_queue.json", "r") as f:
        final_queue = json.load(f)
    with open("review_overrides.json", "r") as f:
        overrides = json.load(f)
        
    max_words = config["reply_style"]["max_words"]
    allowed_categories = config["allowed_categories"]
    allowed_priorities = config["allowed_priorities"]
    routing_rules = config["routing_rules"]

    # 3. Check exact 1:1 prediction mapping and constraints
    if len(norm_tickets) != len(final_queue):
        errors.append(f"Mismatch: {len(norm_tickets)} input tickets vs {len(final_queue)} final outputs.")

    overridden_ids = {o["ticket_id"]: o for o in overrides}

    for fq in final_queue:
        tid = fq["ticket_id"]
        
        # Check constraints
        if fq["final_category"] not in allowed_categories:
            errors.append(f"[{tid}] Invalid category: {fq['final_category']}")
            
        if fq["final_priority"] not in allowed_priorities:
            errors.append(f"[{tid}] Invalid priority: {fq['final_priority']}")
            
        # Check routing
        expected_route = routing_rules.get(fq["final_category"], "manual_review_queue")
        if fq["final_route_to"] != expected_route:
            errors.append(f"[{tid}] Invalid route: {fq['final_route_to']} (Expected {expected_route})")
            
        # Check reply length
        word_count = len(fq["suggested_reply"].split())
        if word_count > max_words:
            errors.append(f"[{tid}] Reply exceeds {max_words} words ({word_count} words).")

        # Verify override was applied properly
        if tid in overridden_ids:
            if not fq["was_overridden"]:
                errors.append(f"[{tid}] Override exists but was_overridden is false.")
            if fq["final_category"] != overridden_ids[tid]["new_category"]:
                errors.append(f"[{tid}] Category override not applied.")

    # 4. Check normalization was deterministic before LLM
    # Verify llm_calls.jsonl if exists to ensure inputs include normalized_tickets
    if os.path.exists("llm_calls.jsonl"):
        with open("llm_calls.jsonl", "r") as f:
            calls = [json.loads(line) for line in f.readlines()]
            if calls:
                if "normalized_tickets.json" not in calls[-1]["input_artifacts"]:
                    errors.append("Normalization was not confirmed prior to LLM call.")

    if not errors:
        print("✅ ALL VALIDATIONS PASSED.")
    else:
        for e in errors: print(f"❌ {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_validation()