#!/usr/bin/env python3
"""
Test script for split/merge CTF operations via Polymarket proxy wallet.

Performs a round-trip: splits USDC.e into outcome tokens, then merges them
back. Prints transaction hashes for verification on Polygonscan.

Usage:
    python test_split_merge.py <condition_id> [amount_usdc]

    condition_id: The market's condition ID (bytes32 hex string)
    amount_usdc:  Amount of USDC to split (default: 1.0)

Environment variables:
    POLYMARKET_MAGIC_PK           - Magic wallet private key (0x...)
    POLYMARKET_BUILDER_API_KEY    - Builder API key
    POLYMARKET_BUILDER_SECRET     - Builder secret (base64)
    POLYMARKET_BUILDER_PASSPHRASE - Builder passphrase
    POLYGON_RPC_URL               - (optional) Custom Polygon RPC

Example:
    export POLYMARKET_MAGIC_PK=0x...
    export POLYMARKET_BUILDER_API_KEY=...
    export POLYMARKET_BUILDER_SECRET=...
    export POLYMARKET_BUILDER_PASSPHRASE=...
    python test_split_merge.py 0xabc123...def456 1.0
"""

import os
import sys
import time

from dotenv import load_dotenv
from eth_account import Account
from py_builder_signing_sdk.config import BuilderApiKeyCreds, BuilderConfig
from web3 import Web3

from magic_proxy.approvals import check_all_approvals, get_missing_approval_txs
from magic_proxy.config import (
    CTF_CONTRACT,
    ERC20_ABI,
    POLYGON_RPC_URL,
    USDC_E,
    USDC_E_DECIMALS,
)
from magic_proxy.ctf_ops import (
    create_merge_positions_tx,
    create_split_position_tx,
    get_outcome_token_balance,
)
from magic_proxy.proxy_wallet import derive_proxy_address
from magic_proxy.relay import execute_relay

POLYGONSCAN_TX_URL = "https://polygonscan.com/tx/"


def load_config():
    """Load configuration from environment variables."""
    load_dotenv()

    private_key = os.environ.get("POLYMARKET_MAGIC_PK")
    if not private_key:
        print("Error: POLYMARKET_MAGIC_PK not set")
        sys.exit(1)

    api_key = os.environ.get("POLYMARKET_BUILDER_API_KEY")
    secret = os.environ.get("POLYMARKET_BUILDER_SECRET")
    passphrase = os.environ.get("POLYMARKET_BUILDER_PASSPHRASE")
    if not all([api_key, secret, passphrase]):
        print("Error: Builder credentials not set (POLYMARKET_BUILDER_API_KEY, "
              "POLYMARKET_BUILDER_SECRET, POLYMARKET_BUILDER_PASSPHRASE)")
        sys.exit(1)

    builder_config = BuilderConfig(
        local_builder_creds=BuilderApiKeyCreds(
            key=api_key, secret=secret, passphrase=passphrase
        )
    )

    return private_key, builder_config


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    condition_id = sys.argv[1]
    amount_usdc = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    amount_raw = int(amount_usdc * 10**USDC_E_DECIMALS)

    # --- Setup ---
    print(f"=== Polymarket Split/Merge Test ===\n")

    private_key, builder_config = load_config()

    account = Account.from_key(private_key)
    eoa_address = account.address
    proxy_address = derive_proxy_address(eoa_address)

    print(f"EOA address:   {eoa_address}")
    print(f"Proxy wallet:  {proxy_address}")
    print(f"Condition ID:  {condition_id}")
    print(f"Amount:        {amount_usdc} USDC ({amount_raw} raw units)")
    print()

    # --- Connect to Polygon ---
    rpc_url = os.environ.get("POLYGON_RPC_URL", POLYGON_RPC_URL)
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        print(f"Error: Cannot connect to Polygon RPC at {rpc_url}")
        sys.exit(1)
    print(f"Connected to Polygon (block {w3.eth.block_number})")

    # --- Check USDC balance ---
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
    balance = usdc.functions.balanceOf(Web3.to_checksum_address(proxy_address)).call()
    balance_usdc = balance / 10**USDC_E_DECIMALS
    print(f"USDC.e balance: {balance_usdc:.6f} USDC")

    if balance < amount_raw:
        print(f"\nError: Insufficient USDC.e balance. Need {amount_usdc}, have {balance_usdc:.6f}")
        sys.exit(1)

    # --- Check approvals ---
    print("\nChecking approvals...")
    approval_status = check_all_approvals(w3, proxy_address)

    if not approval_status["all_approved"]:
        print("Missing approvals detected:")
        for name, approved in approval_status["usdc_approvals"].items():
            if not approved:
                print(f"  USDC -> {name}: NOT APPROVED")
        for name, approved in approval_status["outcome_token_approvals"].items():
            if not approved:
                print(f"  ERC1155 -> {name}: NOT APPROVED")

        print("\nSending approval transactions...")
        missing_txs = get_missing_approval_txs(w3, proxy_address)
        result = execute_relay(private_key, builder_config, missing_txs, "approvals")
        print(f"Approvals tx: {POLYGONSCAN_TX_URL}{result['transaction_hash']}")
        print("Waiting for approvals to propagate...")
        time.sleep(5)
    else:
        print("All approvals OK")

    # --- Split ---
    print(f"\n--- Splitting {amount_usdc} USDC into outcome tokens ---")
    split_tx = create_split_position_tx(condition_id, amount_raw)
    split_result = execute_relay(
        private_key, builder_config, [split_tx], f"split {amount_usdc} USDC"
    )
    split_hash = split_result["transaction_hash"]
    print(f"Split tx:      {POLYGONSCAN_TX_URL}{split_hash}")

    # --- Check outcome token balances ---
    time.sleep(3)
    print("\nOutcome token balances after split:")
    yes_balance = get_outcome_token_balance(w3, proxy_address, condition_id, 1)
    no_balance = get_outcome_token_balance(w3, proxy_address, condition_id, 2)
    print(f"  Yes tokens (indexSet=1): {yes_balance}")
    print(f"  No  tokens (indexSet=2): {no_balance}")

    # --- Merge ---
    print(f"\n--- Merging {amount_usdc} USDC worth of outcome tokens ---")
    merge_tx = create_merge_positions_tx(condition_id, amount_raw)
    merge_result = execute_relay(
        private_key, builder_config, [merge_tx], f"merge {amount_usdc} USDC"
    )
    merge_hash = merge_result["transaction_hash"]
    print(f"Merge tx:      {POLYGONSCAN_TX_URL}{merge_hash}")

    # --- Verify ---
    time.sleep(3)
    final_balance = usdc.functions.balanceOf(
        Web3.to_checksum_address(proxy_address)
    ).call()
    final_balance_usdc = final_balance / 10**USDC_E_DECIMALS
    print(f"\nFinal USDC.e balance: {final_balance_usdc:.6f} USDC")

    # --- Summary ---
    print("\n=== Summary ===")
    print(f"Split tx: {POLYGONSCAN_TX_URL}{split_hash}")
    print(f"Merge tx: {POLYGONSCAN_TX_URL}{merge_hash}")
    print(f"Balance change: {balance_usdc:.6f} -> {final_balance_usdc:.6f} USDC")

    if final_balance == balance:
        print("\nRound-trip successful! Balance unchanged.")
    else:
        diff = (final_balance - balance) / 10**USDC_E_DECIMALS
        print(f"\nBalance delta: {diff:+.6f} USDC (expected 0)")


if __name__ == "__main__":
    main()
