"""Speech-to-text and image understanding as pluggable providers (issues #501, #511).

The owner picks, per capability, one of the model endpoints already connected on the AI model page (or Claude)
and a model. Nothing is sent anywhere until they do: an unset capability changes no behaviour.
"""
