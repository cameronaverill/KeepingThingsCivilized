"""The one rule for counting the length of a message (plan section 4)."""

import unicodedata


def count_message_chars(text: str) -> int:
    """Number of characters in a message, as users and the server both count them.

    Line endings (CRLF and lone CR) become a single ``\\n``, the text is put in Unicode NFC form, leading and trailing
    whitespace is stripped, and the remaining code points are counted (an emoji is 1).
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = unicodedata.normalize("NFC", normalized).strip()
    return len(normalized)
