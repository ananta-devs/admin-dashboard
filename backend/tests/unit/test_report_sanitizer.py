"""Unit tests for the multi-layer reporting SVG, XML, and text sanitizer."""

import pytest

from app.core.exceptions import ValidationError
from app.services.report_sanitizer import ReportSanitizerService


def test_sanitize_svg_allowed_elements():
    raw = (
        '<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="10" y="10" width="80" height="80" fill="#3b82f6"/>'
        '<circle cx="50" cy="50" r="30" stroke="#10b981"/>'
        '<text x="50" y="50">Hello</text>'
        "</svg>"
    )
    clean = ReportSanitizerService.sanitize_svg(raw)
    assert "<svg" in clean
    assert "<rect" in clean
    assert "<circle" in clean
    assert "<text" in clean
    assert "Hello" in clean


def test_sanitize_svg_strips_script_and_dangerous_tags():
    raw = (
        '<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">'
        '<script>alert("xss")</script>'
        '<foreignObject width="100" height="100"><iframe src="http://evil.com"></iframe></foreignObject>'
        '<a href="http://evil.com"><text>Click</text></a>'
        '<rect width="50" height="50"/>'
        "</svg>"
    )
    clean = ReportSanitizerService.sanitize_svg(raw)
    assert "<script" not in clean
    assert "alert" not in clean
    assert "<foreignObject" not in clean
    assert "<iframe" not in clean
    assert "<a" not in clean
    assert "<rect" in clean


def test_sanitize_svg_strips_style_tags():
    raw = (
        '<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">'
        '<style>@import "http://evil.com/leak.css"; rect { fill: red; }</style>'
        '<rect width="50" height="50" fill="blue"/>'
        "</svg>"
    )
    clean = ReportSanitizerService.sanitize_svg(raw)
    assert "<style" not in clean
    assert "@import" not in clean
    assert "<rect" in clean
    assert 'fill="blue"' in clean


def test_sanitize_svg_strips_event_handlers_and_href():
    raw = (
        '<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<rect width="50" height="50" onclick="alert(1)" onload="fetch(\'/steal\')" xlink:href="javascript:alert(2)"/>'
        "</svg>"
    )
    clean = ReportSanitizerService.sanitize_svg(raw)
    assert "onclick" not in clean
    assert "onload" not in clean
    assert "javascript" not in clean
    assert "alert" not in clean
    assert "<rect" in clean


def test_sanitize_svg_valid_local_defs_fragment():
    raw = (
        '<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">'
        "<defs>"
        '<linearGradient id="grad1"><stop offset="0%" stop-color="#fff"/></linearGradient>'
        "</defs>"
        '<rect width="50" height="50" fill="url(#grad1)"/>'
        "</svg>"
    )
    clean = ReportSanitizerService.sanitize_svg(raw)
    assert 'fill="url(#grad1)"' in clean
    assert 'id="grad1"' in clean


def test_sanitize_svg_strips_external_or_unregistered_url_fragment():
    raw = (
        '<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">'
        "<defs>"
        '<linearGradient id="grad1"><stop offset="0%" stop-color="#fff"/></linearGradient>'
        "</defs>"
        '<rect width="50" height="50" fill="url(http://evil.com/grad.svg#grad1)" stroke="url(#nonexistent)"/>'
        "</svg>"
    )
    clean = ReportSanitizerService.sanitize_svg(raw)
    assert "evil.com" not in clean
    assert "url(#nonexistent)" not in clean
    assert 'fill="url(http' not in clean


def test_sanitize_svg_rejects_oversized_payload():
    large_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg">' + ("<g>" * 100) + ("a" * (600 * 1024)) + "</svg>"
    )
    with pytest.raises(ValidationError, match="exceeds limit"):
        ReportSanitizerService.sanitize_svg(large_svg)


def test_sanitize_svg_rejects_xxe_entity_injection():
    xxe = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        '<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">'
        "<text>&xxe;</text>"
        "</svg>"
    )
    with pytest.raises(
        ValidationError, match="DTDForbidden|EntitiesForbidden|Malformed or unsafe SVG"
    ):
        ReportSanitizerService.sanitize_svg(xxe)


def test_sanitize_table_cell():
    assert ReportSanitizerService.sanitize_table_cell(None) == "—"
    assert ReportSanitizerService.sanitize_table_cell(True) == "True"
    assert ReportSanitizerService.sanitize_table_cell(False) == "False"
    assert (
        ReportSanitizerService.sanitize_table_cell("<script>alert(1)</script>")
        == "&lt;script&gt;alert(1)&lt;/script&gt;"
    )

    long_str = "A" * 1500
    truncated = ReportSanitizerService.sanitize_table_cell(long_str)
    assert len(truncated) == 1001  # 1000 + 1 ellipsis
    assert truncated.endswith("…")


def test_escape_text():
    assert ReportSanitizerService.escape_text(None) == ""
    assert (
        ReportSanitizerService.escape_text("<b>Title</b> & Co")
        == "&lt;b&gt;Title&lt;/b&gt; &amp; Co"
    )
