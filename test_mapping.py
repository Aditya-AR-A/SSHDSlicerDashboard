import json

with open("config/user_mapping.json") as f:
    mapping = json.load(f)

username = "SSHD-Slicer1"
canonical = username
for k, v in mapping.items():
    if k.lower() == username.lower():
        canonical = v
        break

print(canonical)
