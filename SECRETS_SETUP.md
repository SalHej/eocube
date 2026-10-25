# Giving the platform an API key

## 1. Create a key

claude.ai → Settings → **Platform → API keys** → Create key. Copy it; it is
shown once.

**This is separate billing from your Pro subscription.** Add a payment method
under Platform → Billing. A working session typically costs $0.50–$3 on
Sonnet; Opus is roughly five times that.

## 2. Give it to the platform

**Option A — environment variable (preferred).** Nothing on disk:

```powershell
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY","sk-ant-...","User")
```

Restart PowerShell afterwards.

**Option B — secrets file.** In the project root:

```json
{ "anthropic_api_key": "sk-ant-..." }
```

Save as `.labdash_secrets.json`. It is already in `.gitignore`. Verify before
your next commit:

```powershell
git check-ignore -v .labdash_secrets.json
```

If that prints nothing, the file is **not** ignored — stop and fix it.

## 3. Rules the platform enforces

The key is read at request time and never written to `dashboard.json`, never
logged, never sent to the browser. A command containing `ANTHROPIC_API_KEY` is
blocked, so the assistant cannot echo it back to itself.

## 4. If a key leaks

Revoke it in the console immediately and create a new one. A leaked key is
billable by whoever holds it.
