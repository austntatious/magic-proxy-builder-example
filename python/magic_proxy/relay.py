"""
Proxy wallet relay infrastructure for Polymarket.

Implements the proxy-specific relay protocol that is NOT available in the
py-builder-relayer-client package (which only supports Safe wallets).

Uses py-builder-signing-sdk for builder HMAC authentication.
"""

import json
import time

import requests
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak
from web3 import Web3

from .config import (
    GAS_CONSTANTS,
    PROXY_FACTORY,
    PROXY_WALLET_FACTORY_ABI,
    RELAY_HUB,
    RELAYER_URL,
)
from .proxy_wallet import derive_proxy_address


# CallType.Call = 1 (from @polymarket/builder-relayer-client)
CALL_TYPE_CALL = 1


def calculate_gas_limit(tx_count: int) -> str:
    """
    Calculate gas limit for a batch of relay transactions.
    Port of calculateGasLimit from utils/relay.ts.
    """
    c = GAS_CONSTANTS
    tx_gas = tx_count * c["BASE_GAS_PER_TX"]
    relayer_will_send = tx_gas + c["RELAY_HUB_PADDING"]
    max_signable = relayer_will_send - c["INTRINSIC_COST"] - c["OVERHEAD_BUFFER"]
    execution_needs = tx_gas + c["MIN_EXECUTION_BUFFER"]
    return str(min(max_signable, max(execution_needs, 3_000_000)))


def encode_proxy_transaction_data(transactions: list[dict]) -> str:
    """
    ABI-encode a call to ProxyWalletFactory.proxy(calls).

    The proxy function signature is:
        proxy((uint8 typeCode, address to, uint256 value, bytes data)[] calls)

    Each transaction dict should have: {to: str, data: str, value: str}

    Returns the hex-encoded calldata string (with 0x prefix).
    """
    w3 = Web3()
    contract = w3.eth.contract(abi=PROXY_WALLET_FACTORY_ABI)

    tuples = []
    for tx in transactions:
        tuples.append((
            CALL_TYPE_CALL,                             # typeCode: uint8
            Web3.to_checksum_address(tx["to"]),         # to: address
            int(tx.get("value", "0")),                  # value: uint256
            bytes.fromhex(tx["data"][2:]) if tx["data"].startswith("0x") else bytes.fromhex(tx["data"]),  # data: bytes
        ))

    return contract.encode_abi("proxy", args=[tuples])


def _create_relay_hash(
    from_addr: str,
    to_addr: str,
    data: str,
    relayer_fee: str,
    gas_price: str,
    gas_limit: str,
    nonce: str,
    relay_hub: str,
    relay: str,
) -> bytes:
    """
    Create the relay hash matching the TypeScript createRelayHash.

    Concatenates raw bytes: "rlx:" + from(20) + to(20) + data(var)
        + fee(32) + gasPrice(32) + gasLimit(32) + nonce(32)
        + relayHub(20) + relay(20)
    Then keccak256.
    """
    def addr_bytes(addr: str) -> bytes:
        return bytes.fromhex(addr[2:])

    def uint256_bytes(val: str) -> bytes:
        return int(val).to_bytes(32, byteorder="big")

    data_bytes = bytes.fromhex(data[2:]) if data.startswith("0x") else bytes.fromhex(data)

    to_hash = b"".join([
        b"rlx:",
        addr_bytes(from_addr),
        addr_bytes(to_addr),
        data_bytes,
        uint256_bytes(relayer_fee),
        uint256_bytes(gas_price),
        uint256_bytes(gas_limit),
        uint256_bytes(nonce),
        addr_bytes(relay_hub),
        addr_bytes(relay),
    ])

    return keccak(to_hash)


def _sign_relay_hash(private_key: str, relay_hash: bytes) -> str:
    """
    Sign the relay hash using personal_sign (EIP-191).

    The TypeScript code uses wallet.signMessage(utils.arrayify(hash))
    which applies the \\x19Ethereum Signed Message:\\n prefix.
    """
    msg = encode_defunct(relay_hash)
    signed = Account.sign_message(msg, private_key)
    return "0x" + signed.signature.hex()


def get_relay_payload(eoa_address: str) -> dict:
    """
    Fetch relay payload (nonce + relay address) from the relayer.
    GET /relay-payload?address={eoa}&type=PROXY
    """
    resp = requests.get(
        f"{RELAYER_URL}/relay-payload",
        params={"address": eoa_address, "type": "PROXY"},
    )
    resp.raise_for_status()
    return resp.json()


def build_proxy_transaction_request(
    private_key: str,
    from_addr: str,
    encoded_data: str,
    gas_limit: str,
    relay_addr: str,
    nonce: str,
    metadata: str = "",
) -> dict:
    """
    Build the full proxy transaction request for relayer submission.
    Mirrors buildProxyTransactionRequest from utils/relay.ts.
    """
    to = PROXY_FACTORY
    proxy_wallet = derive_proxy_address(from_addr)
    relayer_fee = "0"
    gas_price = "0"

    relay_hash = _create_relay_hash(
        from_addr, to, encoded_data,
        relayer_fee, gas_price, gas_limit, nonce,
        RELAY_HUB, relay_addr,
    )

    signature = _sign_relay_hash(private_key, relay_hash)

    return {
        "from": from_addr,
        "to": to,
        "proxyWallet": proxy_wallet,
        "data": encoded_data,
        "nonce": nonce,
        "signature": signature,
        "signatureParams": {
            "gasPrice": gas_price,
            "gasLimit": gas_limit,
            "relayerFee": relayer_fee,
            "relayHub": RELAY_HUB,
            "relay": relay_addr,
        },
        "type": "PROXY",
        "metadata": metadata,
    }


def submit_relay_transaction(request: dict, builder_config) -> str:
    """
    Submit a signed proxy transaction to the relayer.

    Args:
        request: The ProxyTransactionRequest dict.
        builder_config: A py_builder_signing_sdk.BuilderConfig instance.

    Returns:
        The transaction ID from the relayer.
    """
    body = json.dumps(request)
    headers = builder_config.generate_builder_headers("POST", "/submit", body)
    headers["Content-Type"] = "application/json"

    resp = requests.post(f"{RELAYER_URL}/submit", headers=headers, data=body)
    if not resp.ok:
        raise RuntimeError(f"Relayer submission failed ({resp.status_code}): {resp.text}")

    return resp.json()["transactionID"]


def poll_transaction(
    transaction_id: str,
    success_states: list[str] | None = None,
    fail_state: str = "STATE_FAILED",
    max_polls: int = 10,
    poll_interval: float = 2.0,
) -> dict | None:
    """
    Poll the relayer for transaction status until it reaches a desired state.

    Returns the transaction dict on success, None on failure/timeout.
    """
    if success_states is None:
        success_states = ["STATE_MINED", "STATE_CONFIRMED"]

    for _ in range(max_polls):
        resp = requests.get(
            f"{RELAYER_URL}/transaction",
            params={"id": transaction_id},
        )
        if resp.ok:
            txns = resp.json()
            if txns:
                txn = txns[0] if isinstance(txns, list) else txns
                state = txn.get("state", "")
                if state in success_states:
                    return txn
                if state == fail_state:
                    return None

        time.sleep(poll_interval)

    return None


def execute_relay(
    private_key: str,
    builder_config,
    transactions: list[dict],
    description: str = "",
) -> dict:
    """
    Execute a batch of transactions through the Polymarket relay.

    High-level convenience function that handles the full flow:
    encode -> get relay payload -> build request -> submit -> poll.

    Args:
        private_key: EOA private key (hex string with 0x prefix).
        builder_config: A py_builder_signing_sdk.BuilderConfig instance.
        transactions: List of {to, data, value} dicts.
        description: Optional description/metadata.

    Returns:
        Dict with transaction_hash and transaction_id.

    Raises:
        RuntimeError: If the transaction fails or times out.
    """
    account = Account.from_key(private_key)
    from_addr = account.address

    # 1. Encode proxy transaction data
    encoded_data = encode_proxy_transaction_data(transactions)

    # 2. Get relay payload (nonce + relay address)
    relay_payload = get_relay_payload(from_addr)

    # 3. Build signed request
    gas_limit = calculate_gas_limit(len(transactions))
    tx_request = build_proxy_transaction_request(
        private_key,
        from_addr,
        encoded_data,
        gas_limit,
        relay_payload["address"],
        relay_payload["nonce"],
        metadata=description,
    )

    # 4. Submit to relayer
    transaction_id = submit_relay_transaction(tx_request, builder_config)

    # 5. Poll for confirmation
    result = poll_transaction(transaction_id)
    if result is None:
        raise RuntimeError(f"Transaction {transaction_id} failed or timed out")

    return {
        "transaction_hash": result.get("transactionHash", ""),
        "transaction_id": transaction_id,
    }
