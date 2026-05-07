import re
with open('/Users/manojsilwal/workspace/gamify_agents_orchestration/zenith-rewards/apps/worker/fincrawler_client.py', 'r') as f:
    code = f.read()

code = code.replace(
    'headers["Authorization"] = f"Bearer {api_key}"',
    'headers["X-API-Key"] = api_key\n        headers["Authorization"] = f"Bearer {api_key}"'
)

with open('/Users/manojsilwal/workspace/gamify_agents_orchestration/zenith-rewards/apps/worker/fincrawler_client.py', 'w') as f:
    f.write(code)
print("Client patched")
