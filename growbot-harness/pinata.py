import json
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
    with open("examples/story_registration.json", "r") as f:
        cert = json.load(f)
    cid = pin_json(cert, "examples/story_registration.json")
    print(cid)
    print(f"https://gateway.pinata.cloud/ipfs/{cid}")