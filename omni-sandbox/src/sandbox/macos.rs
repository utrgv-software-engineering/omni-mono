use super::SandboxConfig;
use anyhow::{bail, Context, Result};
use std::process::Command;
use tempfile::NamedTempFile;

/// Build a seatbelt (.sb) profile for sandbox-exec.
fn build_profile(config: &SandboxConfig) -> String {
    let mut sb = String::new();

    sb.push_str("(version 1)\n");
    sb.push_str("(deny default)\n\n");

    // --- Process execution ---
    sb.push_str("; Allow executing binaries\n");
    sb.push_str("(allow process-exec)\n");
    sb.push_str("(allow process-fork)\n\n");

    // --- Signal handling ---
    sb.push_str("(allow signal (target self))\n\n");

    // --- System read access ---
    sb.push_str("; Read-only access to system paths\n");
    for dir in &[
        "/usr",
        "/bin",
        "/sbin",
        "/Library",
        "/System",
        "/private/var/db",
        "/private/etc",
        "/etc",
        "/var",
        "/dev",
        "/opt/homebrew",
    ] {
        sb.push_str(&format!("(allow file-read* (subpath \"{dir}\"))\n"));
    }
    sb.push('\n');

    // --- Temp files ---
    sb.push_str("; Writable temp directories\n");
    sb.push_str("(allow file* (subpath \"/private/tmp\"))\n");
    sb.push_str("(allow file* (subpath \"/tmp\"))\n");
    if let Ok(tmpdir) = std::env::var("TMPDIR") {
        sb.push_str(&format!("(allow file* (subpath \"{tmpdir}\"))\n"));
    }
    sb.push('\n');

    // --- Home directory (read-only by default) ---
    if let Ok(home) = std::env::var("HOME") {
        sb.push_str("; Home directory — read-only baseline, write to workspace\n");
        sb.push_str(&format!("(allow file-read* (subpath \"{home}\"))\n"));
    }
    sb.push('\n');

    // --- Workspace (read-write) ---
    let ws = config.workspace.to_string_lossy();
    sb.push_str("; Workspace — full read-write access\n");
    sb.push_str(&format!("(allow file* (subpath \"{ws}\"))\n"));

    // --- Protected subpaths within the workspace (read-only override) ---
    // Deny writes to sensitive directories even inside the writable workspace.
    for name in &config.protected_subpaths {
        let subpath = config.workspace.join(name);
        let sp = subpath.to_string_lossy();
        sb.push_str(&format!(
            "(deny file-write* (subpath \"{sp}\"))\n"
        ));
    }

    // --- Extra binds ---
    for path in &config.ro_binds {
        let p = path.to_string_lossy();
        sb.push_str(&format!("(allow file-read* (subpath \"{p}\"))\n"));
    }
    for path in &config.rw_binds {
        let p = path.to_string_lossy();
        sb.push_str(&format!("(allow file* (subpath \"{p}\"))\n"));
        // Protect subpaths in extra writable roots too.
        for name in &config.protected_subpaths {
            let subpath = path.join(name);
            let sp = subpath.to_string_lossy();
            sb.push_str(&format!(
                "(deny file-write* (subpath \"{sp}\"))\n"
            ));
        }
    }
    sb.push('\n');

    // --- Mach / IPC (needed for basic process operation on macOS) ---
    sb.push_str("; Mach and IPC (required for process lifecycle)\n");
    sb.push_str("(allow mach-lookup)\n");
    sb.push_str("(allow mach-register)\n");
    sb.push_str("(allow ipc-posix-shm-read-data)\n");
    sb.push_str("(allow ipc-posix-shm-write-data)\n");
    sb.push_str("(allow ipc-posix-shm-write-create)\n\n");

    // --- Sysctl (basic process queries) ---
    sb.push_str("(allow sysctl-read)\n\n");

    // --- Network ---
    // Network is all-or-nothing: host-level allowlists are rejected at the
    // CLI layer (fail closed) because seatbelt's per-host filtering is not
    // reliable for hostnames (it filters by IP at connect time, so CDN or
    // multi-A-record hosts leak) and no other backend supports it at all.
    if config.allow_net {
        sb.push_str("; Network access allowed\n");
        sb.push_str("(allow network*)\n");
    } else {
        sb.push_str("; Network blocked — allow only localhost\n");
        sb.push_str("(allow network* (local ip \"localhost:*\"))\n");
        sb.push_str("(allow network* (remote ip \"localhost:*\"))\n");
        sb.push_str("(allow network* (remote unix-socket))\n");
    }

    sb
}

pub fn exec(config: SandboxConfig) -> Result<u8> {
    if config.command.is_empty() {
        bail!("no command specified");
    }

    let profile = build_profile(&config);

    // Write profile to a temp file (sandbox-exec -f <path>).
    let profile_file = NamedTempFile::with_suffix(".sb")
        .context("failed to create temp sandbox profile")?;
    std::fs::write(profile_file.path(), &profile)
        .context("failed to write sandbox profile")?;

    let cwd = config.cwd.as_ref().unwrap_or(&config.workspace);

    let mut cmd = Command::new("sandbox-exec");
    cmd.arg("-f")
        .arg(profile_file.path())
        .args(&config.command)
        .current_dir(cwd)
        .stdin(std::process::Stdio::inherit())
        .stdout(std::process::Stdio::inherit())
        .stderr(std::process::Stdio::inherit());

    // --- Process count limit ---
    // Applied in the child pre-exec so sandbox-exec and everything it runs
    // inherit the RLIMIT_NPROC cap. If it can't be applied, the spawn fails
    // (closed).
    if let Some(limit) = config.pids_limit {
        super::apply_pids_limit(&mut cmd, limit);
    }

    let status = cmd.status().context("failed to spawn sandbox-exec")?;

    Ok(status.code().unwrap_or(1) as u8)
}
