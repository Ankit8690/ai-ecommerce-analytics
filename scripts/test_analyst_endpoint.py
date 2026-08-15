import os, json, requests

API_BASE = os.getenv('DASHBOARD_API_URL', 'http://127.0.0.1:8000')

questions = [
    "What are the total orders and GMV?",
    "Give me least reviewed products"
]
for q in questions:
    try:
        resp = requests.post(f"{API_BASE}/api/analyst", json={"question": q}, timeout=30)
        print(f"\nQuestion: {q}\nStatus: {resp.status_code}")
        if resp.ok:
            data = resp.json()
            print("Answer snippet:", data.get('answer','')[:200])
            if data.get('sql_used'):
                print('SQL used:', data['sql_used'][:200])
        else:
            print('Error:', resp.text)
    except Exception as e:
        print('Exception:', e)
