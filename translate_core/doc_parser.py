# translate_core/doc_parser.py

from pathlib import Path
from typing import Optional

from markitdown import MarkItDown


class DocumentParser:
    """
    Wrapper around Microsoft MarkItDown to convert documents to Markdown.
    """

    def __init__(self, enable_plugins: bool = False):
        self.md = MarkItDown(enable_plugins=enable_plugins)

    def to_markdown(self, source: Path) -> str:
        """
        Convert a local file to Markdown text.
        """
        result = self.md.convert(str(source))
        return result.text_content or ""

    def from_markdown(self, md_text: str, output_path: Path):
        """
        For now: just write the Markdown back.
        If you need round-trip DOCX, you can later add python-docx logic.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(md_text, encoding="utf-8")
