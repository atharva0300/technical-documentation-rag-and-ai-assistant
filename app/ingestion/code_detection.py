"""
Detects code in chunk text, including code extracted from PDFs, where it arrives as
plain lines with no Markdown fences (e.g. "@Configuration", "public class AppConfig {",
"<context:load-time-weaver/>").

Classification is per line and needs at least MIN_CODE_LINES matching lines, so prose
that merely mentions an annotation or a tag inline ("add @EnableLoadTimeWeaving to one
of your @Configuration classes") isn't counted as code.
"""
import re
from typing import List, Tuple

MIN_CODE_LINES = 2

_FENCE = re.compile(r'^\s*(```|~~~)\s*([\w+#-]*)')

_JAVA_LINE = re.compile(
    r'^\s*(?:'
    r'@[A-Z]\w*(?:\(.*\))?\s*$'                                   # annotation on its own line
    r'|(?:public|private|protected|static|final|abstract)\s+[\w<>\[\], ]+'  # declarations
    r'|(?:import|package)\s+[\w.*]+;\s*$'
    r'|(?:class|interface|enum)\s+\w+'
    r'|return\b.*;\s*$'
    r'|[}{]\s*;?\s*$'                                              # lone braces
    r'|.*\)\s*\{\s*$'                                              # method/block opener
    r'|[\w<>\[\].]+\s+\w+\s*=\s*.+;\s*$'                           # typed assignment
    r'|[\w.]+\(.*\);\s*$'                                          # call statement
    r')'
)

_XML_LINE = re.compile(r'^\s*<(?:/?[A-Za-z][\w:.-]*(?:\s[^<>]*)?/?|!--.*--|\?xml[^>]*\?)>\s*\S*.*$')


def detect_code(text: str) -> Tuple[bool, List[str]]:
    """Returns (has_code, languages) for a chunk of text."""
    languages = set()
    java_lines = xml_lines = 0
    in_fence = False

    for line in text.splitlines():
        fence = _FENCE.match(line)
        if fence:
            if not in_fence and fence.group(2):
                languages.add(fence.group(2).lower())
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = line.strip()
        if not stripped or len(stripped) > 160:
            continue
        if _XML_LINE.match(stripped):
            xml_lines += 1
        elif _JAVA_LINE.match(stripped) and not stripped.endswith("."):
            java_lines += 1

    fenced = "```" in text or "~~~" in text
    if java_lines >= MIN_CODE_LINES:
        languages.add("java")
    if xml_lines >= MIN_CODE_LINES:
        languages.add("xml")

    return bool(fenced or java_lines >= MIN_CODE_LINES or xml_lines >= MIN_CODE_LINES), sorted(languages)
