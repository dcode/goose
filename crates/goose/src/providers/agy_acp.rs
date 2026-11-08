use agent_client_protocol::schema::v1::McpServer;
use anyhow::Result;
use futures::future::BoxFuture;
use std::collections::HashMap;
use std::path::PathBuf;

use crate::acp::{
    configured_model_for_provider, extension_configs_to_mcp_servers, AcpProvider,
    AcpProviderConfig, ACP_CURRENT_MODEL,
};
use crate::config::search_path::SearchPaths;
use crate::config::{Config, GooseMode};
use crate::providers::base::{
    current_working_dir, ProviderDef, ProviderDescriptor, ProviderMetadata,
};
use crate::providers::catalog::ProviderSetupMetadata;

pub(crate) const AGY_ACP_PROVIDER_NAME: &str = "agy-acp";
const AGY_ACP_DOC_URL: &str = "https://antigravity.google/docs/cli/acp/";
pub(crate) const AGY_ACP_BINARY: &str = "agy";

const MODEL_CONFIG_OPTION_ID: &str = "model";
const MODE_DEFAULT: &str = "default";
const MODE_ACCEPT_EDITS: &str = "accept-edits";
const MODE_PLAN: &str = "plan";

pub struct AgyAcpProvider;

impl goose_providers::base::ProviderDescriptor for AgyAcpProvider {
    fn metadata() -> ProviderMetadata {
        ProviderMetadata::new(
            AGY_ACP_PROVIDER_NAME,
            "Antigravity CLI (ACP)",
            "Use goose with your Google Antigravity subscription via the agy CLI.",
            ACP_CURRENT_MODEL,
            vec![],
            AGY_ACP_DOC_URL,
            vec![],
        )
        .with_setup_steps(vec![
            "Install the Antigravity CLI: `curl -fsSL https://antigravity.google/cli/install.sh | bash`",
            "Run `agy acp --login` to authenticate with your Google account",
        ])
        .with_setup(
            ProviderSetupMetadata::cli_agent(AGY_ACP_BINARY, &["agy-acp", "agy", "antigravity"])
                .with_acp()
                .with_docs_url(AGY_ACP_DOC_URL)
                .show_only_when_installed(),
        )
    }
}

/// agy has no permission-bypass mode, so Auto shares `accept-edits` with SmartApprove;
/// goose auto-allows the remaining permission requests itself while in Auto.
fn mode_mapping() -> HashMap<GooseMode, Vec<String>> {
    HashMap::from([
        (GooseMode::Auto, vec![MODE_ACCEPT_EDITS.to_string()]),
        (GooseMode::SmartApprove, vec![MODE_ACCEPT_EDITS.to_string()]),
        (GooseMode::Approve, vec![MODE_DEFAULT.to_string()]),
        (GooseMode::Chat, vec![MODE_PLAN.to_string()]),
    ])
}

/// `--process-per-session` is what makes agy advertise HTTP MCP support; without it
/// goose's streamable-HTTP extensions are filtered out before `session/new`.
fn agy_args() -> Vec<String> {
    vec!["acp".to_string(), "--process-per-session".to_string()]
}

fn session_config_options(model: String) -> Vec<(String, String)> {
    if model == ACP_CURRENT_MODEL {
        vec![]
    } else {
        vec![(MODEL_CONFIG_OPTION_ID.to_string(), model)]
    }
}

/// agy names MCP tools `mcp_<server>_<tool>` and requires `^[a-zA-Z0-9_-]{1,64}$`, yet still
/// forwards invalid names to the model, whose API then rejects every turn with a 400.
/// goose extension names like "Chrome DevTools" or "plugin:server" are common.
fn tool_safe_server_name(name: &str) -> String {
    name.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect()
}

fn agy_mcp_servers(extensions: &[crate::config::ExtensionConfig]) -> Vec<McpServer> {
    extension_configs_to_mcp_servers(extensions)
        .into_iter()
        .map(|server| match server {
            McpServer::Stdio(mut s) => {
                s.name = tool_safe_server_name(&s.name);
                McpServer::Stdio(s)
            }
            McpServer::Http(mut s) => {
                s.name = tool_safe_server_name(&s.name);
                McpServer::Http(s)
            }
            other => other,
        })
        .collect()
}

impl AgyAcpProvider {
    fn create(
        extensions: Vec<crate::config::ExtensionConfig>,
        working_dir: PathBuf,
        use_default_model: bool,
    ) -> BoxFuture<'static, Result<AcpProvider>> {
        Box::pin(async move {
            let config = Config::global();
            let resolved_command = SearchPaths::builder().resolve(AGY_ACP_BINARY)?;
            let goose_mode = config.resolve_goose_mode()?;
            let model = if use_default_model {
                ACP_CURRENT_MODEL.to_string()
            } else {
                configured_model_for_provider(config, AGY_ACP_PROVIDER_NAME)
            };
            let mode_mapping = mode_mapping();

            let provider_config = AcpProviderConfig {
                command: resolved_command,
                args: agy_args(),
                env: vec![],
                env_remove: vec![],
                work_dir: working_dir,
                mcp_servers: agy_mcp_servers(&extensions),
                session_mode_id: mode_mapping[&goose_mode].first().cloned(),
                session_config_options: session_config_options(model),
                model_config_option_id: Some(MODEL_CONFIG_OPTION_ID.to_string()),
                mode_mapping,
                notification_callback: None,
            };

            let metadata = Self::metadata();
            AcpProvider::connect(metadata.name, goose_mode, provider_config).await
        })
    }
}

impl ProviderDef for AgyAcpProvider {
    type Provider = AcpProvider;
    const SESSION_BOUND: bool = true;
    const RUNS_OWN_TOOL_LOOP: bool = true;

    fn from_env(
        extensions: Vec<crate::config::ExtensionConfig>,
        tls_config: Option<crate::providers::api_client::TlsConfig>,
    ) -> BoxFuture<'static, Result<AcpProvider>> {
        Self::from_env_with_working_dir(extensions, current_working_dir(), tls_config)
    }

    fn from_env_with_working_dir(
        extensions: Vec<crate::config::ExtensionConfig>,
        working_dir: PathBuf,
        _tls_config: Option<crate::providers::api_client::TlsConfig>,
    ) -> BoxFuture<'static, Result<AcpProvider>> {
        Self::create(extensions, working_dir, false)
    }

    fn from_env_with_default_model(
        extensions: Vec<crate::config::ExtensionConfig>,
        _tls_config: Option<crate::providers::api_client::TlsConfig>,
    ) -> BoxFuture<'static, Result<AcpProvider>> {
        Self::create(extensions, current_working_dir(), true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use test_case::test_case;

    #[test_case(GooseMode::Auto, MODE_ACCEPT_EDITS)]
    #[test_case(GooseMode::SmartApprove, MODE_ACCEPT_EDITS)]
    #[test_case(GooseMode::Approve, MODE_DEFAULT)]
    #[test_case(GooseMode::Chat, MODE_PLAN)]
    fn maps_every_goose_mode_to_an_agy_mode(goose_mode: GooseMode, agy_mode: &str) {
        assert_eq!(mode_mapping()[&goose_mode], vec![agy_mode.to_string()]);
    }

    #[test_case("Chrome DevTools", "Chrome_DevTools")]
    #[test_case("rpi-ctags:ctags", "rpi-ctags_ctags")]
    #[test_case("developer", "developer")]
    fn sanitizes_mcp_server_names_for_tool_names(name: &str, expected: &str) {
        assert_eq!(tool_safe_server_name(name), expected);
    }

    #[test]
    fn launches_acp_server_with_http_mcp_support() {
        assert_eq!(agy_args(), vec!["acp", "--process-per-session"]);
    }

    #[test]
    fn current_model_sends_no_model_option() {
        assert!(session_config_options(ACP_CURRENT_MODEL.to_string()).is_empty());
    }

    #[test]
    fn configured_model_is_sent_as_model_option() {
        assert_eq!(
            session_config_options("gemini-3.8-flash".to_string()),
            vec![("model".to_string(), "gemini-3.8-flash".to_string())]
        );
    }
}
