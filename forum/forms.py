"""Forms of the forum pages (step 7b).

The forms only carry the text the person typed. They never trim, cut or judge it: emptiness, length, the wait between
messages and every other rule are decided by ``forum.services`` so a hand-made POST gets exactly the same answer as the
page. There is deliberately no ``max_length`` (it would add a ``maxlength`` attribute and silently truncate the text).
"""

from django import forms


class TextForm(forms.Form):
    """One free-text field named ``text``: a message in a conversation or a new proposition."""

    text = forms.CharField(required=False, strip=False, widget=forms.Textarea)

    def clean_text(self):
        # Missing field behaves like an empty one; the service explains "Your message is empty."
        return self.cleaned_data.get("text") or ""


class SearchForm(forms.Form):
    """The home page search box (``?q=``)."""

    q = forms.CharField(required=False, strip=True)

    def clean_q(self):
        # Collapse inner whitespace so "rent   caps" finds "rent caps".
        return " ".join((self.cleaned_data.get("q") or "").split())
