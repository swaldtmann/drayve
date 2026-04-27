# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | Yes       |

## Reporting a Vulnerability

**Please do NOT create public issues for security vulnerabilities.**

Report vulnerabilities via email to: **security@waldtmann.de**

What we need:
- Description of the vulnerability
- Steps to reproduce
- Potential impact

What we promise:
- Acknowledgment within 48 hours
- Assessment within 7 days
- Fix or workaround as soon as possible
- Credit in the release notes (if desired)

## Scope

- Ansible roles (common, docker, traefik, auth, monitoring, secrets, deploy, backup)
- Secret handling (quickstart + SOPS modes)
- SSH hardening, firewall configuration
- Docker Compose templates
- CrowdSec integration

Out of scope: Upstream dependencies (Ansible, Docker, Traefik, etc.).
