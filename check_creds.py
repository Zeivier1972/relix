from dotenv import load_dotenv
load_dotenv(override=True)
import os
u = os.getenv("INSTAGRAM_USERNAME", "")
p = os.getenv("INSTAGRAM_PASSWORD", "")
print(f"Username: [{u}]  len={len(u)}")
print(f"Password: [{p}]  len={len(p)}")
