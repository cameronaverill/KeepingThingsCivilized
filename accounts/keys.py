"""Comparison keys for usernames and emails.

SQLite's LOWER() only folds ASCII, so "Emile"/"emile" style look-alikes (and "ss"/sharp s, Kelvin sign, fullwidth
letters, precomposed versus combining accents) would count as different accounts. The User model therefore stores a
normalised copy of each value in a unique column and compares those instead.
"""
import unicodedata


def normalize_key(value: str) -> str:
    """NFKC(casefold(NFKC(value.strip()))): the value two spellings of one name or address have in common."""
    stripped = unicodedata.normalize("NFKC", value.strip())
    return unicodedata.normalize("NFKC", stripped.casefold())
