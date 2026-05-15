# CLAUDE.md

## Agent Startup Rule

Before scanning this repo, read `.agent/repo-digest.md`.

Use the digest as the primary orientation layer. Only open source files after the digest identifies the relevant paths.

Do not broad-scan the repo unless:

1. `.agent/repo-digest.md` is missing,
2. `.agent/repo-digest.md` is clearly stale,
3. the needed path is not listed in the digest, or
4. targeted reads fail.

## Token Discipline

- Prefer targeted reads on exact paths from `.agent/repo-digest.md`.
- Do not summarize or scan `node_modules`, build outputs, caches, logs, generated files, or dependency folders.
- Do not use broad search when a known path is available.
- Keep work tied to revenue, reliability, speed, conversion, retention, or operational leverage.

## Safety Gates

No customer-facing, payment, email, ad, deploy, production data, or destructive database action without explicit Zach approval.

## Repo-Specific Context

This repo is `zirowork-brain-agent`. Use `.agent/repo-digest.md` for the current generated map of commands, architecture, critical files, and danger zones.
