import os
required=["BOT_TOKEN","API_ID","API_HASH"]
missing=[x for x in required if not os.getenv(x)]
print("Missing:", ", ".join(missing) if missing else "none")
