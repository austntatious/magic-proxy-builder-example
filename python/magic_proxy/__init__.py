"""
magic_proxy — Python port of Polymarket's magic-proxy-builder.

A standalone library for interacting with Polymarket's proxy wallet infrastructure
for Magic Link users. Focuses on CTF operations (split, merge, redeem) via the
gasless relay system.

Usage:
    from magic_proxy import derive_proxy_address, execute_relay
    from magic_proxy.ctf_ops import create_split_position_tx, create_merge_positions_tx
"""

from .proxy_wallet import derive_proxy_address
from .relay import execute_relay

__all__ = [
    "derive_proxy_address",
    "execute_relay",
]
