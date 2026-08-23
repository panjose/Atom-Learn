# Security Policy

## Supported versions

| Version | Security support |
| --- | --- |
| Latest signed `0.14.x` release | Supported |
| Unreleased `main` / `v0.15` work | Best-effort pre-release fixes |
| Earlier releases | Unsupported; upgrade before reporting a version-specific issue |

Stable delivery is defined by the signed release manifest and capability ledger,
not by the presence of code on `main`.

## Report a vulnerability privately

Do not open a public issue for a suspected vulnerability.

After the repository is public, use GitHub's **Report a vulnerability** action
under the Security tab. Include only the information required to reproduce and
assess the issue:

- affected version, platform, Python version, and runtime profile;
- affected command, manifest, schema, or trust boundary;
- minimized reproduction steps and expected impact;
- whether a release key, token, private source, learner state, or signed artifact
  may be exposed;
- a safe contact method for coordinated follow-up.

Do not include live credentials, private keys, personal data, copyrighted source
material, learner answers, or unpublished research content. If private
vulnerability reporting is not yet enabled, contact the maintainer through the
[panjose GitHub profile](https://github.com/panjose) with a high-level request
for a private reporting channel and no exploit details.

The maintainer targets acknowledgement within 7 days and an initial severity
assessment within 14 days. Complex release-chain or migration issues may require
longer coordinated remediation. These are response targets, not guarantees.

## High-priority security boundaries

Reports are especially valuable for:

- release-signing key exposure, signature bypass, trust-bundle substitution, or
  rollback/recovery confusion;
- path traversal, archive extraction, symlink/reparse-point, atomic-write, or
  workspace-boundary escapes;
- credential forwarding to an untrusted host or persistence of a token;
- private learner source, Evidence, research note, exam material, or profile
  leakage;
- prompt-injection content escaping its role as untrusted retrieved evidence;
- unauthorized state mutation, Evidence insertion, skip/mastery confusion, or
  self-evolution outside its approval boundary;
- unsafe model loading, remote code execution, pickle-capable weights, or silent
  downloads;
- denial-of-service paths that bypass documented corpus, archive, or request
  bounds.

Model quality disagreements, unsupported product claims, and ordinary feature
requests should use the normal issue templates unless they expose one of the
security or privacy boundaries above.

## Disclosure and releases

Please allow time for a coordinated fix and signed release before public
disclosure. Security fixes must pass the normal release gates; they are never
published as unsigned emergency artifacts. If a signing key may be compromised,
the maintainer will follow the documented rotation or break-glass recovery
process rather than silently replacing trust material.
