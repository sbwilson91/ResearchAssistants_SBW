"""Utilities shared by every bot in the fleet.

Each bot runs with its own folder on PYTHONPATH plus the repo root, so
`from common.http import get` resolves the same way from every bot.
"""
