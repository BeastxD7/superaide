"""Ava School, the local half (#57): generic lessons an owner may share, never their data.

A lesson is one short sentence of advice about how an assistant of a role should behave ("When I say
urgent I mean money, not deadlines"). This package only lets the owner write lessons, consent to each,
export the consented ones to a file they can read, and import a file from another Ava. There is no
network sharing here, and nothing in Ava's prompts reads a lesson: applying lessons is a later decision.
"""
