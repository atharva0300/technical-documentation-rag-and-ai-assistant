import re

# A table-of-contents entry: a title followed by a dot leader and a page number, e.g.
# "7.14. Registering a LoadTimeWeaver ........................ 141". These lines carry no
# content of their own but list every section title, so they out-score the real sections
# in keyword search and crowd them out of the top results.
TOC_LINE_REGEX = re.compile(r'(?:\.\s?){6,}\s*\d+\s*$')


def is_toc_line(line: str) -> bool:
    return bool(TOC_LINE_REGEX.search(line))


def clean_text(text: str) -> str:
    """Basic text cleaning for documentation."""
    # Remove trailing/leading spaces on lines, and drop table-of-contents entries
    lines = [line.strip() for line in text.split('\n')]
    text = '\n'.join(line for line in lines if not is_toc_line(line))
    # Remove excessive blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
