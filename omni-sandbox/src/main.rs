mod sandbox;

use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use std::path::PathBuf;
use std::process::ExitCode;

/// Cross-platform sandbox for Omni Code.
///
/// exec: Process-level sandbox using platform-native isolation
///   - Linux:   bubblewrap (user namespaces + seccomp)
///   - macOS:   sandbox-exec (seatbelt profiles)
///   - Windows: AppContainer (restricted tokens + ACLs)
#[derive(Parser, Debug)]
#[command(name = "omni-sandbox", version)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand, Debug)]
enum Command {
    /// Run a process inside a platform-native sandbox.
    Exec(ExecArgs),
}

#[derive(Parser, Debug)]
struct ExecArgs {
    /// Directory to mount read-write inside the sandbox (the workspace).
    #[arg(long)]
    workspace: PathBuf,

    /// Additional directories to mount read-only.
    #[arg(long = "ro-bind")]
    ro_bind: Vec<PathBuf>,

    /// Additional directories to mount read-write.
    #[arg(long = "rw-bind")]
    rw_bind: Vec<PathBuf>,

    /// Allow unrestricted network access (default: blocked).
    #[arg(long, default_value_t = false)]
    net: bool,

    /// Unsupported: host-level network filtering cannot be enforced by the
    /// process sandbox. Passing this flag is a hard error (fail closed).
    #[arg(long, value_delimiter = ',', hide = true)]
    net_allow: Vec<String>,

    /// Working directory inside the sandbox (defaults to workspace mount point).
    #[arg(long)]
    cwd: Option<PathBuf>,

    /// Subpath names within writable roots to protect as read-only.
    #[arg(long = "protect")]
    protected_subpaths: Vec<String>,

    /// Disable default protected subpath enforcement.
    #[arg(long = "no-protected-subpaths", default_value_t = false)]
    no_protected_subpaths: bool,

    /// Maximum number of processes for the sandboxed command.
    ///
    /// Enforced via setrlimit(RLIMIT_NPROC) on Linux/macOS. RLIMIT_NPROC
    /// counts per-user processes, so the effective cap can only be stricter
    /// than this value, never looser. Unsupported on Windows (hard error).
    #[arg(long = "pids-limit")]
    pids_limit: Option<u32>,

    /// The command to run inside the sandbox.
    #[arg(last = true, required = true)]
    command: Vec<String>,
}

fn main() -> ExitCode {
    let cli = Cli::parse();

    let result = match cli.command {
        Command::Exec(args) => run_exec(args),
    };

    match result {
        Ok(code) => ExitCode::from(code),
        Err(e) => {
            eprintln!("omni-sandbox: {e:#}");
            ExitCode::from(125)
        }
    }
}

/// Build a `SandboxConfig` from parsed CLI args. Extracted for testability.
fn build_config(args: ExecArgs) -> Result<sandbox::SandboxConfig> {
    // Fail closed: a host allowlist cannot be enforced at the process-sandbox
    // level (bwrap/sandbox-exec/AppContainer have no reliable per-host
    // egress filtering). Refuse to run rather than silently granting full
    // network access.
    if !args.net_allow.is_empty() {
        bail!(
            "--net-allow is not supported: host-level network filtering cannot \
             be enforced by the process sandbox. Use --net for unrestricted \
             network access, or omit both flags to block network entirely. \
             Host allowlisting requires a container backend with network-level \
             enforcement."
        );
    }

    let workspace = args
        .workspace
        .canonicalize()
        .with_context(|| format!("workspace path not found: {}", args.workspace.display()))?;

    let protected_subpaths = if args.no_protected_subpaths {
        Vec::new()
    } else if args.protected_subpaths.is_empty() {
        sandbox::DEFAULT_PROTECTED_SUBPATHS
            .iter()
            .map(|s| s.to_string())
            .collect()
    } else {
        args.protected_subpaths
    };

    Ok(sandbox::SandboxConfig {
        workspace,
        ro_binds: args.ro_bind,
        rw_binds: args.rw_bind,
        allow_net: args.net,
        cwd: args.cwd,
        command: args.command,
        protected_subpaths,
        pids_limit: args.pids_limit,
    })
}

fn run_exec(args: ExecArgs) -> Result<u8> {
    let config = build_config(args)?;
    sandbox::exec(config)
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::Parser;

    #[test]
    fn exec_subcommand_parse() {
        let cli = Cli::parse_from([
            "omni-sandbox",
            "exec",
            "--workspace", "/tmp",
            "--", "/bin/true",
        ]);
        let Command::Exec(args) = cli.command;
        assert_eq!(args.workspace, PathBuf::from("/tmp"));
        assert!(!args.net);
        assert_eq!(args.command, vec!["/bin/true"]);
    }

    #[test]
    fn flat_args_are_rejected() {
        // The legacy no-subcommand mode is gone: a subcommand is required.
        let result = Cli::try_parse_from([
            "omni-sandbox",
            "--workspace", "/tmp",
            "--", "/bin/true",
        ]);
        assert!(result.is_err(), "flat args without a subcommand must be rejected");
    }

    #[test]
    fn exec_with_ro_bind_and_protect() {
        let cli = Cli::parse_from([
            "omni-sandbox",
            "exec",
            "--workspace", "/tmp",
            "--ro-bind", "/opt/venv",
            "--protect", ".secret",
            "--", "/bin/true",
        ]);
        let Command::Exec(args) = cli.command;
        assert_eq!(args.ro_bind, vec![PathBuf::from("/opt/venv")]);
        assert_eq!(args.protected_subpaths, vec![".secret".to_string()]);
    }

    #[test]
    fn pids_limit_parses_as_own_flag_value() {
        let cli = Cli::parse_from([
            "omni-sandbox",
            "exec",
            "--workspace", "/tmp",
            "--pids-limit", "256",
            "--", "/bin/true",
        ]);
        let Command::Exec(args) = cli.command;
        assert_eq!(args.pids_limit, Some(256));
    }

    #[test]
    fn pids_limit_defaults_to_none() {
        let cli = Cli::parse_from([
            "omni-sandbox",
            "exec",
            "--workspace", "/tmp",
            "--", "/bin/true",
        ]);
        let Command::Exec(args) = cli.command;
        assert_eq!(args.pids_limit, None);
    }

    #[test]
    fn pids_limit_rejects_non_numeric() {
        let result = Cli::try_parse_from([
            "omni-sandbox",
            "exec",
            "--workspace", "/tmp",
            "--pids-limit", "10G",
            "--", "/bin/true",
        ]);
        assert!(result.is_err(), "--pids-limit must be a plain integer");
    }

    #[test]
    fn pids_limit_flows_into_config() {
        let args = ExecArgs {
            workspace: PathBuf::from("/tmp"),
            ro_bind: vec![],
            rw_bind: vec![],
            net: false,
            net_allow: vec![],
            cwd: None,
            protected_subpaths: vec![],
            no_protected_subpaths: false,
            pids_limit: Some(128),
            command: vec!["/bin/true".into()],
        };
        let config = build_config(args).unwrap();
        assert_eq!(config.pids_limit, Some(128));
    }

    #[test]
    fn repeated_protect_flags_collect_paths() {
        // governance.py emits one --protect per subpath; a comma-joined value
        // would arrive as a single (wrong) subpath name, so the CLI contract
        // is repeated flags.
        let cli = Cli::parse_from([
            "omni-sandbox",
            "exec",
            "--workspace", "/tmp",
            "--protect", ".git",
            "--protect", ".env",
            "--", "/bin/true",
        ]);
        let Command::Exec(args) = cli.command;
        assert_eq!(
            args.protected_subpaths,
            vec![".git".to_string(), ".env".to_string()]
        );
    }

    #[test]
    fn net_allow_fails_closed() {
        let args = ExecArgs {
            workspace: PathBuf::from("/tmp"),
            ro_bind: vec![],
            rw_bind: vec![],
            net: false,
            net_allow: vec!["example.com".into()],
            cwd: None,
            protected_subpaths: vec![],
            no_protected_subpaths: false,
            pids_limit: None,
            command: vec!["/bin/true".into()],
        };
        let err = build_config(args).expect_err("--net-allow must be a hard error");
        assert!(
            err.to_string().contains("--net-allow is not supported"),
            "error should explain that host filtering is unsupported: {err}"
        );
    }

    #[test]
    fn net_allow_rejected_even_with_net() {
        let args = ExecArgs {
            workspace: PathBuf::from("/tmp"),
            ro_bind: vec![],
            rw_bind: vec![],
            net: true,
            net_allow: vec!["example.com".into()],
            cwd: None,
            protected_subpaths: vec![],
            no_protected_subpaths: false,
            pids_limit: None,
            command: vec!["/bin/true".into()],
        };
        assert!(
            build_config(args).is_err(),
            "--net-allow must be rejected regardless of --net"
        );
    }

    #[test]
    fn default_protected_subpaths_applied() {
        let args = ExecArgs {
            workspace: PathBuf::from("/tmp"),
            ro_bind: vec![],
            rw_bind: vec![],
            net: false,
            net_allow: vec![],
            cwd: None,
            protected_subpaths: vec![],
            no_protected_subpaths: false,
            pids_limit: None,
            command: vec!["/bin/true".into()],
        };
        let config = build_config(args).unwrap();
        assert_eq!(
            config.protected_subpaths,
            vec![".git", ".env", ".codex"],
            "should apply DEFAULT_PROTECTED_SUBPATHS"
        );
    }

    #[test]
    fn no_protected_subpaths_flag_clears_defaults() {
        let args = ExecArgs {
            workspace: PathBuf::from("/tmp"),
            ro_bind: vec![],
            rw_bind: vec![],
            net: false,
            net_allow: vec![],
            cwd: None,
            protected_subpaths: vec![],
            no_protected_subpaths: true,
            pids_limit: None,
            command: vec!["/bin/true".into()],
        };
        let config = build_config(args).unwrap();
        assert!(
            config.protected_subpaths.is_empty(),
            "--no-protected-subpaths should clear all defaults"
        );
    }

    #[test]
    fn custom_protected_subpaths_override_defaults() {
        let args = ExecArgs {
            workspace: PathBuf::from("/tmp"),
            ro_bind: vec![],
            rw_bind: vec![],
            net: false,
            net_allow: vec![],
            cwd: None,
            protected_subpaths: vec![".secrets".into()],
            no_protected_subpaths: false,
            pids_limit: None,
            command: vec!["/bin/true".into()],
        };
        let config = build_config(args).unwrap();
        assert_eq!(
            config.protected_subpaths,
            vec![".secrets"],
            "explicit --protect should replace defaults"
        );
    }
}
