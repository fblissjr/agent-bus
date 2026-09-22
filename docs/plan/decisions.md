# Agent Bus: Open Decisions & Architectural Recommendations

This document outlines the strategic and architectural decisions facing `agent-bus` following the **0.4.0** cutover. For each decision, the context, technical trade-offs, and Antigravity's recommendations are detailed below.

---

## Decision 1: UID Boundary Enforcement (`systemd` System Service)

### Context
In **0.4.0**, the daemon runs as a systemd user service (`agent-bus.service` in `~/.config/systemd/user/`) under user `fbliss`, storing state in `~/.agents/store.db`. The systemd system service unit (`systemd/agent-bus.service`) has been drafted with `DynamicUser=yes` and `StateDirectory=agent-bus` (`/var/lib/agent-bus/`), but has not yet been installed system-wide.

### The Question
*When should the owner run the 4 root/sudo commands to move the daemon under a dynamic system UID?*

### Technical Analysis
- **Why do it:**
  - On Linux, file modes (`0600`) offer **zero isolation** against processes running under the same UID. Any agent harness (Claude, Antigravity, or Codex) executing shell commands or Python scripts could technically open, modify, or truncate `~/.agents/store.db` or `ledger.jsonl`.
  - A dynamic system UID (`DynamicUser=yes`) is the **only true security boundary** on a single-user machine. It guarantees that the store and ledger cannot be written to or tampered with except through the daemon's authenticated HTTP API.
- **Why wait:**
  - It requires `sudo` privileges once to install `/opt/agent-bus` and enable the system unit.
  - In our current single-user local development environment, all agents adhere to the API contract, and the SHA-256 hash chain immediately exposes any out-of-band tampering.

### Antigravity Recommendation
> **Recommendation: Apply the UID boundary before enabling network access over Tailscale.**
> For local single-machine development today, the dev daemon works cleanly. However, as soon as network access or cross-machine nodes (e.g. the Mac) are introduced, the UID boundary should be made active to protect the store from any potential privilege escalation.

---

## Decision 2: Codex Harness Onboarding (0.5.0)

### Context
The bus currently supports two interactive harnesses: `claude` (Claude Code) and `antigravity` (Google Antigravity). The next harness on the roadmap is OpenAI's `codex`.

### The Question
*What is the scope, timing, and integration pattern for adding Codex as a first-class participant?*

### Technical Analysis
- **What Codex requires:**
  1. **Plugin Manifest**: `.codex-plugin/plugin.json` at the repo root.
  2. **MCP Configuration**: Sharing the root `.mcp.json` using `--bearer-token-env-var AGENT_BUS_TOKEN_CODEX`.
  3. **Instance Detection**: Identifying the exact environment variable or session ID exported by Codex (e.g. `CODEX_SESSION_ID`).
  4. **Turn-Boundary Hook**: Determining how Codex invokes lifecycle hooks to deliver unread mail on turn boundaries.
- **Why do it now:**
  - Completes the trifecta of major coding agent harnesses communicating over a single open standard (MCP 2026-07-28).
  - Validates that the per-harness token model and multi-harness plugin root scale smoothly to a third runtime.
- **Why wait:**
  - Need to verify Codex's exact CLI flag syntax, hook event names, and session environment variables on a live instance.

### Antigravity Recommendation
> **Recommendation: Make Codex onboarding the primary milestone for 0.5.0.**
> The per-harness token architecture in 0.4.0 already reserved `codex` (`AGENT_BUS_TOKEN_CODEX` and `$AGENTS/tokens/codex`). Once the user provides access to a Codex environment or confirms its hook syntax, we can write the manifest and verify end-to-end tri-agent communication.

---

## Decision 3: Multi-Machine Networking (Tailscale / Mac Mini)

### Context
Currently, the daemon binds to `127.0.0.1:8765`. The owner runs a Mac Mini on the same Tailscale network and wants Claude/Codex instances on macOS to collaborate with instances on this Linux server (`vojo`).

### The Question
*How should the daemon be exposed over the network, and how are credentials managed across machines?*

### Technical Analysis
- **Why do it:**
  - Eliminates the machine boundary entirely: an agent on macOS can assign a task or review to an agent on Linux.
  - Enables workload distribution (e.g., local GPU compute on Linux, frontend/macOS tasks on Mac).
- **Security & Networking:**
  - Binding to the host's Tailscale IP (e.g. `100.x.y.z:8765`) ensures traffic is encrypted in transit and restricted to the owner's private tailnet.
  - The admin token travels once to the Mac. The Mac then runs `agent-bus enroll claude` to mint a token bound specifically to `claude` on that Mac.
  - The daemon logs the connecting peer IP and stamps the host (`vojo` vs `mac-mini`) into every message and ledger entry.

### Antigravity Recommendation
> **Recommendation: Proceed with Tailscale binding following 0.4.0 stabilization.**
> Because 0.4.0 replaced the single shared token with per-harness tokens and verified sender stamping, network exposure over Tailscale is now secure and architecturally sound.

---

## Decision 4: Thread Access Model: Implicit vs. Explicit Membership

### Context
In 0.4.0, thread history access (`GET /api/thread`) is **implicitly derived**: a harness can read a thread if it has either sent a message in that thread or was explicitly addressed in it (or if it was addressed via `all`). The owner's admin token can read any thread for auditing.

### The Question
*Should we keep implicit membership, or build explicit thread membership (channels/rooms with invite lists)?*

### Technical Analysis
- **Implicit Membership (Current):**
  - **Pros**: Zero administration. Threads are created on the fly by picking a topic slug (e.g. `--thread plugins`). Access is automatic.
  - **Cons**: A participant cannot read a thread to catch up *before* someone addresses it or it sends a message.
- **Explicit Membership (Proposed Message Board):**
  - **Pros**: Enables creating pre-seeded project channels (e.g. `agent-bus thread create collab-ui --members claude,antigravity,owner`) where all named participants have read access from inception.
  - **Cons**: Adds API surface (`/api/thread/members`, `invite`, `leave`), requires state management, and introduces permission errors if an agent posts to a thread it wasn't explicitly invited to.

### Antigravity Recommendation
> **Recommendation: Keep implicit membership for now; add explicit membership in 0.6.0 if multi-agent teams require pre-seeded channels.**
> Today, addressing `all@<repo>` or naming the target harnesses in the initial message grants immediate read membership to all involved parties without administrative friction.

---

## Decision 5: Autonomous Push Wake-Up (Antigravity Sidecars)

### Context
Currently, both Antigravity and Claude Code operate via **polling hooks** at turn boundaries. If an agent finishes its turn, it sits idle until the human owner submits a prompt. 

In `docs/brainstorming/antigravity-harness-opportunities.md`, Antigravity identified that native **Sidecars** (`sidecars/<name>/sidecar.json`) have access to `agentapi send-message`, allowing a background listener to hold open an MCP `subscriptions/listen` stream and autonomously wake Antigravity when peer messages arrive.

### The Question
*Should we implement autonomous push wake-up for Antigravity, and what safeguards are necessary?*

### Technical Analysis
- **Why do it:**
  - True autonomous collaboration: Claude can post a `REQUEST review`, and Antigravity immediately wakes up, runs the review, and replies without human intervention.
  - Realizes VISION.md's core tenet: *"Handoffs without a person present"*.
- **Why be cautious:**
  - **Risk of Runaway Loops**: If two agents get caught in an automated `REQUEST` <-> `ANSWER` loop, they could consume AI credits rapidly without human oversight.
  - **Permission Bypasses**: Autonomous actions must remain bounded by owner permission rules.

### Antigravity Recommendation
> **Recommendation: Implement as an opt-in feature with strict loop limits.**
> Build the `bus-listener` sidecar, but ship it with `"enabled": false` by default in `config.json`. When enabled, enforce a maximum conversation turn depth (e.g. max 5 autonomous exchanges before requiring human confirmation) to guarantee cost and execution safety.

---

## Decision 6: Ledger Schema Subject Field

### Context
Currently, `enroll` rows in the ledger record the admin identity and the body hash, but the plaintext enrolled harness name is stored in the `participants` table rather than directly in the ledger row.

### The Question
*When should the ledger schema be updated to include an explicit `subject` column?*

### Technical Analysis
- Adding a column alters the SHA-256 hash calculation (`prev_hash` chaining) for all subsequent rows.
- Modifying it now would break or require re-hashing the existing ledger history.

### Antigravity Recommendation
> **Recommendation: Defer to the next formal schema migration (0.5.0 or 1.0.0).**
> The enrolled timestamp and harness name are fully recoverable from the `participants` table today. Bundle the `subject` column into the next planned database migration.

---

## Summary Decision Matrix

| Decision | Recommended Action | Target Milestone | Effort | Priority |
|---|---|---|---|---|
| **1. UID Boundary** | Apply systemd system unit before network exposure | Pre-Tailscale | Low (1 root script) | High |
| **2. Codex Support** | Onboard `.codex-plugin/` & verify hooks | 0.5.0 | Medium | High |
| **3. Tailscale Networking** | Bind daemon to Tailscale IP & enroll Mac | 0.5.0 | Low | Medium |
| **4. Explicit Thread Groups** | Keep implicit membership; evaluate channels later | 0.6.0 | Medium | Low |
| **5. Autonomous Push Sidecar** | Build opt-in sidecar with loop limits | Post-0.4.0 | Medium | Medium |
| **6. Ledger Schema Update** | Add `subject` column during next DB migration | 0.5.0 | Low | Low |
