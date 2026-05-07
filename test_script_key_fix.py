path = '/Users/manojsilwal/workspace/gamify_agents_orchestration/zenith-rewards/apps/worker/test_e2e_fincrawler_points.py'
with open(path, 'r') as f:
    code = f.read()

code = code.replace(
    'os.environ["FINCRAWLER_TIMEOUT_SECONDS"] = "180"',
    'os.environ["FINCRAWLER_TIMEOUT_SECONDS"] = "180"\nos.environ["FINCRAWLER_API_KEY"] = "efff6510a96c4e4333895c67f749c514b3fbf4755b30ce6a90e07a95531ec574"'
)

with open(path, 'w') as f:
    f.write(code)
print("Key added")
