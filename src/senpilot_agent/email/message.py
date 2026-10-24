"""Shared email text extraction for polling and webhook intake."""

from html.parser import HTMLParser


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def message_text(message: dict) -> str:
    """Prefer the provider's extracted current reply over quoted history."""
    for key in ("extracted_text", "extracted_html", "text", "html"):
        value = message.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        if key.endswith("html"):
            parser = _VisibleText()
            parser.feed(value)
            return "".join(parser.parts).strip()
        return value
    return ""
