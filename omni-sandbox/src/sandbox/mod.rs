use std::path::PathBuf;

#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "macos")]
mod macos;
#[cfg(target_os = "windows")]
mod windows;

/// Default directory names that should be protected (read-only) even inside
/// writable roots. These are re-bound as read-only after the writable mount
/// so the sandboxed process cannot modify them.
pub const DEFAULT_PROTECTED_SUBPATHS: &[&str] = &[".git", ".env", ".codex"];

/// Platform-agnostic sandbox configuration.
#[derive(Debug)]
pub struct SandboxConfig {
    /// Primary workspace directory — mounted read-write.
    pub workspace: PathBuf,
    /// Extra read-only bind mounts.
    pub ro_binds: Vec<PathBuf>,
    /// Extra read-write bind mounts.
    pub rw_binds: Vec<PathBuf>,
    /// Whether outbound network access is permitted. Network is all-or-nothing:
    /// host-level allowlists are rejected at the CLI layer because no process
    /// sandbox backend can reliably enforce them (fail closed).
    pub allow_net: bool,
    /// Working directory inside the sandbox (defaults to workspace).
    pub cwd: Option<PathBuf>,
    /// The command + arguments to execute.
    pub command: Vec<String>,
    /// Subpath names within writable roots to protect as read-only.
    /// Defaults to [`.git`, `.env`, `.codex`] if empty.
    pub protected_subpaths: Vec<String>,
    /// Maximum number of processes for the sandboxed command.
    ///
    /// Enforced via `setrlimit(RLIMIT_NPROC)` on Linux/macOS, applied in the
    /// child just before exec so it caps the whole sandboxed process tree.
    /// Note RLIMIT_NPROC counts processes per real UID, so the effective cap
    /// can only be *stricter* than the requested value (host processes owned
    /// by the same user count against it) — never looser. Unsupported on
    /// Windows: passing it there is a hard error (fail closed).
    pub pids_limit: Option<u32>,
}

/// Apply an RLIMIT_NPROC cap to a command, enforced in the child process
/// right before exec (so it is inherited by everything the sandbox runs).
///
/// The cap is clamped to the current hard limit (unprivileged processes
/// cannot raise it); clamping only ever makes the limit stricter. If the
/// rlimit cannot be applied the spawn fails — we never run without it.
#[cfg(unix)]
pub(crate) fn apply_pids_limit(cmd: &mut std::process::Command, limit: u32) {
    use std::os::unix::process::CommandExt;

    unsafe {
        cmd.pre_exec(move || {
            let mut rl = libc::rlimit {
                rlim_cur: 0,
                rlim_max: 0,
            };
            if libc::getrlimit(libc::RLIMIT_NPROC, &mut rl) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            let capped = std::cmp::min(limit as libc::rlim_t, rl.rlim_max);
            rl.rlim_cur = capped;
            rl.rlim_max = capped;
            if libc::setrlimit(libc::RLIMIT_NPROC, &rl) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
}

/// Launch the sandboxed process. Returns the child's exit code.
///
/// On Linux this calls `bwrap`, on macOS `sandbox-exec`, on Windows
/// the AppContainer API. Fails with an error if the sandbox cannot be
/// established — there is no unsandboxed fallback.
pub fn exec(config: SandboxConfig) -> anyhow::Result<u8> {
    #[cfg(target_os = "linux")]
    return linux::exec(config);

    #[cfg(target_os = "macos")]
    return macos::exec(config);

    #[cfg(target_os = "windows")]
    return windows::exec(config);

    #[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
    {
        anyhow::bail!(
            "No sandbox backend available for this platform. \
             Supported platforms: Linux, macOS, Windows."
        )
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::process::Command;

    fn current_nproc_hard_limit() -> libc::rlim_t {
        let mut rl = libc::rlimit {
            rlim_cur: 0,
            rlim_max: 0,
        };
        assert_eq!(
            unsafe { libc::getrlimit(libc::RLIMIT_NPROC, &mut rl) },
            0,
            "getrlimit(RLIMIT_NPROC) should succeed"
        );
        rl.rlim_max
    }

    #[test]
    fn apply_pids_limit_sets_rlimit_in_child() {
        // `ulimit -u` in the child reports the soft RLIMIT_NPROC — it must
        // equal the requested limit (clamped to the hard limit).
        let requested: u32 = 4321;
        let expected = std::cmp::min(requested as libc::rlim_t, current_nproc_hard_limit());

        let mut cmd = Command::new("/bin/sh");
        cmd.arg("-c").arg("ulimit -u");
        apply_pids_limit(&mut cmd, requested);

        let out = cmd.output().expect("child should spawn");
        assert!(out.status.success(), "ulimit -u should exit 0");
        let reported = String::from_utf8_lossy(&out.stdout).trim().to_string();
        assert_eq!(
            reported,
            expected.to_string(),
            "child RLIMIT_NPROC should match the requested pids limit"
        );
    }

    #[test]
    fn apply_pids_limit_is_clamped_to_hard_limit() {
        // Requesting more than the hard limit must clamp down (stricter),
        // never fail open by leaving the limit unset.
        let hard = current_nproc_hard_limit();
        if hard == libc::RLIM_INFINITY || hard >= u32::MAX as libc::rlim_t {
            // Can't exceed the hard limit with a u32 request — nothing to clamp.
            return;
        }
        let mut cmd = Command::new("/bin/sh");
        cmd.arg("-c").arg("ulimit -u");
        apply_pids_limit(&mut cmd, (hard as u32).saturating_add(1000));

        let out = cmd.output().expect("child should spawn");
        let reported = String::from_utf8_lossy(&out.stdout).trim().to_string();
        assert_eq!(reported, hard.to_string(), "limit should clamp to hard limit");
    }
}
