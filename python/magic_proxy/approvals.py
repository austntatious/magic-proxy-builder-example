"""ERC20 and ERC1155 approval management for Polymarket proxy wallets."""

from web3 import Web3

from .config import (
    CTF_CONTRACT,
    CTF_EXCHANGE,
    ERC1155_ABI,
    ERC20_ABI,
    NEG_RISK_ADAPTER,
    NEG_RISK_CTF_EXCHANGE,
    USDC_E,
)

MAX_UINT256 = 2**256 - 1
APPROVAL_THRESHOLD = 1_000_000_000_000  # 1M USDC in raw units

# Spenders that need USDC.e allowance (matches approvals.ts)
USDC_SPENDERS = [
    {"address": CTF_CONTRACT, "name": "CTF Contract"},
    {"address": NEG_RISK_ADAPTER, "name": "Neg Risk Adapter"},
    {"address": CTF_EXCHANGE, "name": "CTF Exchange"},
    {"address": NEG_RISK_CTF_EXCHANGE, "name": "Neg Risk CTF Exchange"},
]

# Spenders that need ERC1155 setApprovalForAll on the CTF contract
OUTCOME_TOKEN_SPENDERS = [
    {"address": CTF_EXCHANGE, "name": "CTF Exchange"},
    {"address": NEG_RISK_CTF_EXCHANGE, "name": "Neg Risk Exchange"},
    {"address": NEG_RISK_ADAPTER, "name": "Neg Risk Adapter"},
]


def check_usdc_approval(w3: Web3, proxy_address: str, spender: str) -> bool:
    """Check if the proxy wallet has sufficient USDC.e allowance for a spender."""
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI
    )
    allowance = contract.functions.allowance(
        Web3.to_checksum_address(proxy_address),
        Web3.to_checksum_address(spender),
    ).call()
    return allowance >= APPROVAL_THRESHOLD


def check_erc1155_approval(w3: Web3, proxy_address: str, spender: str) -> bool:
    """Check if the proxy wallet has ERC1155 approval for a spender on CTF."""
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(CTF_CONTRACT), abi=ERC1155_ABI
    )
    return contract.functions.isApprovedForAll(
        Web3.to_checksum_address(proxy_address),
        Web3.to_checksum_address(spender),
    ).call()


def check_all_approvals(w3: Web3, proxy_address: str) -> dict:
    """
    Check all required approvals for the proxy wallet.

    Returns:
        {
            "all_approved": bool,
            "usdc_approvals": {"CTF Contract": True, ...},
            "outcome_token_approvals": {"CTF Exchange": True, ...},
        }
    """
    usdc_approvals = {}
    for s in USDC_SPENDERS:
        usdc_approvals[s["name"]] = check_usdc_approval(w3, proxy_address, s["address"])

    outcome_token_approvals = {}
    for s in OUTCOME_TOKEN_SPENDERS:
        outcome_token_approvals[s["name"]] = check_erc1155_approval(
            w3, proxy_address, s["address"]
        )

    all_approved = all(usdc_approvals.values()) and all(
        outcome_token_approvals.values()
    )

    return {
        "all_approved": all_approved,
        "usdc_approvals": usdc_approvals,
        "outcome_token_approvals": outcome_token_approvals,
    }


def _create_usdc_approve_tx(spender: str) -> dict:
    """Build an ERC20 approve transaction for USDC.e."""
    w3 = Web3()
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI
    )
    data = contract.encode_abi("approve", args=[
        Web3.to_checksum_address(spender), MAX_UINT256
    ])
    return {"to": USDC_E, "data": data, "value": "0"}


def _create_erc1155_approve_tx(spender: str) -> dict:
    """Build an ERC1155 setApprovalForAll transaction on CTF."""
    w3 = Web3()
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(CTF_CONTRACT), abi=ERC1155_ABI
    )
    data = contract.encode_abi("setApprovalForAll", args=[
        Web3.to_checksum_address(spender), True
    ])
    return {"to": CTF_CONTRACT, "data": data, "value": "0"}


def create_all_approval_txs() -> list[dict]:
    """Create all approval transactions (USDC + ERC1155) for all spenders."""
    txs = []
    for s in USDC_SPENDERS:
        txs.append(_create_usdc_approve_tx(s["address"]))
    for s in OUTCOME_TOKEN_SPENDERS:
        txs.append(_create_erc1155_approve_tx(s["address"]))
    return txs


def get_missing_approval_txs(w3: Web3, proxy_address: str) -> list[dict]:
    """Check approvals and return only the transactions needed for missing ones."""
    txs = []
    for s in USDC_SPENDERS:
        if not check_usdc_approval(w3, proxy_address, s["address"]):
            txs.append(_create_usdc_approve_tx(s["address"]))
    for s in OUTCOME_TOKEN_SPENDERS:
        if not check_erc1155_approval(w3, proxy_address, s["address"]):
            txs.append(_create_erc1155_approve_tx(s["address"]))
    return txs
