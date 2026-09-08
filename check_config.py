import os
required=["BOT_TOKEN"]
optional=["ADMIN_IDS","FORCE_JOIN_CHANNELS","SUPPORT_URL","CARD_NUMBER","CARD_NUMBER_NAME","PAYMENT_ADMIN_IDS","GAME_BOT_USERNAME","WEB_ADMIN_PASSWORD","DATA_DIR","PORT"]
missing=[x for x in required if not os.getenv(x)]
print("Missing:", ", ".join(missing) if missing else "none")
print("Optional configured:", ", ".join(x for x in optional if os.getenv(x)) or "none")
