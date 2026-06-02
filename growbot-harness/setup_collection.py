import os
from dotenv import load_dotenv
from web3 import Web3  # pyright: ignore[reportMissingImports]

from story_sdk_compat import apply_story_sdk_compat

apply_story_sdk_compat()

from story_protocol_python_sdk import StoryClient  # noqa: E402  # pyright: ignore[reportMissingImports]

load_dotenv()
web3 = Web3(Web3.HTTPProvider(os.environ["STORY_RPC"]))
account = web3.eth.account.from_key(os.environ["STORY_PRIVATE_KEY"])
story_client = StoryClient(web3, account, int(os.environ["STORY_CHAIN_ID"]))  # 1315

new_collection = story_client.NFTClient.create_nft_collection(
    name="growbot",
    symbol="GROW",
    is_public_minting=True,
    mint_open=True,
    mint_fee_recipient="0x0000000000000000000000000000000000000000",
    contract_uri="",
)
print("tx:", new_collection["tx_hash"])
print("NFT_CONTRACT =", new_collection["nft_contract"])