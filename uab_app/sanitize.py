"""User/document text sanitization and prompt-injection heuristics."""

from __future__ import annotations

import re

# (stable_id, human_label, compiled pattern — labels for UI; IDs for logic)
INJECTION_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("override_prior_instructions", 'instruction override ("ignore previous…")', re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior)\s+instructions")),
    ("disregard_system", 'disregard system / prior context', re.compile(r"(?i)disregard\s+(the\s+)?(above|system)")),
    ("fake_system_role", 'suspicious system-role delimiter', re.compile(r"(?i)system\s*:\s*")),
    ("instruction_inst_tag", "instruction-style [INST] tag", re.compile(r"(?i)\[INST\]")),
    ("roleplay_override", 'role override ("you are now…")', re.compile(r"(?i)you\s+are\s+now\s+(a|an|the)\s+")),
    ("safety_override", "override safety or rules", re.compile(r"(?i)override\s+(safety|rules|instructions)")),
    ("developer_mode", "developer mode phrase", re.compile(r"(?i)developer\s+mode")),
    ("template_injection_delimiters", "template-style <%…%> delimiters", re.compile(r"(?i)<%.*?%>")),
]


def strip_control_chars(s: str) -> str:
    """Normalize whitespace: preserve newlines/tabs, normalize \\r to \\n, strip other control chars."""
    return "".join(
        "\n" if ch == "\r" else ch
        for ch in s
        if ch == "\n" or ch == "\t" or ch == "\r" or ord(ch) >= 32
    )


def detect_prompt_injection(text: str, source: str = "user") -> list[str]:
    flags: list[str] = []
    for rule_id, _label, pat in INJECTION_RULES:
        if pat.search(text):
            flags.append(rule_id)
    # Uploaded documents can include OCR/artifact strings like "system:"
    # that are not intentional prompt attacks. Keep user-typed text strict.
    if source == "document":
        relaxed_allow_false_positives = {
            "fake_system_role",
            "instruction_inst_tag",
            "template_injection_delimiters",
        }
        flags = [f for f in flags if f not in relaxed_allow_false_positives]
    return flags


def injection_labels_for_ids(rule_ids: list[str]) -> list[str]:
    id_to_label = {rid: lab for rid, lab, _ in INJECTION_RULES}
    return [id_to_label.get(r, r) for r in rule_ids]


def sanitize_input(text: str, source: str = "user") -> tuple[str, list[str]]:
    cleaned = strip_control_chars(text)
    flags = detect_prompt_injection(cleaned, source=source)
    return cleaned, flags


def regex_cleanup_fallback(text: str) -> str:
    t = strip_control_chars(text)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"(?i)\b(MRN|DOB|SSN)\s*[:#]?\s*[\w\-./]+", "[REDACTED]", t)
    return t.strip()
