"""XML parsing for input data (review H2 fix 3, plan task 63).

Tools parse XML from a user's file (FB2, an EPUB's OPF or container.xml, OOXML parts) only
through :func:`parse_xml`, never ``xml.etree``, ``xml.dom``, ``xml.sax`` or ``lxml`` directly
(``tests/unit/test_archive_checks.py`` fails on a direct import). ``defusedxml`` refuses entity
declarations and external references, so no entity is ever expanded or fetched (XXE, billion
laughs). A DOCTYPE without entities, such as XHTML's, is fine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from filex.registry import StepError


def parse_xml(data: bytes | str, what: str) -> Any:
    """The root ``Element`` of ``data``. Entity declarations, external references or
    malformed XML are ``INVALID_INPUT``; ``what`` names the file in the message."""
    from defusedxml import DefusedXmlException
    from defusedxml.ElementTree import ParseError, fromstring

    try:
        return fromstring(data)
    except DefusedXmlException as e:
        raise StepError(
            "INVALID_INPUT", f"{what} declares XML entities, which FileX does not allow."
        ) from e
    except ParseError as e:
        raise StepError("INVALID_INPUT", f"{what} is not well-formed XML.") from e


def xml_root_tag(path: Path, what: str) -> str:
    """The root element's tag (``{namespace}name``) of the XML file at ``path``, read only up to
    the root's start tag, so a large file is not parsed whole. Entity declarations sit before
    the root, so they are refused here like in :func:`parse_xml`."""
    from defusedxml import DefusedXmlException
    from defusedxml.ElementTree import ParseError, iterparse

    try:
        with path.open("rb") as fh:
            for _, element in iterparse(fh, events=("start",)):
                return element.tag
    except DefusedXmlException as e:
        raise StepError(
            "INVALID_INPUT", f"{what} declares XML entities, which FileX does not allow."
        ) from e
    except ParseError as e:
        raise StepError("INVALID_INPUT", f"{what} is not well-formed XML.") from e
    raise StepError("INVALID_INPUT", f"{what} is not well-formed XML.")
