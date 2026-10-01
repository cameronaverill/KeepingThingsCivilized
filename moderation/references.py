"""Renumbers "message N" references in moderator text to the numbers the page shows.

The agents see each message as `<message id="<database pk>">`, while the page labels messages with their per-conversation
`seq_no`. The model sometimes cites the id and sometimes counts positionally, so the text it writes is post-processed here
(the model's input never changes). `renumber_message_references(text, id_to_seq)` replaces every number that is a key of
`id_to_seq` (a transcript id) with its `seq_no`, each independently and in one pass; numbers that are not keys stay as
written (that is how a positional "message 1" survives). The draft preview's placeholder id -1 is just one more key.

Known and accepted limit: if the model cites a position that happens to equal another window message's id, it is
renumbered. That needs small ids and a non-identity mapping, so it is rare.
"""

import re

_NUMBER = r"-?\d+(?![\w-])"
_SEPARATOR = r"(?:\s*,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+|\s*&\s*)"
_REFERENCE = re.compile(
    r"(?<![\w-])(messages?\s*#?\s*)(" + _NUMBER + r"(?:" + _SEPARATOR + _NUMBER + r")*)", re.IGNORECASE
)
_NUMBER_TOKEN = re.compile(r"(?<![\w-])" + _NUMBER)


def renumber_message_references(text, id_to_seq):
    """`text` with each message reference's numbers replaced by `id_to_seq[number]` where present (see module docstring)."""
    if not isinstance(text, str) or not id_to_seq:
        return text

    def swap_number(match):
        value = int(match.group(0))
        return str(id_to_seq[value]) if value in id_to_seq else match.group(0)

    def swap_reference(match):
        return match.group(1) + _NUMBER_TOKEN.sub(swap_number, match.group(2))

    return _REFERENCE.sub(swap_reference, text)
