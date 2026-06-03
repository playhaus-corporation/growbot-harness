# anchor_check.py  (repo root)
import json, cert_verify
cert = json.load(open("anchored_certificate.json"))
print(cert_verify.verify_anchor(cert))