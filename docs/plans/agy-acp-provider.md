# Plan: `agy-acp` provider (Antigravity CLI over ACP)

Status: **Phases 1–3 written (provider, tests, docs); Phase 4 verification in progress.**
Branch: `claude/elegant-johnson-lwx4rh`. Last updated 2026-11-08.

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
and calls `AcpProvider::connect`. All protocol work lives in `crates/goose/src/acp/provider.rs`, and it already:

- applies the model via a config option of category `model` (`model_config_option_id`),
- exposes thinking effort for any option of category `thought_level` (no per-provider code),
- switches modes via `session/set_mode` or a `mode`-category config option,
- **auto-allows `session/request_permission` when goose is in Auto mode** (`provider.rs` ~L2390),
- detects `-32000 AuthRequired` (`acp::is_auth_required`),
- never calls `authenticate` (fine for agy — see probe results).

| Concern | Where | Reference impl |
|---|---|---|
| Provider def + metadata | `crates/goose/src/providers/<name>.rs` | `copilot_acp.rs` (vendor binary + arg, model option) |
| Module export | `crates/goose/src/providers/mod.rs` | `pub mod copilot_acp;` |
| Registry | `crates/goose/src/providers/init.rs` | `register_with_inventory::<CopilotAcpProvider>(false, …)` |
| Inventory (installed? / model refresh) | `crates/goose/src/providers/inventory/registrations.rs` | `copilot_acp_inventory()` → `acp_inventory(name, binary, true)` |
| Binary lookup | `config::search_path::SearchPaths` | default paths already include `~/.local/bin` (agy's install dir) |
| Setup catalog test | `crates/goose/tests/acp_custom_provider_methods_test.rs` | provider-id list |
| Live e2e test | `crates/goose/tests/providers.rs` | `test_copilot_acp_provider` |
| User docs | `documentation/docs/guides/acp-providers.md` | Amp/Pi/Copilot sections |

## Probe results (agy 1.5.3, macOS, Google AI Pro tier, 2026-11-08)

Raw transcript: run `docs/plans/agy_probe.py`. Summary:

- **Auth:** after `agy acp --login`, `initialize` → `session/new` succeeds with **no `authenticate` call**.
  The earlier `-32000` was a probe artifact (separate process, no `initialize`).
- **authMethods:** `oauth-personal`, `oauth-business`, `gemini-api-key`, `agent-platform`; plus
  `terminal-login` (`type: "terminal"`, `args: ["--login"]`) **only when the client declares `clientCapabilities.auth.terminal`**.
- **agentCapabilities:** `loadSession`; session `resume/close/load/list/additionalDirectories`; `auth.logout`;
  prompt image + embeddedContext (no audio); `_meta.antigravity.systemPrompt.append = true`.
- **Modes** (`modes.availableModes` and a `mode`-category config option, both present):
  `default` (ask before edits/commands), `accept-edits`, `plan`. Initial `default`. **No bypass/auto mode.**
- **Config options:**
  - `id: "model"`, category `model`: `gemini-4-argon` (default), `gemini-3.8-flash`, `claude-opus-5-5`, `claude-sonnet-5-5`, `gpt-oss-120b-medium`.
  - `id: "thought_level"`, category `thought_level`: `low | medium (default) | high`.
  - `id: "mode"`, category `mode`: same values as the modes above.
- **Legacy `models`** (`session/set_model`): model × effort combos, e.g. `gemini-4-argon-high`. Ignore; use the config options.
- **`_meta.antigravity.tier`** on `session/new`: `{name: "Google AI Pro", id: "g1-pro-tier", upgradeUri}`. The model list likely varies by tier.
- **MCP passthrough works.** A stdio `mcpServers` entry (`server-everything`) shows up as tools for the model,
  with or without extra flags.
- **HTTP MCP needs `--process-per-session`.** Only with that flag does `initialize` advertise
  `mcpCapabilities: {http: true}`. goose's `filter_supported_servers` (`provider.rs` ~L1921) drops `McpServer::Http`
  entries when `http` is false, so without the flag goose's StreamableHttp extensions would be silently dropped.
- **Usage split:** `inputTokens` excludes cache hits (2nd run: `inputTokens 2051`, `cachedReadTokens 15230`,
  `totalTokens 17659`). goose maps only input/output/total, so input looks tiny while total is right.
  That's a shared ACP-client issue, not agy-specific.
- **`session/prompt` result includes `usage`** (`inputTokens`, `outputTokens`, `cachedReadTokens`, `cachedWriteTokens`,
  `thoughtTokens`, `totalTokens`). Check goose maps it into its token accounting.
- Extra notifications goose must tolerate: `available_commands_update` (~70 slash commands/skills: `/goal`, `/compact`,
  `/plan`, plus bundled skills), `session_info_update` (`title`), `user_message_chunk` (prompt echo — make sure goose
  doesn't render it twice).
- Bare system prompt costs ~12.5k input tokens per turn (agy's own instructions + skills list). Worth noting in docs.
- `agy acp --help` flags of interest: `--dangerously-skip-permissions`, `--sandbox`, `--agent`, `--add-dir`,
  `--process-per-session`, `--log-file`, `--disable-telemetry`, `--app-data-dir` / `--gemini-dir` / `--cli_dir_name`,
  `--include-default-customizations` (default true).

## Design (decided)

- Provider id `agy-acp`, display name "Antigravity CLI (ACP)", binary `agy`, args `["acp", "--process-per-session"]`.
  Aliases `["agy-acp", "agy", "antigravity"]`.
- **Model:** copy `copilot_acp.rs` — `configured_model_for_provider`, `session_config_options = [("model", m)]` unless `current`,
  `model_config_option_id = Some("model")`, plus `from_env_with_default_model`. Base model ids (`gemini-4-argon`), not the legacy suffixed ones.
- **Thinking effort:** nothing to write; the generic `thought_level` handling picks it up. E2e test sets `.test_thinking_effort(true)`.
- **Modes:**

  | goose | agy mode | why |
  |---|---|---|
  | Auto | `accept-edits` | edits auto-approved by agy; anything else goose auto-allows via request_permission |
  | SmartApprove | `accept-edits` | edits without asking, commands still prompt |
  | Approve | `default` | asks before edits and commands |
  | Chat | `plan` | closest to no-execution |

  Two goose modes map to `accept-edits`. Check that `resolve_mode` picks the current goose mode when agy reports
  `accept-edits` back (the copilot mapping already has this many-to-one shape, so it should).
  **Do not pass `--dangerously-skip-permissions`:** it is per-process, so a mid-session mode switch could not undo it,
  and goose's Auto handling already covers it.
- **Auth:** none in the provider. Setup steps tell the user to run `agy acp --login`. Desktop "authenticate" action
  (`with_capabilities(_, true, _)`) is a follow-up. goose doesn't declare `auth.terminal`, so agy won't advertise `terminal-login` to it anyway.
- **Inventory:** `acp_inventory(AGY_ACP_PROVIDER_NAME, AGY_ACP_BINARY, true)` (model refresh supported).
- **Setup metadata:** `cli_agent("agy", aliases).with_acp().with_docs_url("https://antigravity.google/docs/cli/acp/")`,
  `.show_only_when_installed()` for v1, so users without `agy` don't see it. Capabilities stay off.
- **MCP:** pass `extension_configs_to_mcp_servers(&extensions)` as usual, and launch with **`args = ["acp", "--process-per-session"]`** so HTTP extensions are accepted. Cheap: goose already runs one agy process per goose session (`SESSION_BOUND`).

## Open questions

All resolved:
- `user_message_chunk` echo: falls through `_ => {}` in `provider.rs`, so it is ignored (no double render).
- Two goose modes on `accept-edits`: `resolve_mode` keeps the current goose mode when it's among the candidates.
- ToS: `agy acp` is Google's documented way for third-party editors to connect; mention it in the upstream issue.

## Roadmap / TODO

### Phase 0 — Probe the real server
- [x] Install `agy`, `agy acp --login`.
- [x] `initialize` + `session/new` captured; auth, modes, models and thinking settled (see Probe results).
- [x] MCP + prompt probe: stdio works; HTTP needs `--process-per-session`.

### Phase 1 — Provider
- [x] `crates/goose/src/providers/agy_acp.rs` (consts `AGY_ACP_PROVIDER_NAME`, `AGY_ACP_BINARY`, metadata, `create()`, mode mapping).
- [x] `pub mod agy_acp;` in `providers/mod.rs`.
- [x] `agy_acp_inventory()` in `inventory/registrations.rs`.
- [x] Register in `providers/init.rs` (alphabetical, after `AmpAcpProvider`).

### Phase 2 — Tests
- [x] Unit tests in `agy_acp.rs`: mode mapping, launch args, model option (`resolve_mode` itself is already covered in `provider.rs`).
- [x] `test_agy_acp_provider` in `tests/providers.rs` (`// Requires: agy installed + agy acp --login`):
      `.model_switch_name("gemini-3.8-flash")`, `.test_thinking_effort(true)`, `.test_mcp_tools(true)`.
- [x] Skip adding to `acp_custom_provider_methods_test.rs` while `show_only_when_installed` (CI has no `agy`).

### Phase 3 — Docs
- [x] `documentation/docs/guides/acp-providers.md`: Available / Setup / Configuration sections; add `agy` to the Error Handling binary list.
- [x] Troubleshooting: logs at `~/.gemini/antigravity-cli/log/acp-*.log`; API key / ADC env vars (`GEMINI_API_KEY`, `AGY_ADC_AUTH`).

### Phase 4 — Verify and land
- [ ] `cargo fmt`, `cargo clippy --all-targets -- -D warnings`, `GOOSE_DISABLE_KEYRING=1 cargo test -p goose`.
- [ ] Manual: `GOOSE_PROVIDER=agy-acp goose session` in each goose mode; model switch; thinking switch; one MCP extension.
- [ ] Desktop: provider shows in setup catalog when `agy` is installed; model and thinking pickers work.
- [ ] Manual test on Intel macOS 15 using the CLI artifact from `.github/workflows/agy-test-build.yml` (temporary; runs on every push to this branch).
- [ ] Before an upstream PR: delete `.github/workflows/agy-test-build.yml`, `docs/plans/` (move notes into the issue).
- [ ] Open upstream issue from `.github/ISSUE_TEMPLATE/` (Feature); wait for **Ready** before an upstream PR.

## Follow-ups (out of scope for v1)
- Desktop setup auth action running `agy acp --login` (or declare `auth.terminal` in goose's ACP client generically).
- Pass `--agent <name>` / `--add-dir` via provider config.
- Use `_meta.antigravity.systemPrompt.append` to pass goose's system prompt / hints.
- ACP client: map `cachedReadTokens`/`cachedWriteTokens` into goose `Usage` (affects all ACP providers).
- Surface `_meta.antigravity.tier` (e.g. in provider status) and ACP `logout` in the setup UI.
