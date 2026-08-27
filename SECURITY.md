<p align="right"><strong>English</strong> · <a href="SECURITY_ZH.md">中文</a></p>

# Security

## Reporting a vulnerability

Please do not open a public issue for vulnerabilities that could expose reviewer identities, invite codes, annotation content, or authentication material. Contact the repository owner privately through the address listed on their GitHub profile.

## Deployment notes

- Keep databases, audit logs, exports, invite codes and session secrets outside the repository.
- Put shared deployments behind HTTPS and set `ANNOTATION_COOKIE_SECURE=1`.
- Use a random session secret with at least 32 bytes of entropy.
- Back up the SQLite database together with its WAL state, or use the built-in frozen export flow.
- Review images and model output for personal or confidential information before publishing screenshots.
