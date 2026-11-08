# Plan: `agy-acp` provider (Antigravity CLI over ACP)

Status: **planning** — nothing implemented yet. Branch: `claude/elegant-johnson-lwx4rh`.

> Working doc for picking this back up after a break. Drop it from the branch (or
> move the content into the upstream issue) before opening an upstream PR —
> per `AGENTS.md`, upstream work needs a **Ready** issue on the Goose Issues board.

## Goal

Let goose drive Google's Antigravity CLI (`agy`) as an agent provider by spawning
`agy acp` (native ACP v1 server over stdio). No adapter package is needed; this
copies the `copilot-acp` setup (vendor binary + subcommand/flag) instead of the
`*-acp` npm-adapter providers.

## What already exists (no new infrastructure needed)

Every ACP provider is a ~100-line `ProviderDef` that builds an `AcpProviderConfig`
and calls `AcpProvider::connect`. All protocol work (initialize, session/new,
modes, config options, permissions, MCP passthrough, auth-required detection)
lives in `crates/goose/src/acp/provider.rs`.

| Concern | Where | Reference impl |
|---|---|---|
| Provider def + metadata | `crates/goose/src/providers/<name>.rs` | `copilot_acp.rs` (closest: vendor binary + arg, model option) |
| Module export | `crates/goose/src/providers/mod.rs` | `pub mod copilot_acp;` |
| Registry | `crates/goose/src/providers/init.rs` | `register_with_inventory::<CopilotAcpProvider>(false, …)` |
| Inventory (installed? / model refresh) | `crates/goose/src/providers/inventory/registrations.rs` | `copilot_acp_inventory()` → `acp_inventory(name, binary, true)` |
| Binary lookup | `config::search_path::SearchPaths` | default paths already include `~/.local/bin` (agy's install dir) |
| Auth-required errors | `acp::is_auth_required` | matches ACP `AuthRequired` (-32000), which is what agy returns |
| Setup catalog test | `crates/goose/tests/acp_custom_provider_methods_test.rs` | provider-id list |
| Live e2e test | `crates/goose/tests/providers.rs` | `test_copilot_acp_provider` |
| User docs | `documentation/docs/guides/acp-providers.md` | Amp/Pi/Copilot sections |

## What we know about `agy acp` (from official docs)

- Start: `agy acp` (flags go after `acp`). Server name `Antigravity ACP Server`, ACP v1, stdio.
- Install: `curl -fsSL https://antigravity.google/cli/install.sh | bash` → `~/.local/bin/agy`. No npm package.
- Auth: interactive `agy` once, or `agy acp --login` (paste-code, works over SSH). Unauthenticated → `-32000 Authentication required`.
  Also `GEMINI_API_KEY` (needs `modelProvider: "gemini"` in `~/.gemini/antigravity-cli/settings.json`) and ADC (`AGY_ADC_AUTH=true`, `GOOGLE_CLOUD_PROJECT`/`LOCATION`).
- Session config options: **Model** and **Thinking level** (`Low|Medium|High|Extra|Max`). Option **ids not documented**.
- Session modes over ACP: **not documented**. The TUI has `auto | plan | default | accept-edits`.
- Flags: `--dangerously-skip-permissions`, `--sandbox`, `--agent <name>`, `--add-dir`, `--process-per-session`
  (docs: *required for HTTP MCP servers the client configures*), `--ide-mcp-port`, `--log-file`, `--disable-telemetry`.
- Logs: `~/.gemini/antigravity-cli/log/acp-*.log`.
- Supports session load/resume/list/close; resume doesn't replay history.

## Probe results so far (agy 1.5.3, 2026-11-08)

- `initialize` → `agentInfo: Antigravity ACP Server 1.5.3`, protocol 1. `loadSession`, session `resume/close/load/list/additionalDirectories`,
  `auth.logout`, prompt images + embedded context. `_meta.antigravity.systemPrompt.append = true` (could carry goose's system prompt later).
- `authMethods`: `oauth-personal`, `oauth-business`, `gemini-api-key`, `agent-platform`, and
  `terminal-login` (`type: "terminal"`, `args: ["--login"]`) — i.e. ACP terminal-auth: client re-runs the agent binary with `--login`.
  This is exactly what the setup UI's "auth" capability should invoke.
- `agy acp --help` confirms the flag list; also `--stdio` (default), `--include-default-customizations` (default true),
  `--app-data-dir`/`--gemini-dir`/`--cli_dir_name` (useful for test isolation), and `--log-file`.
- `session/new` returned `-32000 Authentication required` **even after `agy acp --login` succeeded** — but that run was a
  separate process with no `initialize` first, so inconclusive. **goose's ACP client never calls `authenticate`**, so if agy
  needs an explicit `authenticate(oauth-personal)` even with cached creds, the shared client needs an opt-in hook
  (e.g. `AcpProviderConfig::authenticate_method: Option<String>`). `docs/plans/agy_probe.py` answers this.

## Open questions (resolve with a live probe — Phase 0)

0. **Does `session/new` succeed after `initialize` alone with cached creds, or is `authenticate` required?** (run `agy_probe.py`)

1. **Mode ids** — does `session/new` return `modes.availableModes`? If yes, which ids (`auto`/`plan`/`default`/`accept-edits`?).
2. **Config option ids** — exact `configOptions[].id` for model and thinking level (`model`? `thinking_level`?).
3. **Model ids** — values in the model select (needed for `model_switch_name` in the e2e test).
4. **MCP passthrough** — are stdio `mcpServers` from `session/new` honored? Is `--process-per-session` really needed for HTTP ones (goose maps `StreamableHttp` extensions → `McpServer::Http`)?
5. **Thinking effort** — does goose's thinking-effort plumbing (`claude-acp` test uses `.test_thinking_effort(true)`) map onto agy's option, or is that a follow-up?
6. **ToS** — Google forum threads question third-party clients driving `agy`. `agy acp` is an official, documented
   integration surface for "other editors", so this is probably fine, but say so in the issue.

## Design (proposed, pending Phase 0 answers)

- Provider id `agy-acp`, display name "Antigravity CLI (ACP)", binary `agy`, base args `["acp"]`.
- Aliases: `["agy-acp", "agy", "antigravity"]`.
- Model: `configured_model_for_provider` + `session_config_options = [(<model id>, model)]` unless `current`;
  `model_config_option_id = Some(<model id>)`; implement `from_env_with_default_model` (copy `copilot_acp.rs`).
- Modes:
  - **If agy exposes ACP modes:** `Auto→auto`, `SmartApprove→accept-edits`, `Approve→default`, `Chat→plan`.
  - **If not:** empty `mode_mapping`, and for `GooseMode::Auto` add `--dangerously-skip-permissions` to args
    (per-process, so a mode change mid-session can't toggle it — note this in docs).
- MCP: add `--process-per-session` only if Phase 0 shows HTTP MCP servers are dropped without it.
- Inventory: `acp_inventory(AGY_ACP_PROVIDER_NAME, AGY_ACP_BINARY, true)`.
- Setup metadata: `cli_agent(AGY_ACP_BINARY, aliases).with_acp().with_docs_url("https://antigravity.google/docs/cli/acp/")`.
  Use `show_only_when_installed()` at first (like Pi) so users without `agy` don't see it, unless maintainers prefer
  it always listed. `with_capabilities(...)` stays off until install/auth work through the setup UI.
- No `env_remove` needed unless the probe shows nested-session detection (cf. `CLAUDECODE` for claude-acp).

## Roadmap / TODO

### Phase 0 — Probe the real server
- [x] Install `agy` and `agy acp --login` (done on Derek's machine).
- [x] `initialize` response captured (see Probe results).
- [ ] Run `python3 docs/plans/agy_probe.py --mcp-cmd "npx -y @modelcontextprotocol/server-everything" --prompt`
      and commit `docs/plans/agy-acp-probe.json` (check it for anything sensitive first).
- [ ] Answer open questions 1–5 above and update the Design section.

### Phase 1 — Provider
- [ ] `crates/goose/src/providers/agy_acp.rs` (consts `AGY_ACP_PROVIDER_NAME`, `AGY_ACP_BINARY`, metadata, `create()`).
- [ ] `pub mod agy_acp;` in `providers/mod.rs`.
- [ ] `agy_acp_inventory()` in `inventory/registrations.rs`.
- [ ] Register in `providers/init.rs` (alphabetical, next to `AmpAcpProvider`).

### Phase 2 — Tests
- [ ] Add `"agy-acp"` to the setup-catalog list in `tests/acp_custom_provider_methods_test.rs`
      (only if it isn't `show_only_when_installed`, otherwise it will be filtered out in CI).
- [ ] `test_agy_acp_provider` in `tests/providers.rs` (`// Requires: agy installed + agy acp --login`),
      with `model_switch_name`, and `.test_mcp_tools(false)` / `.test_thinking_effort(...)` per Phase 0.
- [ ] Unit test for the Auto-mode → `--dangerously-skip-permissions` args mapping if we go that route.

### Phase 3 — Docs
- [ ] `documentation/docs/guides/acp-providers.md`: Available / Setup / Configuration sections, plus `agy` in the Error Handling binary list.
- [ ] Mention API key / ADC env vars and the log location for troubleshooting.

### Phase 4 — Verify and land
- [ ] `cargo fmt`, `cargo clippy --all-targets -- -D warnings`, `GOOSE_DISABLE_KEYRING=1 cargo test -p goose`.
- [ ] Manual: `GOOSE_PROVIDER=agy-acp goose session` in each goose mode; model switch; one MCP extension.
- [ ] Desktop: provider shows in setup catalog; pick model.
- [ ] Open upstream issue from `.github/ISSUE_TEMPLATE/` (Feature) and wait for **Ready** before an upstream PR.

## Follow-ups (out of scope for v1)
- Thinking-level picker wired to goose thinking effort (if not done in Phase 1).
- Pass `--agent <name>` / `--add-dir` via provider config fields.
- Use `_meta.antigravity.systemPrompt.append` to pass goose's system prompt / hints.
- Support ACP `logout` from the setup UI.
- Setup-UI install/login actions (`with_capabilities(true, true, true)`) using `install.sh` and `agy acp --login`.
