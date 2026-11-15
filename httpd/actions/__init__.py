"""Adapter mixins for the POST actions, grouped by the domain they adapt.

Each module turns a JSON body into a call on a domain module and the answer back into
JSON. None of them opens the database directly, and none decides who may ask — `gate.py`
does that once, before any of these runs.
"""
