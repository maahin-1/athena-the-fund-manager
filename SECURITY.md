# Security policy

## Supported versions

The project is pre-release (0.1.0). Only the current `main` branch receives security fixes. There are no released versions to backport to.

## Reporting a vulnerability

**Do not open a public issue, pull request or discussion for a vulnerability.**

Use GitHub's private vulnerability reporting: on the repository page open **Security**, then **Report a vulnerability**. (Direct link: `https://github.com/maahin-1/athena-the-fund-manager/security/advisories/new`.)

> **Precondition before this file is merged:** private vulnerability reporting must be switched on for the repository and checked by actually opening the form. If it is not available, replace this section with another channel the maintainer has verified. No email address is given here on purpose: none has been verified for this purpose.

Please include what you found, how to reproduce it with **synthetic data**, which version or commit, and the effect you expect it to have.

**Do not send** credentials, tokens, API keys, account or client numbers, broker or provider responses, files from a private data store, or a working exploit against a service you do not own. If you accidentally find a real credential, say where it is without repeating it.

## What to expect

The maintainer will acknowledge a report and say whether it is in scope. There is no service-level promise: this is a small project with one maintainer, and any response time stated here would be invented. Reporters are credited in the fix unless they ask not to be.

## Scope

In scope: code in this repository (the Python library, the Rust terminal server, the web page, the scripts), the access guard, and the CI configuration.
Out of scope: live trading losses, the correctness of any investment output (the software is research tooling and not advice), third-party services, and problems that need an attacker who already controls your machine or your account.

## Safe handling of secrets by the project

Credentials belong in the operator's own environment and are never read by tests or committed. Repository secret scanning and push protection are enabled once the maintainer has verified they are available (see the maintenance record). If a credential is ever committed it is treated as compromised and revoked first; removing it from history is a separate, deliberate step and is not done automatically.
