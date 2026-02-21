"""CREATE2 proxy wallet address derivation for Polymarket Magic Link wallets."""

from eth_utils import keccak, to_checksum_address

from .config import PROXY_FACTORY, PROXY_INIT_CODE_HASH


def _encode_packed_address(address: str) -> bytes:
    """Solidity abi.encodePacked(address) = 20 raw bytes, no padding."""
    return bytes.fromhex(address[2:].lower())


def derive_proxy_address(eoa_address: str) -> str:
    """
    Derive the deterministic proxy wallet address from an EOA address using CREATE2.

    Mirrors the TypeScript implementation:
        getCreate2Address({
            bytecodeHash: PROXY_INIT_CODE_HASH,
            from: PROXY_FACTORY,
            salt: keccak256(encodePacked(["address"], [eoaAddress])),
        })

    Args:
        eoa_address: The EOA (externally owned account) address.

    Returns:
        The checksummed proxy wallet address.
    """
    # salt = keccak256(encodePacked(["address"], [eoa]))
    # encodePacked for address = raw 20 bytes
    salt = keccak(_encode_packed_address(eoa_address))

    # CREATE2: keccak256(0xff ++ factory ++ salt ++ init_code_hash)[12:]
    factory_bytes = bytes.fromhex(PROXY_FACTORY[2:])
    create2_input = b"\xff" + factory_bytes + salt + PROXY_INIT_CODE_HASH
    proxy_bytes = keccak(create2_input)[12:]

    return to_checksum_address(proxy_bytes)
