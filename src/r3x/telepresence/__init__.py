"""Telepresence mode (owner, November 16): Rex as a conduit for the owner's voice, with no models.

`python -m r3x telepresence` wakes the servos, opens the visor, shows the telepresence lights and
then carries room audio and camera video to one authenticated client and the client's voice to
Rex's speaker, over the home WireGuard VPN. Nothing here listens for meaning, replies, remembers or
moves on its own. Plan and decisions: docs/TELEPRESENCE_PLAN.md. Importing this package opens no
socket, device or model.
"""
