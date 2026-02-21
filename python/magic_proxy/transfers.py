"""USDC.e transfer transaction builder."""

from web3 import Web3

from .config import ERC20_ABI, USDC_E


def create_usdc_transfer_tx(recipient: str, amount: int) -> dict:
    """
    Build an ERC20 transfer transaction for USDC.e.

    Args:
        recipient: The recipient address.
        amount: Amount in raw units (6 decimals). e.g. 1_000_000 = 1 USDC.

    Returns:
        Transaction dict {to, data, value} ready for relay execution.
    """
    w3 = Web3()
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI
    )
    data = contract.encode_abi("transfer", args=[
        Web3.to_checksum_address(recipient), amount
    ])
    return {"to": USDC_E, "data": data, "value": "0"}
