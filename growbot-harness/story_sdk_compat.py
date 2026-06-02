from eth_typing import HexStr
import ens.ens as ens_module


def apply_story_sdk_compat() -> None:
    """Restore the HexStr export expected by story-protocol-python-sdk."""
    if not hasattr(ens_module, "HexStr"):
        setattr(ens_module, "HexStr", HexStr)
