"""Multi-layer SVG, XML, and HTML security sanitizer for VIBE reporting."""

from __future__ import annotations

import html
import re
from typing import Any
import xml.etree.ElementTree as standard_ET

import defusedxml.ElementTree as defused_ET
from defusedxml.common import DefusedXmlException

from app.core.exceptions import ValidationError

# Register SVG namespaces so ElementTree tostring generates clean tags without ns0: prefix
standard_ET.register_namespace("", "http://www.w3.org/2000/svg")
standard_ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")


class ReportSanitizerService:
    """Hardened sanitizer enforcing element, attribute, and local fragment allowlists on untrusted SVG content."""

    MAX_SVG_BYTES = 512 * 1024  # 512 KiB
    MAX_XML_DEPTH = 20
    MAX_NODE_COUNT = 5000
    MAX_ATTRS_PER_NODE = 100
    MAX_CELL_STRING_LENGTH = 1000

    ALLOWED_TAGS = {
        "svg",
        "defs",
        "clipPath",
        "g",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "linearGradient",
        "radialGradient",
        "stop",
        "text",
        "tspan",
    }

    ALLOWED_ATTRS = {
        "viewBox",
        "width",
        "height",
        "x",
        "y",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "x1",
        "y1",
        "x2",
        "y2",
        "d",
        "points",
        "transform",
        "clip-path",
        "fill",
        "fill-opacity",
        "stroke",
        "stroke-width",
        "stroke-opacity",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "font-family",
        "font-size",
        "font-weight",
        "text-anchor",
        "dominant-baseline",
        "id",
        "class",
        "gradientUnits",
        "offset",
        "stop-color",
        "stop-opacity",
        "version",
        "xmlns",
        "xmlns:xlink",
        "baseProfile",
    }

    URL_FRAGMENT_REGEX = re.compile(r"^url\(#([a-zA-Z0-9_\-]+)\)$")

    @classmethod
    def sanitize_svg(cls, raw_svg: str) -> str:
        """Sanitize an SVG string using defusedxml and strict allowlists.
        
        Strips <style> tags and all executable or remote references while preserving
        verified local XML fragment definitions (clip-paths and gradients).
        """
        if not raw_svg or not raw_svg.strip():
            raise ValidationError("SVG content is empty.")

        svg_bytes = raw_svg.encode("utf-8")
        if len(svg_bytes) > cls.MAX_SVG_BYTES:
            raise ValidationError(
                f"SVG byte size ({len(svg_bytes)} bytes) exceeds limit of {cls.MAX_SVG_BYTES} bytes."
            )

        # 1. Parse structurally with defusedxml (blocks DTD, external entities, Billion Laughs)
        try:
            root = defused_ET.fromstring(
                raw_svg,
                forbid_dtd=True,
                forbid_entities=True,
                forbid_external=True,
            )
        except (DefusedXmlException, standard_ET.ParseError) as exc:
            raise ValidationError(f"Malformed or unsafe SVG XML: {exc}") from exc

        # 2. Collect all valid definition IDs from <defs> block
        valid_def_ids: set[str] = set()
        node_count = 0

        # Helper to strip namespace from tag name: {http://...}tag -> tag
        def get_local_tag(elem: standard_ET.Element) -> str:
            tag = elem.tag
            if "}" in tag:
                return tag.split("}", 1)[1]
            return tag

        if get_local_tag(root) != "svg":
            raise ValidationError(f"Root XML element must be <svg>, got <{get_local_tag(root)}>.")

        for elem in root.iter():
            node_count += 1
            if node_count > cls.MAX_NODE_COUNT:
                raise ValidationError(f"SVG node count exceeds maximum allowed limit ({cls.MAX_NODE_COUNT}).")

            local_tag = get_local_tag(elem)
            if local_tag in {"clipPath", "linearGradient", "radialGradient"} and "id" in elem.attrib:
                valid_def_ids.add(elem.attrib["id"])

        # 3. Recursive depth check and tree sanitization
        def sanitize_node(node: standard_ET.Element, depth: int) -> None:
            if depth > cls.MAX_XML_DEPTH:
                raise ValidationError(f"SVG XML nesting depth exceeds maximum allowed limit ({cls.MAX_XML_DEPTH}).")

            # Remove forbidden children (like <script>, <foreignObject>, <style>, <image>, <use>, <a>)
            children_to_remove = []
            for child in list(node):
                child_tag = get_local_tag(child)
                if child_tag not in cls.ALLOWED_TAGS:
                    children_to_remove.append(child)
                else:
                    sanitize_node(child, depth + 1)

            for child in children_to_remove:
                node.remove(child)

            # Filter attributes on current node
            if len(node.attrib) > cls.MAX_ATTRS_PER_NODE:
                raise ValidationError(f"Node <{get_local_tag(node)}> exceeds maximum attribute limit.")

            cleaned_attrib = {}
            for attr_name, attr_val in list(node.attrib.items()):
                # Normalize namespace in attr_name if present
                clean_attr_name = attr_name
                if "}" in clean_attr_name:
                    clean_attr_name = clean_attr_name.split("}", 1)[1]

                # Strip any event handler attribute starting with "on"
                if clean_attr_name.lower().startswith("on"):
                    continue

                # Strip href and xlink:href
                if "href" in clean_attr_name.lower():
                    continue

                # Reject attribute if not in allowed list
                if clean_attr_name not in cls.ALLOWED_ATTRS:
                    continue

                # Inspect attribute values containing "url("
                val_str = str(attr_val).strip()
                if "url(" in val_str.lower():
                    # Must strictly match local fragment regex ^url\(#[a-zA-Z0-9_\-]+\)$
                    match = cls.URL_FRAGMENT_REGEX.match(val_str)
                    if not match:
                        # Reject external URL, encoded URL, or scheme URL
                        continue
                    ref_id = match.group(1)
                    if ref_id not in valid_def_ids:
                        # Referenced ID does not exist in <defs>
                        continue

                cleaned_attrib[attr_name] = val_str

            node.attrib = cleaned_attrib

        sanitize_node(root, depth=1)

        # 4. Serialize sanitized XML back to string
        clean_xml = standard_ET.tostring(root, encoding="unicode")
        return clean_xml

    @classmethod
    def sanitize_table_cell(cls, value: Any) -> str:
        """Sanitize and boundedly escape a table cell value for HTML rendering."""
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "True" if value else "False"
        s = str(value)
        if len(s) > cls.MAX_CELL_STRING_LENGTH:
            s = s[: cls.MAX_CELL_STRING_LENGTH] + "…"
        return html.escape(s)

    @classmethod
    def escape_text(cls, text: str | None) -> str:
        """Strict HTML escaping for titles, descriptions, and user strings."""
        if text is None:
            return ""
        return html.escape(str(text))
