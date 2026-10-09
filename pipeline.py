import os
import sys
import json
import datetime
import hashlib
import subprocess
from openai import OpenAI

def log_stage(stage_name):
    timestamp = datetime.datetime.now().isoformat()
    print(f"\n[{timestamp}] -> {stage_name}")

def main():
    log_stage("INIT")
    
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("WARNING: DEEPSEEK_API_KEY not set. Please set it via: $env:DEEPSEEK_API_KEY='your_key'")
        sys.exit(1)
        
    # Initialize the DeepSeek client (using the OpenAI SDK)
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    model_name = "deepseek-chat"
    
    # Load files
    with open("tickets.json", "r") as f:
        tickets = json.load(f)
    with open("triage_config.json", "r") as f:
        config = json.load(f)
        
    log_stage("INPUTS_LOADED")

    # 1. Deterministic Ticket Normalization
    normalized_tickets = []
    for t in tickets:
        text_for_model = f"Subject: {t['subject']}\nMessage: {t['message']}"
        normalized_tickets.append({
            "ticket_id": t["ticket_id"],
            "subject": t["subject"],
            "message": t["message"],
            "channel": t["channel"],
            "created_at": t["created_at"],
            "text_for_model": text_for_model,
            "char_count": len(text_for_model)
        })
        
    with open("normalized_tickets.json", "w") as f:
        json.dump(normalized_tickets, f, indent=2)
        
    log_stage("TICKETS_NORMALIZED")

    # 2. Ticket Triage Prediction
    prompt = (
        f"Allowed categories: {config['allowed_categories']}\n"
        f"Allowed priorities: {config['allowed_priorities']}\n"
        f"Routing rules: {json.dumps(config['routing_rules'])}\n"
        f"Reply Style: {config['reply_style']['tone']} (max {config['reply_style']['max_words']} words)\n\n"
        "Return a JSON object with a single key 'predictions' containing an array where each ticket object has:\n"
        "ticket_id, category, priority, reason, suggested_reply, route_to, confidence.\n"
        "For 'confidence', provide a float on a scale of 0.0 to 1.0, where 0.0 is completely uncertain and 1.0 is absolute certainty based on the routing rules.\n\n"
        f"Tickets:\n{json.dumps([{'id': t['ticket_id'], 'text': t['text_for_model']} for t in normalized_tickets])}"
    )

    # Call the DeepSeek model
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": "You are an expert customer support triage agent. You must output valid JSON."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"}
    )
    
    raw_text = response.choices[0].message.content

    # 8. LLM Call Logging
    log_entry = {
        "stage": "TRIAGE_PREDICTED",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "provider": "deepseek",
        "model": model_name,
        "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(),
        "input_artifacts": ["normalized_tickets.json", "triage_config.json"],
        "output_artifact": "triage_predictions.json"
    }
    with open("llm_calls.jsonl", "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    try:
        data = json.loads(raw_text)
        raw_predictions = data.get("predictions", [])
    except Exception as e:
        print(f"Error parsing LLM response: {e}")
        print(f"Raw response was: {raw_text}")
        raw_predictions = []

    # 7. Batch Resilience 
    predictions_map = {p.get("ticket_id"): p for p in raw_predictions if isinstance(p, dict)}
    resilient_predictions = []
    
    for nt in normalized_tickets:
        tid = nt["ticket_id"]
        if tid in predictions_map:
            pred = predictions_map[tid]
            if pred.get("category") not in config["allowed_categories"]: pred["category"] = "other"
            if pred.get("priority") not in config["allowed_priorities"]: pred["priority"] = "normal"
            pred["route_to"] = config["routing_rules"].get(pred["category"], "manual_review_queue")
            resilient_predictions.append(pred)
        else:
            resilient_predictions.append({
                "ticket_id": tid,
                "category": "other",
                "priority": "normal",
                "reason": "System fallback: LLM failed to process this ticket.",
                "suggested_reply": "We have received your message and an agent will assist you shortly.",
                "route_to": "manual_review_queue",
                "confidence": 0.0
            })

    with open("triage_predictions.json", "w") as f:
        json.dump(resilient_predictions, f, indent=2)

    log_stage("TRIAGE_PREDICTED")

    # 3. Human Review Checkpoint
    print("\n--- PENDING TICKETS FOR REVIEW ---")
    for p in resilient_predictions:
        print(f"{p['ticket_id']} | Cat: {p['category']:<15} | Pri: {p['priority']:<8} | Route: {p['route_to']}")
    
    print("\nEnter any overrides as: ticket_id,category,priority")
    print("Press Enter on an empty line when done.")
    
    overrides = []
    override_map = {}
    while True:
        try:
            choice = input("> ").strip()
        except EOFError:
            break
            
        if not choice:
            break
            
        parts = [p.strip() for p in choice.split(",")]
        if len(parts) == 3:
            tid, cat, prio = parts
            if cat not in config["allowed_categories"]:
                print(f"Invalid category. Must be one of: {config['allowed_categories']}")
                continue
            if prio not in config["allowed_priorities"]:
                print(f"Invalid priority. Must be one of: {config['allowed_priorities']}")
                continue
            
            orig = next((p for p in resilient_predictions if p["ticket_id"] == tid), None)
            if orig:
                overrides.append({
                    "ticket_id": tid,
                    "old_category": orig["category"],
                    "new_category": cat,
                    "old_priority": orig["priority"],
                    "new_priority": prio
                })
                override_map[tid] = {"category": cat, "priority": prio}
            else:
                print(f"Ticket {tid} not found.")
        else:
            print("Invalid format. Use: ticket_id,category,priority")

    with open("review_overrides.json", "w") as f:
        json.dump(overrides, f, indent=2)
        
    log_stage("HUMAN_REVIEW_COMPLETE")

    # 4. Final Queue Output & 5. Escalations
    final_queue = []
    escalations = []
    
    counts_cat = {c: 0 for c in config["allowed_categories"]}
    counts_pri = {p: 0 for p in config["allowed_priorities"]}
    counts_route = {}

    for p in resilient_predictions:
        tid = p["ticket_id"]
        final_cat = override_map.get(tid, {}).get("category", p["category"])
        final_pri = override_map.get(tid, {}).get("priority", p["priority"])
        final_route = config["routing_rules"].get(final_cat, "manual_review_queue")
        
        conf = float(p.get("confidence", 1.0))
        if final_cat == "other" or conf < 0.60:
            escalations.append({
                "ticket_id": tid,
                "confidence": conf,
                "category": final_cat,
                "reason": "category == other" if final_cat == "other" else "confidence < 0.60"
            })

        final_queue.append({
            "ticket_id": tid,
            "final_category": final_cat,
            "final_priority": final_pri,
            "final_route_to": final_route,
            "suggested_reply": p["suggested_reply"],
            "was_overridden": tid in override_map
        })
        
        counts_cat[final_cat] += 1
        counts_pri[final_pri] += 1
        counts_route[final_route] = counts_route.get(final_route, 0) + 1

    with open("final_queue.json", "w") as f:
        json.dump(final_queue, f, indent=2)
        
    with open("escalations.json", "w") as f:
        json.dump(escalations, f, indent=2)

    with open("queue_summary.md", "w") as f:
        f.write("# Support Queue Summary\n\n")
        f.write(f"**Total Tickets:** {len(final_queue)}\n\n")
        
        f.write("## By Category\n")
        for k, v in counts_cat.items():
            f.write(f"- {k}: {v}\n")
            
        f.write("\n## By Priority\n")
        for k, v in counts_pri.items():
            f.write(f"- {k}: {v}\n")
            
        f.write("\n## By Destination\n")
        for k, v in counts_route.items():
            f.write(f"- {k}: {v}\n")
            
        f.write("\n## Overridden Tickets\n")
        if not overrides:
            f.write("None.\n")
        for o in overrides:
            f.write(f"- **{o['ticket_id']}**: {o['old_category']} -> {o['new_category']} | {o['old_priority']} -> {o['new_priority']}\n")

    log_stage("FINAL_QUEUE_GENERATED")

    log_stage("VALIDATION_COMPLETE")
    subprocess.run([sys.executable, "validate.py"], check=False)
    
    log_stage("RESULTS_FINALISED")

if __name__ == "__main__":
    main()