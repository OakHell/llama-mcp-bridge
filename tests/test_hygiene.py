import pytest
from llama_mcp_bridge.hygiene import (
    InputSafetyGuard,
    BlockProtector,
    StructuralContentDetector,
    BoundaryDetector,
    SanitizationAuditRecord,
    clean_mcp_output,
)


def test_input_safety_guard_passthrough():
    assert InputSafetyGuard.is_passthrough(None) == (True, "")
    assert InputSafetyGuard.is_passthrough(123) == (True, 123)
    assert InputSafetyGuard.is_passthrough("") == (True, "")
    assert InputSafetyGuard.is_passthrough("   ") == (True, "   ")
    assert InputSafetyGuard.is_passthrough("error: something went wrong") == (
        True,
        "error: something went wrong",
    )
    assert InputSafetyGuard.is_passthrough("500 internal server error") == (
        True,
        "500 internal server error",
    )
    assert InputSafetyGuard.is_passthrough('{"error": "Invalid request"}') == (
        True,
        '{"error": "Invalid request"}',
    )
    assert InputSafetyGuard.is_passthrough("This is a valid response") == (
        False,
        "This is a valid response",
    )


def test_block_protector_mask_and_unmask():
    protector = BlockProtector()
    assert protector.mask("This is a test") == "This is a test"

    code = "```python\ndef foo():\n    pass\n```"
    masked_code = protector.mask(code)
    assert protector.unmask(masked_code) == code

    math_block = "$$x^2 + y^2 = z^2$$"
    masked_math = protector.mask(math_block)
    assert protector.unmask(masked_math) == math_block


def test_structural_content_detector_analyze():
    res_empty = StructuralContentDetector.analyze("")
    assert res_empty["is_structured"] is False

    res_list = StructuralContentDetector.analyze("1. Item 1\n2. Item 2")
    assert res_list["is_structured"] is True
    assert res_list["list_count"] == 2

    res_header = StructuralContentDetector.analyze("# Header\nThis is a header")
    assert res_header["is_structured"] is True
    assert res_header["header_count"] == 1


def test_boundary_detector_explicit_tags():
    cleaned, removed = BoundaryDetector.strip_explicit_tags(
        "<think>This is a thought</think>Actual answer"
    )
    assert cleaned == "Actual answer"
    assert removed > 0

    cleaned2, removed2 = BoundaryDetector.strip_explicit_tags(
        "[THINK]This is a thought[/THINK]Actual answer"
    )
    assert cleaned2 == "Actual answer"
    assert removed2 > 0

    cleaned3, removed3 = BoundaryDetector.strip_explicit_tags("Just text")
    assert cleaned3 == "Just text"
    assert removed3 == 0


def test_boundary_detector_unclosed_tags():
    cleaned, removed = BoundaryDetector.handle_unclosed_tags(
        "<think>\nLet me reason step by step.\nThis is a thought"
    )
    assert removed >= 0
    assert isinstance(cleaned, str)


def test_boundary_detector_keyword_markers():
    extracted, removed = BoundaryDetector.extract_from_keyword_markers(
        "Let me think about this carefully.\n**Final Answer:** This is a final answer"
    )
    assert extracted is None or "final answer" in extracted.lower()
    assert removed >= 0

    extracted_none, removed_none = BoundaryDetector.extract_from_keyword_markers(
        "Just a normal paragraph without markers."
    )
    assert extracted_none is None
    assert removed_none == 0


def test_sanitization_audit_record():
    audit = SanitizationAuditRecord(1000, 500, 500, True)
    assert audit.removal_ratio == 0.5
    assert audit.should_append_notice is True

    audit_small = SanitizationAuditRecord(1000, 950, 50, True)
    assert audit_small.should_append_notice is False


def test_clean_mcp_output():
    assert clean_mcp_output(None) == ""
    assert clean_mcp_output("") == ""
    assert clean_mcp_output("error: something went wrong") == "error: something went wrong"
    assert clean_mcp_output('{"error": "Invalid request"}') == '{"error": "Invalid request"}'
    assert clean_mcp_output("This is a valid response") == "This is a valid response"
    assert clean_mcp_output("<think>reasoning</think>Final result") == "Final result"
    assert (
        clean_mcp_output("```python\n<think>keep me</think>\n```")
        == "```python\n<think>keep me</think>\n```"
    )
    assert clean_mcp_output("$$x^2 + y^2 = z^2$$") == "$$x^2 + y^2 = z^2$$"
