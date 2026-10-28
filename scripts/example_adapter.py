"""Offline protocol example ONLY. Replace with a verified provider adapter before use."""
import json,sys
packet=json.load(sys.stdin)
# Deliberately refuses to send anything; this is not a translation provider.
json.dump({'status':'not_sent'},sys.stdout)
