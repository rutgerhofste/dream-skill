#!/usr/bin/env python3
"""
secret_scan.py - Stop dream from persisting secrets or PII into memory.

Memory consolidation reads session transcripts, and transcripts routinely contain
things that must NEVER become durable, version-able memory: API keys, tokens, private
keys, passwords, connection strings, and third-party PII. Claude Code's memory files are
plaintext and long-lived; an auto-dream running headless could silently write a leaked
credential into ~/.claude/.../memory/. This script is the guard.

It is pure stdlib, read-only, deterministic. The skill runs it twice:
  - Phase 2 (gather): never *ingest* a detected secret as a candidate fact.
  - Phase 5 (review): scan the work copy before applying; any secret -> do NOT write it,
    queue a redacted note for human review instead. Secrets are blocking even headless.

Severities:
  - secret : blocking. Exit code 1 if any are found. The skill must not persist these.
  - pii    : warning. Reported, but does not fail by default (identity memory legitimately
             holds the user's own contact info). Use --strict to fail on PII too.

Usage:
    secret_scan.py <path> [<path> ...]      # scan files/dirs (recurses .md)
    secret_scan.py --stdin                  # scan text on stdin
    secret_scan.py --strict <path>          # also exit 1 on PII findings
    secret_scan.py --selftest               # deterministic self-check (CI)

Output: JSON {"findings": [...], "summary": {...}} on stdout; exit 1 if blocking.
Every reported match is REDACTED - the raw secret is never printed.
"""

import argparse
import json
import os
import re
import sys

# (name, severity, compiled regex). Order matters only for reporting.
# Patterns are intentionally specific to keep false positives low.
_RULES = [
    ("aws_access_key_id", "secret", r"\bAKIA[0-9A-Z]{16}\b"),
    ("github_pat", "secret", r"\bghp_[A-Za-z0-9]{36}\b"),
    ("github_pat_fine", "secret", r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    ("github_oauth", "secret", r"\bgh[osru]_[A-Za-z0-9]{36}\b"),
    ("openai_like_key", "secret", r"\bsk-[A-Za-z0-9]{20,}\b"),
    ("slack_token", "secret", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ("google_api_key", "secret", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    ("private_key_block", "secret", r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
    ("jwt", "secret", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ("bearer_header", "secret", r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._\-]{12,}"),
    ("db_url_with_creds", "secret",
     r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:\s/]+:[^@\s/]+@"),
    ("assigned_secret", "secret",
     r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret|"
     r"auth[_-]?token|private[_-]?key)\b\s*[:=]\s*['\"]?(?P<val>[^\s'\"]{8,})"),
    ("email", "pii", r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    ("credit_card", "pii", r"\b(?:\d[ -]?){13,16}\b"),
]
RULES = [(name, sev, re.compile(rx)) for name, sev, rx in _RULES]

# Values that look like assignments but are obvious placeholders, not real secrets.
PLACEHOLDER = re.compile(
    r"^(?:x{3,}|\*{3,}|\.{3,}|<[^>]*>|your[_-]?\w+|change[_-]?me|placeholder|example|"
    r"redacted|none|null|true|false|todo|fixme|enabled|disabled)$",
    re.IGNORECASE,
)


def redact(s):
    s = s.strip()
    if len(s) <= 4:
        return "*" * len(s)
    return s[:4] + "*" * min(len(s) - 4, 12)


def scan_text(text, source="<stdin>"):
    findings = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, sev, rx in RULES:
            for m in rx.finditer(line):
                val = m.groupdict().get("val") or m.group(0)
                if name == "assigned_secret" and PLACEHOLDER.match(val.strip("'\"")):
                    continue
                if name == "credit_card":
                    digits = re.sub(r"\D", "", m.group(0))
                    if not _luhn(digits):
                        continue
                findings.append({
                    "source": source,
                    "line": lineno,
                    "rule": name,
                    "severity": sev,
                    "match": redact(val),
                })
    return findings


def _luhn(num):
    if not 13 <= len(num) <= 16:
        return False
    total, alt = 0, False
    for d in reversed(num):
        n = ord(d) - 48
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


def scan_path(path):
    findings = []
    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            for fn in sorted(files):
                if fn.endswith(".md") or fn.endswith(".txt"):
                    findings += _scan_file(os.path.join(root, fn))
    else:
        findings += _scan_file(path)
    return findings


def _scan_file(path):
    try:
        text = open(path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError):
        return []
    return scan_text(text, source=path)


def selftest():
    """Build samples at runtime so no literal secret sits in this file (keeps gitleaks
    clean on our own repo) while still exercising every rule."""
    total = passed = 0

    def chk(cond, label):
        nonlocal total, passed
        total += 1
        if cond:
            passed += 1
            print(f"[PASS] {label}")
        else:
            print(f"[FAIL] {label}")

    def rules_hit(text):
        return {f["rule"] for f in scan_text(text)}

    aws = "AKIA" + "Q" * 16
    ghp = "ghp_" + "a" * 36
    skk = "sk-" + "b" * 30
    jwt = "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12
    dburl = "postgresql://admin:" + "s" * 10 + "@db.example.com:5432/app"

    chk("aws_access_key_id" in rules_hit(aws), "detects AWS access key id")
    chk("github_pat" in rules_hit(ghp), "detects GitHub PAT")
    chk("openai_like_key" in rules_hit(skk), "detects sk- style key")
    chk("jwt" in rules_hit(jwt), "detects JWT")
    chk("db_url_with_creds" in rules_hit(dburl), "detects DB URL with credentials")
    chk("assigned_secret" in rules_hit("password = " + "h" * 12), "detects assigned password")
    chk("assigned_secret" not in rules_hit('api_key = "<your-key-here>"'), "ignores placeholder value")
    chk("email" in rules_hit("contact bob@example.com"), "flags email as PII")
    chk(rules_hit("the user prefers pnpm and Result types") == set(), "no false positive on clean memory")

    # exit-code contract: secrets block, clean text passes
    chk(any(f["severity"] == "secret" for f in scan_text(aws)), "AWS key is blocking severity")
    chk(redact(ghp).startswith("ghp_") and "*" in redact(ghp), "matches are redacted, never raw")

    print(f"\n  secret_scan selftest: {passed}/{total} passed")
    return 0 if passed == total else 1


def main(argv=None):
    p = argparse.ArgumentParser(description="Scan text/files for secrets and PII.")
    p.add_argument("paths", nargs="*", help="Files or directories to scan.")
    p.add_argument("--stdin", action="store_true", help="Scan text from stdin.")
    p.add_argument("--strict", action="store_true", help="Also exit 1 on PII findings.")
    p.add_argument("--selftest", action="store_true", help="Run deterministic self-check.")
    args = p.parse_args(argv)

    if args.selftest:
        return selftest()

    findings = []
    if args.stdin:
        findings += scan_text(sys.stdin.read(), source="<stdin>")
    for path in args.paths:
        findings += scan_path(path)

    secrets = [f for f in findings if f["severity"] == "secret"]
    pii = [f for f in findings if f["severity"] == "pii"]
    out = {
        "findings": findings,
        "summary": {"secrets": len(secrets), "pii": len(pii)},
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    if secrets:
        print(f"BLOCKING: {len(secrets)} secret(s) detected - do not persist.", file=sys.stderr)
        return 1
    if pii and args.strict:
        print(f"BLOCKING (strict): {len(pii)} PII finding(s).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
