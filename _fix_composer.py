"""
Fix the mangled _build_reply_user function in composer.py
"""
import sys
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

content = open('composer.py', encoding='utf-8').read()
lines = content.split('\n')

good_function = [
    "    def _build_reply_user(self, category, merchant, trigger, customer, conv_hist, merchant_msg, intent):",
    "        identity = merchant.get('identity', {})",
    "        if customer and intent == 'customer_reply':",
    "            cid        = customer.get('identity', {})",
    "            rel        = customer.get('relationship', {})",
    "            prefs      = customer.get('preferences', {})",
    "            pref_slots = prefs.get('preferred_slots', [])",
    "            cust_name  = cid.get('name', 'the customer')",
    "            lang       = cid.get('language_pref', 'en')",
    "            cust_block = (",
    "                '\\n=== CUSTOMER (reply directly to them) ===\\n'",
    "                + f'name: {cust_name}  | language: {lang}\\n'",
    "                + f'preferred_slots: {pref_slots}\\n'",
    "                + f'CRITICAL: Address {cust_name} BY NAME. Confirm booking. You ARE the merchant voice.'",
    "            )",
    "        else:",
    "            cust_block = ''",
    "        role = 'CUSTOMER JUST SAID' if intent == 'customer_reply' else 'MERCHANT JUST SAID'",
    "        offers_active = [o.get('title') for o in merchant.get('offers', []) if o.get('status') == 'active'] or 'none'",
    "        trg_kind = trigger.get('kind', '') if trigger else 'unknown'",
    "        city     = identity.get('city', '')",
    "        locality = identity.get('locality', '')",
    "        mname    = identity.get('name', '')",
    "        return (",
    "            '=== CONVERSATION SO FAR ===\\n'",
    "            + self._history_block(conv_hist) + '\\n\\n'",
    "            + f'=== {role} ===\\n'",
    "            + f'\"{merchant_msg}\"\\n\\n'",
    "            + '=== CONTEXT (quick ref) ===\\n'",
    "            + f'merchant: {mname}  | city: {city}  | locality: {locality}\\n'",
    "            + f'active_offers: {offers_active}\\n'",
    "            + f'trigger_kind: {trg_kind}'",
    "            + cust_block + '\\n\\n'",
    "            + 'Reply now. Output ONLY the JSON object.'",
    "        )",
    "",
]

# Replace lines[255:257] with the good function
lines[255:257] = good_function

new_content = '\n'.join(lines)
open('composer.py', 'w', encoding='utf-8').write(new_content)
print("Done! composer.py fixed.")
