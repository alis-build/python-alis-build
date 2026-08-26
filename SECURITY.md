# Security policy

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately through
[GitHub's security advisories](https://github.com/alis-build/python-alis-build/security/advisories/new),
which lets us discuss and fix the problem before it is disclosed.

Include what you have: the affected package and version, what an attacker can
do, and a reproduction if you have one. A clear description of the class of
problem is more useful than a working exploit.

## Supported versions

These packages are pre-1.0 and only the latest release of each is supported. If
you are pinned to an older version, upgrading is the fix.

## Scope

`alis-build-iam` decides who a caller is and what they may do, so anything that
lets a caller be mistaken for someone else, or reach something they should not,
is in scope. Some examples:

- A token accepted despite an invalid signature, a wrong audience, or an expiry
  in the past.
- An identity that decodes differently from how the service that sent it
  encoded it.
- A policy binding matching an identity it does not name.
- A role or permission check returning true where it should return false.
- An identity leaking between concurrent requests, or between requests sharing a
  server thread.

## Not vulnerabilities

Some behaviours look alarming but are deliberate and documented. Reporting them
is welcome as an issue rather than an advisory:

- **`from_unverified_jwt` verifies nothing.** No signature, no expiry, and the
  identity type is derived from an unverified claim. It is a parser. Use
  `AuthnClient.verified_identity` when the answer needs to be trusted.
- **Open roles grant to everyone.** An open role is an allow-list of role names,
  not a grant to a particular caller; a check for one succeeds for any caller at
  all.
- **System and admin identities bypass authorization** entirely. That is what
  they are for.
- **Conditional policy bindings are not evaluated.** By default they grant and
  log a warning naming the condition, because the condition needs the resource
  being accessed and a check is never given it. Set `ConditionMode.DENY` if your
  deployment needs them refused.
- **`verify_signatures=False` leaves the expiry as the only check.** It exists
  for tokens a service issued and stored itself, never for tokens a caller
  supplies.
