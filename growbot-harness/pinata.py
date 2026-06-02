import os

import requests
from dotenv import load_dotenv

load_dotenv()

def pin_json(obj, name):
    r = requests.post(
        "https://api.pinata.cloud/pinning/pinJSONToIPFS",
        headers={"Authorization": f"Bearer {os.environ['PINATA_JWT']}"},
        json={"pinataContent": obj, "pinataMetadata": {"name": name}},
        timeout=30,
    )
    r.raise_for_status()
    return "ipfs://" + r.json()["IpfsHash"]

if __name__ == "__main__":
    # Smoke test: needs PINATA_JWT + network; pins a trivial object, spends nothing.
    cid = pin_json({"growbot": "pinata smoke test"}, "growbot-pinata-smoke-test.json")
    print(cid)
    print(f"https://gateway.pinata.cloud/ipfs/{cid}")