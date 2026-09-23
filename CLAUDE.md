# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

Pratique: a CAPTCHA replacement that spins up a sandboxed AI gatekeeper on DigitalOcean Managed
Agents (Harness Runtime) for every login attempt and interviews the visitor. **Pure Python 3.10+
standard library, zero dependencies** — a change that adds a dependency needs a very good reason.
This repository is public: no credentials, no internal hostnames, configuration by environment
variable only (see the README table).

## Where things live

- `gatekeeper/SKILL.md` — the gatekeeper's personality and reply contract (it *recommends*);
  `gatekeeper/HARBORMASTER.md` — the harbormaster's (it *rules*, and may overrule). Most behavior
  changes belong in these two files, not in code.
- `pratique/gatekeeper.py` — builds both environment specs (skills inlined), the prompts and the
  case file, and parses replies (`parse_reply` tolerates prose and fences, accepts
  `recommendation` or `verdict`). The harbormaster runs on the **OpenCode** adapter because the
  Claude Code adapter's bundled client refuses the newest Anthropic models.
- `pratique/store.py` — attempts persist to an S3-compatible bucket (SigV4, stdlib) or to disk.
- `pratique/challenger.py` + `scripts/challenge.py` — the attacker: API channel or a real browser
  (Playwright + Chromium installed through `doctl harness-runtime exec` first).
- `pratique/harness.py` — the sessions API client. `run_turn` opens the event stream *before*
  posting input so nothing is missed; events for other runs are passed to `on_event` but not
  collected.
- `gatekeeper/agents.yaml` is generated: edit the source and run `python3 scripts/render_spec.py`.

## Commands

```bash
python3 scripts/probe.py            # live smoke test: one session, three turns, teardown (needs tokens)
python3 scripts/render_spec.py      # print the spec YAML
python3 server.py                   # the app on :8080
```

## Platform facts worth remembering

- Session create returns READY in about 1.5 s; turns take 5–10 s on DigitalOcean inference; the
  first run of a session costs ~$0.29 (it reads the playbook), later runs ~$0.025.
- A fresh `/events` stream replays recent history before going live: dedupe by `event_id`, or
  keep one stream open per session and dispatch from it (what the server does).
- `doctl harness-runtime validate` warns that `ANTHROPIC_MODEL` is missing for `claude-code`
  with `HARNESS_INFERENCE_*`; the platform accepts the spec as-is and the warning is spurious.
- The events endpoint is `/v2/agents/sessions/{id}/events` (SSE). Keepalive comments arrive every
  15 s; a run ends with `run.completed` or `run.failed`.
- The model access key must be minted on the same team as the session; another team's key fails
  inside the sandbox with `401 invalid trusted identity`.
- Naming any egress host turns the allowlist on; the platform adds the inference endpoint itself.
- `permissions.default: deny` is a hard block on the `claude-code` adapter, which is what we want.
