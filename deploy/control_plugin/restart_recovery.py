"""Eligibility only; native authority, freshness and restart-loop guards remain authoritative."""
REASONS={'restart_timeout','shutdown_timeout','restart_interrupted'}


def safe_candidate(entry,admissions,goal,fresh):
    if not fresh or not getattr(entry,'resume_pending',False) or getattr(entry,'suspended',False): return False
    if getattr(entry,'resume_reason','') not in REASONS: return False
    if not goal or goal.status!='active' or not admissions: return False
    if any(row.get('status')!='terminal' for row in admissions): return False
    latest=max(enumerate(admissions),key=lambda item:(item[1].get('seq',item[1].get('sequence',0)),item[0]))[1]
    return latest.get('outcome')=='interrupted'
