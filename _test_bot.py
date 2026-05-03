import json, urllib.request, urllib.error

BASE = 'http://127.0.0.1:9090'

def post(path, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(BASE + path, data=body, headers={'Content-Type': 'application/json'})
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())

def section(title):
    print()
    print("=" * 55)
    print(" " + title)
    print("=" * 55)

def check(label, condition, detail=""):
    status = "[PASS]" if condition else "[FAIL]"
    print(status, label, "|", detail)

section("1. CONTEXT VERSION FIX (stale_version bug)")
post('/v1/teardown', {})

ctx = {
    'scope': 'merchant', 'context_id': 'm_t1', 'version': 1,
    'payload': {
        'identity': {'name': 'Test Clinic', 'owner_first_name': 'Rahul',
                     'city': 'Mumbai', 'locality': 'Bandra', 'languages': ['en']},
        'category_slug': 'dentists',
        'performance': {'ctr': 0.03, 'calls': 120, 'views': 4000},
        'offers': [{'title': 'Dental Checkup Rs 299', 'status': 'active'}],
        'customer_aggregate': {'lapsed_180d_plus': 45}
    },
    'delivered_at': '2026-01-01T00:00:00Z'
}

r1 = post('/v1/context', ctx)
check("Push v1 accepted", r1.get('accepted') == True)

# Push same version again (was the stale_version bug)
r2 = post('/v1/context', ctx)
check("Push v1 again accepted (THE BUG FIX)", r2.get('accepted') == True, str(r2))

# Push older version (should reject)
r3 = post('/v1/context', {**ctx, 'version': 0, 'payload': {}})
check("Push v0 rejected", r3.get('accepted') == False, "reason: " + str(r3.get('reason')))

section("2. AUTO-REPLY FLOW: wait(1h) -> wait(24h) -> end")
auto = 'Thank you for contacting us! Our team will respond shortly.'
expected_actions = ['wait', 'wait', 'end']
all_ok = True
for i in range(1, 5):
    r = post('/v1/reply', {
        'conversation_id': 'conv_ar', 'merchant_id': 'm_t1', 'customer_id': None,
        'from_role': 'merchant', 'message': auto,
        'received_at': '2026-01-01T00:00:00Z', 'turn_number': i
    })
    action = r.get('action')
    wait   = r.get('wait_seconds', '-')
    expected = expected_actions[i-1] if i <= len(expected_actions) else 'end'
    ok = action == expected
    if not ok:
        all_ok = False
    print("  Turn", i, ":", action, "wait=" + str(wait) + "s", "(expected " + expected + ")", "OK" if ok else "FAIL")
    if action == 'end':
        break
check("Auto-reply flow correct", all_ok)

section("3. STOP / HOSTILE HANDLING")
post('/v1/teardown', {})
r = post('/v1/reply', {
    'conversation_id': 'cv1', 'merchant_id': 'm_t1', 'customer_id': None,
    'from_role': 'merchant', 'message': 'STOP messaging me!',
    'received_at': '2026-01-01T00:00:00Z', 'turn_number': 1
})
check("STOP -> action=end", r.get('action') == 'end', r.get('action'))

section("4. COMMITMENT / INTENT TRANSITION")
post('/v1/teardown', {})
r = post('/v1/reply', {
    'conversation_id': 'cv2', 'merchant_id': 'm_t1', 'customer_id': None,
    'from_role': 'merchant', 'message': 'Ok lets do it. Yes proceed.',
    'received_at': '2026-01-01T00:00:00Z', 'turn_number': 1
})
body = r.get('body', '')
action_words = ['done', 'draft', 'confirm', 'proceed', 'execution', 'next']
qualify_words = ['would you', 'do you', 'can you tell', 'what if', 'how about']
is_action_mode = any(w in body.lower() for w in action_words)
is_qualifying  = any(w in body.lower() for w in qualify_words)
check("Commitment -> execution mode, not re-qualifying", is_action_mode and not is_qualifying, body[:80])

section("5. CUSTOMER SLOT PICK")
post('/v1/teardown', {})
r = post('/v1/reply', {
    'conversation_id': 'cv3', 'merchant_id': 'm_t1', 'customer_id': 'c1',
    'from_role': 'customer', 'message': 'Yes please book me for Wed 5 Nov 6pm',
    'received_at': '2026-01-01T00:00:00Z', 'turn_number': 1
})
check("Customer slot pick -> action=send", r.get('action') == 'send', r.get('action'))
check("Customer body is customer-addressed", len(r.get('body','')) > 20, r.get('body','')[:100])

section("6. SCHEMA COMPLIANCE")
r = post('/v1/teardown', {})
check("Teardown works", r.get('status') == 'wiped')
import urllib.request as _ur
hzr = json.loads(_ur.urlopen(BASE + '/v1/healthz', timeout=5).read())
check("Healthz ok", hzr.get('status') == 'ok')
mdr = json.loads(_ur.urlopen(BASE + '/v1/metadata', timeout=5).read())
check("Metadata has team_name", bool(mdr.get('team_name')))

print()
print("=" * 55)
print("  ALL TESTS COMPLETE")
print("=" * 55)
