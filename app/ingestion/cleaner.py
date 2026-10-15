import re

def clean_text(text: str) -> str:
    """Basic text cleaning for documentation."""
    # Remove excessive blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Remove trailing/leading spaces on lines
    lines = [line.strip() for line in text.split('\n')]
    text = '\n'.join(lines)
    return text.strip()
