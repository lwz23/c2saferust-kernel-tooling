// SPDX-License-Identifier: GPL-2.0

use std::{
    collections::{BTreeMap, BTreeSet},
    env, fs,
    path::{Path, PathBuf},
};

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct ProofSite {
    file: String,
    line: usize,
    kind: String,
    status: String,
    id: String,
}

#[derive(Clone, Debug)]
struct StructuralRule {
    id: String,
    status: String,
    file: String,
    must_contain: Vec<String>,
    must_not_contain: Vec<String>,
}

#[derive(Default)]
struct Config {
    driver_files: Vec<String>,
    driver_forbidden: Vec<String>,
    allow_unsafe_files: Vec<String>,
    proof_sites: Vec<ProofSite>,
    structural_rules: Vec<StructuralRule>,
}

fn parse_args() -> Result<(PathBuf, PathBuf), String> {
    let mut repo_root = None;
    let mut config = None;
    let mut args = env::args().skip(1);

    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--repo-root" => repo_root = args.next().map(PathBuf::from),
            "--config" => config = args.next().map(PathBuf::from),
            _ => return Err(format!("unknown argument: {arg}")),
        }
    }

    let repo_root = repo_root.ok_or_else(|| "missing --repo-root".to_string())?;
    let config = config.ok_or_else(|| "missing --config".to_string())?;
    Ok((repo_root, config))
}

fn parse_config(path: &Path) -> Result<Config, String> {
    let mut config = Config::default();
    let content = fs::read_to_string(path).map_err(|err| err.to_string())?;

    for line in content.lines() {
        if line.trim().is_empty() {
            continue;
        }
        let parts: Vec<&str> = line.split('\t').collect();
        match parts.as_slice() {
            ["DRIVER_FILE", file] => config.driver_files.push((*file).to_string()),
            ["DRIVER_FORBIDDEN", token] => config.driver_forbidden.push((*token).to_string()),
            ["ALLOW_UNSAFE_FILE", file] => config.allow_unsafe_files.push((*file).to_string()),
            ["PROOF_SITE", file, line, kind, status, id] => {
                config.proof_sites.push(ProofSite {
                    file: (*file).to_string(),
                    line: line.parse().map_err(|_| format!("bad line number in {path:?}: {line}"))?,
                    kind: (*kind).to_string(),
                    status: (*status).to_string(),
                    id: (*id).to_string(),
                });
            }
            ["STRUCTURAL_RULE", id, status, file, must_contain, must_not_contain] => {
                config.structural_rules.push(StructuralRule {
                    id: (*id).to_string(),
                    status: (*status).to_string(),
                    file: (*file).to_string(),
                    must_contain: must_contain
                        .split('\u{1f}')
                        .filter(|item| !item.is_empty())
                        .map(ToOwned::to_owned)
                        .collect(),
                    must_not_contain: must_not_contain
                        .split('\u{1f}')
                        .filter(|item| !item.is_empty())
                        .map(ToOwned::to_owned)
                        .collect(),
                });
            }
            _ => return Err(format!("unrecognized config line: {line}")),
        }
    }

    Ok(config)
}

fn strip_rust_noncode(text: &str) -> String {
    let mut result = String::with_capacity(text.len());
    let chars: Vec<char> = text.chars().collect();
    let mut index = 0;
    let mut block_comment_depth = 0usize;
    let mut in_line_comment = false;
    let mut in_string = false;
    let mut in_char = false;
    let mut escape = false;

    while index < chars.len() {
        let ch = chars[index];
        let next = chars.get(index + 1).copied().unwrap_or('\0');

        if in_line_comment {
            if ch == '\n' {
                in_line_comment = false;
                result.push(ch);
            } else {
                result.push(' ');
            }
            index += 1;
            continue;
        }

        if block_comment_depth > 0 {
            if ch == '/' && next == '*' {
                block_comment_depth += 1;
                result.push(' ');
                result.push(' ');
                index += 2;
            } else if ch == '*' && next == '/' {
                block_comment_depth -= 1;
                result.push(' ');
                result.push(' ');
                index += 2;
            } else {
                result.push(if ch == '\n' { '\n' } else { ' ' });
                index += 1;
            }
            continue;
        }

        if in_string {
            if escape {
                escape = false;
                result.push(' ');
            } else if ch == '\\' {
                escape = true;
                result.push(' ');
            } else if ch == '"' {
                in_string = false;
                result.push(' ');
            } else {
                result.push(if ch == '\n' { '\n' } else { ' ' });
            }
            index += 1;
            continue;
        }

        if in_char {
            if escape {
                escape = false;
                result.push(' ');
            } else if ch == '\\' {
                escape = true;
                result.push(' ');
            } else if ch == '\'' {
                in_char = false;
                result.push(' ');
            } else {
                result.push(if ch == '\n' { '\n' } else { ' ' });
            }
            index += 1;
            continue;
        }

        if ch == '/' && next == '/' {
            in_line_comment = true;
            result.push(' ');
            result.push(' ');
            index += 2;
            continue;
        }

        if ch == '/' && next == '*' {
            block_comment_depth = 1;
            result.push(' ');
            result.push(' ');
            index += 2;
            continue;
        }

        if ch == '"' {
            in_string = true;
            result.push(' ');
            index += 1;
            continue;
        }

        if ch == '\'' {
            in_char = true;
            result.push(' ');
            index += 1;
            continue;
        }

        result.push(ch);
        index += 1;
    }

    result
}

fn normalize_whitespace(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn compact_whitespace(text: &str) -> String {
    text.split_whitespace().collect::<String>()
}

fn token_is_present(line: &str, token: &str) -> bool {
    if token == "unsafe" {
        let bytes = line.as_bytes();
        let needle = b"unsafe";
        let mut index = 0;
        while let Some(pos) = line[index..].find("unsafe") {
            let start = index + pos;
            let end = start + needle.len();
            let left_ok = start == 0 || !bytes[start - 1].is_ascii_alphanumeric() && bytes[start - 1] != b'_';
            let right_ok =
                end == bytes.len() || (!bytes[end].is_ascii_alphanumeric() && bytes[end] != b'_');
            if left_ok && right_ok {
                return true;
            }
            index = end;
        }
        false
    } else {
        line.contains(token)
    }
}

fn collect_unsafe_sites(file: &str, stripped: &str) -> Vec<(String, usize, String)> {
    let mut sites = Vec::new();
    for (line_index, line) in stripped.lines().enumerate() {
        if !line.contains("unsafe") {
            continue;
        }
        let kind = if line.contains("unsafe impl") {
            "unsafe-impl"
        } else if line.contains("unsafe fn") {
            "unsafe-fn"
        } else if line.contains("unsafe trait") {
            "unsafe-trait"
        } else if line.contains("unsafe {") {
            "unsafe-block"
        } else {
            "unsafe-token"
        };
        sites.push((file.to_string(), line_index + 1, kind.to_string()));
    }
    sites
}

fn main() -> Result<(), String> {
    let (repo_root, config_path) = parse_args()?;
    let config = parse_config(&config_path)?;
    let mut violations: Vec<(String, String, Option<usize>, String)> = Vec::new();

    for driver_file in &config.driver_files {
        let path = repo_root.join(driver_file);
        let text = fs::read_to_string(&path).map_err(|err| format!("{driver_file}: {err}"))?;
        let stripped = strip_rust_noncode(&text);
        for token in &config.driver_forbidden {
            for (line_number, line) in stripped.lines().enumerate() {
                if token_is_present(line, token) {
                    violations.push((
                        "driver-forbidden-token".to_string(),
                        driver_file.clone(),
                        Some(line_number + 1),
                        format!("driver contains forbidden token `{token}`"),
                    ));
                }
            }
        }
    }

    let configured_sites: BTreeSet<(String, usize, String)> = config
        .proof_sites
        .iter()
        .map(|site| (site.file.clone(), site.line, site.kind.clone()))
        .collect();

    let proof_statuses: BTreeMap<(String, usize, String), (&str, &str)> = config
        .proof_sites
        .iter()
        .map(|site| {
            (
                (site.file.clone(), site.line, site.kind.clone()),
                (site.status.as_str(), site.id.as_str()),
            )
        })
        .collect();

    let mut actual_sites = BTreeSet::new();
    for file in &config.allow_unsafe_files {
        let path = repo_root.join(file);
        let text = fs::read_to_string(&path).map_err(|err| format!("{file}: {err}"))?;
        let stripped = strip_rust_noncode(&text);
        for site in collect_unsafe_sites(file, &stripped) {
            actual_sites.insert(site);
        }
    }

    for site in &actual_sites {
        if !configured_sites.contains(site) {
            violations.push((
                "unaccounted-unsafe-site".to_string(),
                site.0.clone(),
                Some(site.1),
                "unsafe site is not covered by soundness-discharge.json".to_string(),
            ));
        }
    }

    for site in &configured_sites {
        if !actual_sites.contains(site) {
            violations.push((
                "stale-proof-site".to_string(),
                site.0.clone(),
                Some(site.1),
                "soundness-discharge.json contains a stale unsafe site entry".to_string(),
            ));
        } else if let Some((status, proof_id)) = proof_statuses.get(site) {
            if *status != "discharged" {
                violations.push((
                    "undischarged-proof-site".to_string(),
                    site.0.clone(),
                    Some(site.1),
                    format!("proof site `{proof_id}` is not discharged"),
                ));
            }
        }
    }

    for rule in &config.structural_rules {
        let path = repo_root.join(&rule.file);
        let text = fs::read_to_string(&path).map_err(|err| format!("{}: {err}", rule.file))?;
        let normalized_text = normalize_whitespace(&text);
        let compact_text = compact_whitespace(&text);
        if rule.status != "discharged" {
            violations.push((
                "blocked-structural-rule".to_string(),
                rule.file.clone(),
                None,
                format!("structural rule `{}` is not discharged", rule.id),
            ));
        }
        for needle in &rule.must_contain {
            let normalized_needle = normalize_whitespace(needle);
            let compact_needle = compact_whitespace(needle);
            if !text.contains(needle)
                && !normalized_text.contains(&normalized_needle)
                && !compact_text.contains(&compact_needle)
            {
                violations.push((
                    "missing-structural-pattern".to_string(),
                    rule.file.clone(),
                    None,
                    format!("rule `{}` requires pattern `{needle}`", rule.id),
                ));
            }
        }
        for needle in &rule.must_not_contain {
            let normalized_needle = normalize_whitespace(needle);
            let compact_needle = compact_whitespace(needle);
            if text.contains(needle)
                || normalized_text.contains(&normalized_needle)
                || compact_text.contains(&compact_needle)
            {
                violations.push((
                    "forbidden-structural-pattern".to_string(),
                    rule.file.clone(),
                    None,
                    format!("rule `{}` forbids pattern `{needle}`", rule.id),
                ));
            }
        }
    }

    for (kind, file, line, message) in &violations {
        println!(
            "VIOLATION\t{kind}\t{file}\t{}\t{message}",
            line.map(|line| line.to_string()).unwrap_or_else(|| "-".to_string())
        );
    }

    if violations.is_empty() {
        Ok(())
    } else {
        Err(format!("{} safety violation(s)", violations.len()))
    }
}
