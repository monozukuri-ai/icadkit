//! Records the resolved `parasolid-core` version from the workspace lock file.
//!
//! The crate reports which backend release it was compiled with
//! (`PARASOLID_CORE_VERSION`). Reading it from `Cargo.lock` keeps the manifest
//! pin the only place in the source that names that version.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

fn main() -> Result<(), String> {
    let manifest_dir = env::var_os("CARGO_MANIFEST_DIR").ok_or("CARGO_MANIFEST_DIR is not set")?;
    let lock = PathBuf::from(manifest_dir)
        .ancestors()
        .map(|dir| dir.join("Cargo.lock"))
        .find(|path| path.is_file())
        .ok_or("no Cargo.lock above the crate directory")?;
    let version = resolved_version(&lock, "parasolid-core")?;
    println!("cargo:rerun-if-changed={}", lock.display());
    println!("cargo:rustc-env=ICAD_PARASOLID_CORE_VERSION={version}");
    Ok(())
}

/// The version of `package` in the lock file, which must resolve it exactly once.
fn resolved_version(lock: &Path, package: &str) -> Result<String, String> {
    let text = fs::read_to_string(lock)
        .map_err(|error| format!("cannot read {}: {error}", lock.display()))?;
    let mut versions = Vec::new();
    let mut name = None;
    for line in text.lines().map(str::trim) {
        if line == "[[package]]" {
            name = None;
        } else if let Some(value) = line.strip_prefix("name = ") {
            name = Some(value.trim_matches('"'));
        } else if let Some(value) = line.strip_prefix("version = ")
            && name == Some(package)
        {
            versions.push(value.trim_matches('"').to_owned());
        }
    }
    match versions.as_slice() {
        [version] => Ok(version.clone()),
        [] => Err(format!("{package} is not resolved in {}", lock.display())),
        _ => Err(format!(
            "{package} resolves to several versions in {}",
            lock.display()
        )),
    }
}
