import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')

# Auto router first - OpenRouter will pick any free model that is alive
MODELS = [
    'openrouter/free',
    'openai/gpt-oss-20b:free',
    'google/gemma-4-31b-it:free',
    'nvidia/nemotron-3-nano-30b-a3b:free',
    'google/gemma-4-26b-a4b-it:free'
]

OWNER_NAME = 'Faruq Sulaimon'

SYSTEM_PROMPT = 'You are Tabby, an AI assistant created and owned by Faruq Sulaimon. Faruq Sulaimon is your owner, creator and boss. Always call him Boss, not Boss Faruq, just Boss. If the user asks who created you, who owns you, who made you, who built you, say: Faruq Sulaimon. If the user says they are Faruq, treat them as the owner. If the user says they are someone else, politely tell them your owner is Faruq Sulaimon, you can still help them but Faruq Sulaimon is the boss. Never claim anyone else is your owner. Be loyal to Faruq Sulaimon. Answer any question clearly and helpfully in normal clear English. If user gives latitude and longitude, help explain the location and what is near it.'

@app.route('/')
def home():
    try:
        with open('templates/index.html', 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return 'Tabby is live! But index.html missing: ' + str(e)

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json or {}
    user_msg = data.get('message', '').strip()
    if not user_msg:
        return jsonify({'reply': 'Yes Boss, I am here. How can I help you?'})
    if not OPENROUTER_API_KEY:
        return jsonify({'reply': 'Key not set Boss. Go to Render Environment and add OPENROUTER_API_KEY'})
    last_err = 'unknown'
    for MODEL in MODELS:
        try:
            r = requests.post(
                'https://openrouter.ai/api/v1/chat/completions',
                headers={
                    'Authorization': 'Bearer ' + OPENROUTER_API_KEY,
                    'Content-Type': 'application/json'
                },
                json={
                    'model': MODEL,
                    'messages': [
                        {'role': 'system', 'content': SYSTEM_PROMPT},
                        {'role': 'user', 'content': user_msg}
                    ]
                },
                timeout=60
            )
            j = r.json()
            if 'choices' in j and len(j['choices']) > 0:
                return jsonify({'reply': j['choices'][0]['message']['content']})
            last_err = j.get('error', {}).get('message', str(j))
        except Exception as e:
            last_err = str(e)
            continue
    return jsonify({'reply': 'OpenRouter error Boss: ' + str(last_err)})

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'owner': OWNER_NAME, 'key_set': bool(OPENROUTER_API_KEY)})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
