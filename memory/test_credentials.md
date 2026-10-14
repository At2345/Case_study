# Test credentials (seeded by backend/server.py from backend/.env, stored hashed in /app/var/users.json)

| Username | Password | Role | Can |
|---|---|---|---|
| danny | PlannerPass2026! | planner | review queue, record overrides, commit cycle |
| admin | AdminPass2026! | admin | everything + run monthly cycle / Amazon pipeline, reload, verify ledger |
| auditor | AuditorPass2026! | auditor | read-only, verify audit ledger hash chain |

Login: POST /api/v1/auth/login {"username","password"} -> access_token (Bearer). 5 failed attempts lock the user for 5 minutes.
