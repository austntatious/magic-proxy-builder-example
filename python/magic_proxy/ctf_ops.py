"""
CTF (Conditional Token Framework) operations: split, merge, redeem.

These operations interact with the Gnosis CTF contract used by Polymarket.
The existing TypeScript codebase only implements redeemPositions;
splitPosition and mergePositions are new additions for market making.
"""

from eth_utils import keccak
from web3 import Web3

from .config import CTF_ABI, CTF_CONTRACT, ERC1155_ABI, USDC_E

# bytes32(0) — used as parentCollectionId for top-level positions
PARENT_COLLECTION_ID = b"\x00" * 32

# Binary market partition: outcome 0 = indexSet 1, outcome 1 = indexSet 2
BINARY_PARTITION = [1, 2]

# Shared web3 + contract instances for ABI encoding
_w3 = Web3()
_ctf = _w3.eth.contract(address=Web3.to_checksum_address(CTF_CONTRACT), abi=CTF_ABI)


def create_split_position_tx(condition_id: str, amount: int) -> dict:
    """
    Build a splitPosition transaction for a binary market.

    Splits `amount` of USDC.e into equal amounts of Yes and No outcome tokens.

    Args:
        condition_id: The market's condition ID (bytes32 hex string).
        amount: Amount of USDC.e to split, in raw units (6 decimals).
                e.g. 1_000_000 = 1 USDC.

    Returns:
        Transaction dict {to, data, value} ready for relay execution.
    """
    data = _ctf.encode_abi("splitPosition", args=[
        Web3.to_checksum_address(USDC_E),
        PARENT_COLLECTION_ID,
        bytes.fromhex(condition_id[2:]) if condition_id.startswith("0x") else bytes.fromhex(condition_id),
        BINARY_PARTITION,
        amount,
    ])
    return {"to": CTF_CONTRACT, "data": data, "value": "0"}


def create_merge_positions_tx(condition_id: str, amount: int) -> dict:
    """
    Build a mergePositions transaction for a binary market.

    Merges `amount` of each outcome token (Yes + No) back into USDC.e.

    Args:
        condition_id: The market's condition ID (bytes32 hex string).
        amount: Amount to merge, in raw units (6 decimals).

    Returns:
        Transaction dict {to, data, value} ready for relay execution.
    """
    data = _ctf.encode_abi("mergePositions", args=[
        Web3.to_checksum_address(USDC_E),
        PARENT_COLLECTION_ID,
        bytes.fromhex(condition_id[2:]) if condition_id.startswith("0x") else bytes.fromhex(condition_id),
        BINARY_PARTITION,
        amount,
    ])
    return {"to": CTF_CONTRACT, "data": data, "value": "0"}


def create_redeem_positions_tx(
    condition_id: str, index_sets: list[int] | None = None
) -> dict:
    """
    Build a redeemPositions transaction.

    Redeems winning outcome tokens for USDC.e after market resolution.

    Args:
        condition_id: The market's condition ID (bytes32 hex string).
        index_sets: Which outcome positions to redeem. Defaults to [1, 2].

    Returns:
        Transaction dict {to, data, value} ready for relay execution.
    """
    if index_sets is None:
        index_sets = BINARY_PARTITION

    data = _ctf.encode_abi("redeemPositions", args=[
        Web3.to_checksum_address(USDC_E),
        PARENT_COLLECTION_ID,
        bytes.fromhex(condition_id[2:]) if condition_id.startswith("0x") else bytes.fromhex(condition_id),
        index_sets,
    ])
    return {"to": CTF_CONTRACT, "data": data, "value": "0"}


def get_collection_id(condition_id: str, index_set: int) -> bytes:
    """
    Compute the collection ID for a specific outcome of a condition.

    Matches the Gnosis CTF contract formula:
        collectionId = bytes32(uint256(parentCollectionId) +
                               uint256(keccak256(abi.encodePacked(conditionId, indexSet))))
    """
    cond_bytes = bytes.fromhex(condition_id[2:]) if condition_id.startswith("0x") else bytes.fromhex(condition_id)
    index_set_bytes = index_set.to_bytes(32, byteorder="big")
    hash_part = int.from_bytes(keccak(cond_bytes + index_set_bytes), byteorder="big")
    parent_int = int.from_bytes(PARENT_COLLECTION_ID, byteorder="big")
    result = (parent_int + hash_part) % (2**256)
    return result.to_bytes(32, byteorder="big")


def get_position_id(condition_id: str, index_set: int) -> int:
    """
    Compute the ERC1155 token ID (position ID) for a specific outcome.

    Matches the Gnosis CTF contract formula:
        positionId = uint256(keccak256(abi.encodePacked(collateralToken, collectionId)))

    Args:
        condition_id: The market's condition ID (bytes32 hex string).
        index_set: The index set for the outcome (1 for Yes, 2 for No in binary markets).

    Returns:
        The ERC1155 token ID as an integer.
    """
    collection_id = get_collection_id(condition_id, index_set)
    collateral_bytes = bytes.fromhex(USDC_E[2:])  # 20-byte address (encodePacked, no padding)
    return int.from_bytes(keccak(collateral_bytes + collection_id), byteorder="big")


def get_outcome_token_balance(w3: Web3, proxy_address: str, condition_id: str, index_set: int) -> int:
    """
    Query the outcome token balance for a specific position.

    Args:
        w3: Web3 instance connected to Polygon.
        proxy_address: The proxy wallet address.
        condition_id: The market's condition ID.
        index_set: The index set (1 for Yes, 2 for No).

    Returns:
        The token balance in raw units.
    """
    token_id = get_position_id(condition_id, index_set)
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(CTF_CONTRACT), abi=ERC1155_ABI
    )
    return contract.functions.balanceOf(
        Web3.to_checksum_address(proxy_address), token_id
    ).call()
