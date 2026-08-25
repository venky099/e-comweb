"""Shared form widgets and fields."""
from django import forms


class MultipleFileInput(forms.ClearableFileInput):
    """File input that accepts several files.

    Django 5 refuses ``multiple`` on the stock ``ClearableFileInput`` because
    the default field only cleans one file; this pairs it with
    ``MultipleFileField`` below, which cleans the whole list.
    """

    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """``FileField`` whose ``clean`` handles a list of uploads."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(item, initial) for item in data]
        return single_clean(data, initial)
