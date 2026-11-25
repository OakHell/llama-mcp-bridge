"""
Robust Hygiene and Reasoning-Trace Extraction Engine
----------------------------------------------------
Engineered for local AI bridges and Model Context Protocol (MCP) tools.
Handles tagless monologues, keyword markers, structural dividers, and XML tags
without corrupting nested code blocks, LaTeX equations, or raw indentation.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


# =====================================================================
# 1. Input Normalisation & Safety Guards
# =====================================================================

class InputSafetyGuard:
    """Detects server/transport errors, null states, and preserves exact non-text types."""

    ERROR_PREFIXES = (
        "error:", "error ", "traceback (most recent call last):",
        "exception:", "httperror", "connectionrefused", "connection error",
        "500 internal server error", "502 bad gateway", "503 service unavailable",
        "504 gateway timeout", "404 not found", "401 unauthorized", "403 forbidden"
    )

    @classmethod
    def is_passthrough(cls, raw_output: Any) -> Tuple[bool, Any]:
        if raw_output is None:
            return True, ""
        if not isinstance(raw_output, str):
            return True, raw_output
        trimmed = raw_output.strip()
        if not trimmed:
            return True, raw_output

        lower = trimmed.lower()
        if any(lower.startswith(prefix) for prefix in cls.ERROR_PREFIXES):
            return True, raw_output

        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                parsed = json.loads(trimmed)
                if isinstance(parsed, dict) and ("error" in parsed or "errors" in parsed):
                    return True, raw_output
            except Exception:
                pass

        return False, raw_output


# =====================================================================
# 2. Protected Block Vault (Code Fences & LaTeX Equations)
# =====================================================================

class BlockProtector:
    """
    Masks code blocks (``` and ~~~), display math ($$ and \\[), and inline code (`)
    so pattern matching never alters user payloads or code-embedded keywords.
    """

    CODE_FENCE_RE = re.compile(r'(?m)^[ \t]{0,3}(`{3,}|~{3,}).*?^[ \t]{0,3}\1[ \t]*$', re.DOTALL)
    DISPLAY_MATH_RE = re.compile(r'(\$\$.*?\$\$|\\\[.*?\\\])', re.DOTALL)
    INLINE_CODE_RE = re.compile(r'(`[^`\n]+`)')

    def __init__(self):
        self.vault: Dict[str, str] = {}
        self._counter = 0

    def mask(self, text: str) -> str:
        self.vault.clear()
        self._counter = 0

        def _stash(match: re.Match) -> str:
            token = f"@@@MCP_PROTECTED_BLOCK_{self._counter}@@@"
            self.vault[token] = match.group(0)
            self._counter += 1
            return token

        # Mask fenced blocks first, then math, then inline backticks
        masked = self.CODE_FENCE_RE.sub(_stash, text)
        masked = self.DISPLAY_MATH_RE.sub(_stash, masked)
        masked = self.INLINE_CODE_RE.sub(_stash, masked)
        return masked

    def unmask(self, text: str) -> str:
        result = text
        for token, original in reversed(list(self.vault.items())):
            result = result.replace(token, original)
        return result


# =====================================================================
# 3. Structural Content & Layout Analysis
# =====================================================================

class StructuralContentDetector:
    """
    Inspects text blocks to determine whether they represent structured user-facing
    payloads (proofs, lists, documentation) or unformatted conversational scratchpads.
    """

    LIST_ITEM_RE = re.compile(r'(?m)^[ \t]*(?:\d+[\.\)]|[-*+])\s+')
    HEADER_RE = re.compile(r'(?m)^[ \t]*#{1,6}\s+')
    TABLE_RE = re.compile(r'(?m)^[ \t]*\|.*\|[ \t]*$')
    STEP_LABEL_RE = re.compile(r'(?i)\b(?:step|stage|phase)\s+\d+[:\-]')

    @classmethod
    def analyze(cls, text: str) -> Dict[str, Any]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return {"is_structured": False, "structural_ratio": 0.0, "elements": 0}

        list_count = len(cls.LIST_ITEM_RE.findall(text))
        header_count = len(cls.HEADER_RE.findall(text))
        table_count = len(cls.TABLE_RE.findall(text))
        step_label_count = len(cls.STEP_LABEL_RE.findall(text))

        total_structural_elements = list_count + header_count + table_count + step_label_count
        structural_ratio = total_structural_elements / max(len(lines), 1)

        # High structural integrity: Contains multiple numbered/bulleted steps,
        # markdown headers, tables, or labeled steps.
        is_structured = (
            list_count >= 2
            or header_count >= 1
            or table_count >= 2
            or step_label_count >= 2
            or structural_ratio >= 0.25
        )

        return {
            "is_structured": is_structured,
            "list_count": list_count,
            "header_count": header_count,
            "table_count": table_count,
            "structural_ratio": structural_ratio,
        }


# =====================================================================
# 4. Deterministic Boundary & Reasoning Trimming
# =====================================================================

class BoundaryDetector:
    """
    Handles closed/unclosed XML tags, reasoning sections without tags,
    keyword markers, and markdown horizontal rules separating thoughts.
    """

    TAG_NAMES = r"think|reasoning|thought|reflection|internal_monologue|scratchpad"

    CLOSED_TAG_RE = re.compile(
        rf"<({TAG_NAMES})>.*?</\1>\s*",
        re.DOTALL | re.IGNORECASE
    )

    BRACKET_TAG_NAMES = r"THINK|THOUGHT|REASONING|INTERNAL"

    BRACKETED_TAG_RE = re.compile(
        rf"\[({BRACKET_TAG_NAMES})\](.*?)\[/\1\]\s*",
        re.DOTALL | re.IGNORECASE
    )

    # 1. Strong markers unambiguously designate macro answer boundaries
    STRONG_PAYLOAD_MARKERS = [
        r"(?:^|\n)\s*(?:[-*_]{3,}\s*\n)?\s*(?:#{1,6}\s*)?(?:\*\*|__)?(?:Final Answer|Final Result|Final Payload)(?:\*\*|__)?\s*[:\-]?\s*",
    ]
    STRONG_MARKER_RE = re.compile("|".join(STRONG_PAYLOAD_MARKERS), re.IGNORECASE)

    # 2. Weak markers are frequently found in normal explanations, summaries, and proofs
    WEAK_PAYLOAD_MARKERS = [
        r"(?:^|\n)\s*(?:[-*_]{3,}\s*\n)?\s*(?:#{1,6}\s*)?(?:\*\*|__)?(?:Answer|Result|Solution|Conclusion)(?:\*\*|__)?\s*[:\-]\s*",
        r"(?:^|\n)\s*(?:#{1,6}\s*)(?:Answer|Result|Solution|Conclusion)\b\s*",
    ]
    WEAK_MARKER_RE = re.compile("|".join(WEAK_PAYLOAD_MARKERS), re.IGNORECASE)

    # Explicit container headers emitted by CoT models without XML tags
    MONOLOGUE_HEADER_RE = re.compile(
        r"^(?:#{1,6}\s*)?(?:\*\*|__)?(?:Thinking Process|Thought Process|Thought|Reasoning|Internal Monologue|Scratchpad)(?:\*\*|__)?\s*[:\-]?\s*",
        re.IGNORECASE
    )

    # Conversational first-person deliberation cues
    MONOLOGUE_CUES = re.compile(
        r"\b(?:let(?:\'s| me)|i (?:need|should|must|will|can|have|am|thought|think)|"
        r"the user (?:is asking|wants|needs)|hmm+|wait|okay|looking at the|"
        r"let me double check|let me verify|actually, that won't work|wait, no)\b",
        re.IGNORECASE
    )

    @classmethod
    def strip_explicit_tags(cls, text: str) -> Tuple[str, int]:
        initial_len = len(text)
        cleaned = cls.CLOSED_TAG_RE.sub("", text)
        cleaned = cls.BRACKETED_TAG_RE.sub("", cleaned)
        chars_removed = initial_len - len(cleaned)
        return cleaned.strip(), chars_removed

    @classmethod
    def handle_unclosed_tags(cls, text: str) -> Tuple[str, int]:
        """
        Handles streaming or truncated generations where <think> opened
        at the root, but the closing tag was never generated.
        """
        open_tag_match = re.search(rf"(?:<({cls.TAG_NAMES})>|\[({cls.BRACKET_TAG_NAMES})\])", text, re.IGNORECASE)
        if not open_tag_match:
            return text, 0

        start_idx = open_tag_match.start()
        if start_idx > 80:
            return text, 0

        initial_len = len(text)
        content_after = text[open_tag_match.end():]

        # Check for transitions inside the unclosed block
        for marker_re in (cls.STRONG_MARKER_RE, cls.WEAK_MARKER_RE):
            marker_match = marker_re.search(content_after)
            if marker_match:
                payload = content_after[marker_match.end():].strip()
                return payload, initial_len - len(payload)

        # Check for horizontal rule transition
        hr_split = re.split(r"(?m)^[ \t]{0,3}(?:[-*_]{3,})[ \t]*$", content_after)
        if len(hr_split) > 1 and len(hr_split[-1].strip()) > 0:
            payload = hr_split[-1].strip()
            return payload, initial_len - len(payload)

        # If unclosed, take only the tail end (last 400 chars) where code/conclusions usually live,
        # avoiding a massive raw dump while still giving visibility.
        trimmed_content = content_after.strip()
        if len(trimmed_content) > 0:
            tail_snippet = trimmed_content[-400:] if len(trimmed_content) > 400 else trimmed_content
            partial_rescue = (
                f"> *[System Notice: Model token budget exhausted inside unclosed thought block. Showing tail-end rescue]*\n\n"
                f"...\n{tail_snippet}"
            )
            return partial_rescue, 0

        # Output was entirely within an unclosed thinking tag with no payload
        return "", initial_len

    @classmethod
    def extract_from_keyword_markers(cls, text: str) -> Tuple[Optional[str], int]:
        """
        Extracts payload across keyword markers with structural protection.
        Guarantees structured breakdowns, steps, and lists are NEVER dropped.
        """
        # Pass 1: Check strong markers (e.g., "Final Answer:")
        strong_matches = list(cls.STRONG_MARKER_RE.finditer(text))
        if strong_matches:
            last_match = strong_matches[-1]
            preamble = text[:last_match.start()].strip()
            payload = text[last_match.end():].strip()

            has_explicit_header = bool(cls.MONOLOGUE_HEADER_RE.search(preamble))
            structure = StructuralContentDetector.analyze(preamble)

            # If preamble starts with "Thinking Process:", it was explicitly labeled scratchpad
            if has_explicit_header:
                return payload, len(preamble)

            # If preamble contains structured lists or math/steps, DO NOT drop it
            if structure["is_structured"]:
                return None, 0

            # Only strip preamble if validated as conversational self-talk
            cue_count = len(cls.MONOLOGUE_CUES.findall(preamble))
            if cue_count >= 2:
                return payload, len(preamble)

        # Pass 2: Check weak markers (e.g., "Answer:", "Conclusion:")
        weak_matches = list(cls.WEAK_MARKER_RE.finditer(text))
        if weak_matches:
            last_match = weak_matches[-1]
            preamble = text[:last_match.start()].strip()
            payload = text[last_match.end():].strip()

            has_explicit_header = bool(cls.MONOLOGUE_HEADER_RE.search(preamble))
            structure = StructuralContentDetector.analyze(preamble)

            # Weak markers ONLY strip preamble if an explicit monologue header is present
            if has_explicit_header and not structure["is_structured"]:
                return payload, len(preamble)

        return None, 0

    @classmethod
    def extract_from_thematic_break(cls, text: str) -> Tuple[Optional[str], int]:
        """
        Splits by '---' only when the preamble is definitively validated as a monologue,
        preserving multi-section Markdown documents.
        """
        parts = re.split(r"(?m)^[ \t]{0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$", text)
        if len(parts) <= 1:
            return None, 0

        preamble = parts[0].strip()
        payload = "---".join(parts[1:]).strip()

        structure = StructuralContentDetector.analyze(preamble)
        if structure["is_structured"]:
            return None, 0

        has_header = bool(cls.MONOLOGUE_HEADER_RE.search(preamble))
        cue_matches = len(cls.MONOLOGUE_CUES.findall(preamble))

        if has_header or cue_matches >= 3:
            return payload, len(preamble)

        return None, 0


# =====================================================================
# 5. Sanitisation Auditing & Metrics
# =====================================================================

@dataclass
class SanitizationAuditRecord:
    original_len: int
    final_len: int
    scrubbed_reasoning_chars: int
    reasoning_detected: bool

    @property
    def removal_ratio(self) -> float:
        return (self.scrubbed_reasoning_chars / self.original_len) if self.original_len > 0 else 0.0

    @property
    def should_append_notice(self) -> bool:
        # Notice fires only if actual reasoning was detected,
        # dropped at least 300 characters, and reduced payload by at least 50%
        return (
            self.reasoning_detected
            and self.scrubbed_reasoning_chars >= 300
            and self.removal_ratio >= 0.50
        )


# =====================================================================
# 6. Main Hygiene Engine Entry Point
# =====================================================================

def clean_mcp_output(
    raw_output: Any,
    include_audit_notice: bool = True
) -> str:
    """
    Primary hygiene engine function for local AI model outputs in MCP.

    Pipeline:
      1. Normalisation & Safety Guard (errors/empty pass-through)
      2. Code & Math Block Masking (zero-loss guarantee)
      3. Deterministic Extraction (XML tags, markers, horizontal rules)
      4. Structural Content Preservation
      5. Block Unmasking & Cumulative Auditing Notice
    """
    is_safe, safe_output = InputSafetyGuard.is_passthrough(raw_output)
    if is_safe:
        return safe_output

    original_len = len(str(raw_output))
    total_reasoning_chars_scrubbed = 0
    reasoning_detected = False

    # Step 1: Mask protected elements (code fences, display math, inline code)
    protector = BlockProtector()
    masked = protector.mask(raw_output)

    # Step 2: Strip explicit XML/bracketed tags
    cleaned, tag_drop = BoundaryDetector.strip_explicit_tags(masked)
    if tag_drop > 0:
        total_reasoning_chars_scrubbed += tag_drop
        reasoning_detected = True

    # Step 3: Handle unclosed streaming tags
    cleaned, unclosed_drop = BoundaryDetector.handle_unclosed_tags(cleaned)
    if unclosed_drop > 0:
        total_reasoning_chars_scrubbed += unclosed_drop
        reasoning_detected = True

    # Step 4: Extract from keyword markers (with structural protection)
    marker_payload, marker_drop = BoundaryDetector.extract_from_keyword_markers(cleaned)
    if marker_payload is not None:
        cleaned = marker_payload
        total_reasoning_chars_scrubbed += marker_drop
        reasoning_detected = True

    # Step 5: Extract across validated thematic breaks (---)
    hr_payload, hr_drop = BoundaryDetector.extract_from_thematic_break(cleaned)
    if hr_payload is not None:
        cleaned = hr_payload
        total_reasoning_chars_scrubbed += hr_drop
        reasoning_detected = True

    # Clean residual monologue headers if present at the top
    pre_header_len = len(cleaned)
    cleaned = BoundaryDetector.MONOLOGUE_HEADER_RE.sub("", cleaned).strip()
    header_drop = pre_header_len - len(cleaned)
    if header_drop > 0:
        total_reasoning_chars_scrubbed += header_drop
        reasoning_detected = True

    # Step 6: Restore code blocks and math formulas
    final_payload = protector.unmask(cleaned).strip()
    final_len = len(final_payload)

    # If payload is empty (e.g. cut off mid-thought), return empty without notices
    if not final_payload:
        return ""

    # Step 7: Audit calculation and optional system notice
    audit = SanitizationAuditRecord(
        original_len=original_len,
        final_len=final_len,
        scrubbed_reasoning_chars=total_reasoning_chars_scrubbed,
        reasoning_detected=reasoning_detected,
    )

    if audit.should_append_notice:
        return (
            f"{final_payload}\n\n"
            f"[System Notice: {audit.scrubbed_reasoning_chars} chars of internal "
            f"reasoning stripped ({audit.removal_ratio:.0%}).]"
        )

    return final_payload
