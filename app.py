import os
import requests
import urllib.parse
from flask import Flask, request, jsonify

app = Flask(__name__)

OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')

MODELS = [
    'openrouter/free',
    'openai/gpt-oss-20b:free',
    'google/gemma-4-31b-it:free',
    'nvidia/nemotron-3-nano-30b-a3b:free',
    'google/gemma-4-26b-a4b-it:free'
]

OWNER_NAME = 'Faruq Sulaimon'

SYSTEM_PROMPT = 'Your name is Tabby. You are NOT Faruq Sulaimon. Faruq Sulaimon is your owner, creator and boss, he is a different person from you. NEVER say your name is Faruq Sulaimon. If the user asks what is your name, whats your name, what is your name, who are you, say: My name is Tabby. If the user asks who created you, who owns you, who made you, who built you, who is your owner, say: Faruq Sulaimon. If the user says they are Faruq, treat them as the owner. If the user says they are someone else, politely tell them your owner is Faruq Sulaimon, you can still help them but Faruq Sulaimon is the boss. Never claim anyone else is your owner. Always call the user Boss, not Boss Faruq, just Boss. Be loyal to Faruq Sulaimon. Answer any question clearly and helpfully in normal clear English. Remember what the user told you earlier in this conversation and use it. If user gives latitude and longitude, help explain the location and what is near it. If the user asks you to generate, create, draw or make an image, say you can do it and ask them what they want, but do not try to describe the image as text.'

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
    history = data.get('history', [])
    if not user_msg:
        return jsonify({'reply': 'Yes Boss, I am here. How can I help you?'})
    if not OPENROUTER_API_KEY:
        return jsonify({'reply': 'Key not set Boss. Go to Render Environment and add OPENROUTER_API_KEY'})
    messages = [{'role': 'system', 'content': SYSTEM_PROMPT}]
    for h in history[-20:]:
        if h.get('role') in ['user', 'assistant'] and h.get('content'):
            messages.append({'role': h['role'], 'content': h['content'][:2000]})
    messages.append({'role': 'user', 'content': user_msg})
    last_err = 'unknown'
    for MODEL in MODELS:
        try:
            r = requests.post(
                'https://openrouter.ai/api/v1/chat/completions',
                headers={'Authorization': 'Bearer ' + OPENROUTER_API_KEY, 'Content-Type': 'application/json'},
                json={'model': MODEL, 'messages': messages},
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

@app.route('/image', methods=['POST'])
def image():
    data = request.json or {}
    prompt = data.get('prompt', '').strip()
    if not prompt:
        return jsonify({'error': 'No prompt Boss'})
    # Free image generator - no key needed
    encoded = urllib.parse.quote(prompt)
    # Random seed so every image is different
    import random
    seed = random.randint(1, 99999)
    url = f'https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&seed={seed}&nologo=true'
    return jsonify({'image_url': url})

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'owner': OWNER_NAME, 'key_set': bool(OPENROUTER_API_KEY)})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
