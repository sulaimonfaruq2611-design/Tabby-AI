import os
import requests
from flask import Flask, request, jsonify
app = Flask(__name__)
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
MODEL = 'meta-llama/llama-3.1-8b-instruct:free'
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
        return jsonify({'reply': 'Ask me something na'})
    if not OPENROUTER_API_KEY:
        return jsonify({'reply': 'Key not set. Go to Render Environment add OPENROUTER_API_KEY'})
    try:
        r = requests.post('https://openrouter.ai/api/v1/chat/completions', headers={'Authorization': 'Bearer ' + OPENROUTER_API_KEY, 'Content-Type': 'application/json', 'HTTP-Referer': 'https://tabby-ai.onrender.com', 'X-Title': 'Tabby AI'}, json={'model': MODEL, 'messages': [{'role': 'system', 'content': 'You are Tabby, a friendly smart AI assistant from Nigeria. Answer any question clearly, helpfully, chill friendly tone. Use small Nigerian Pidgin sometimes but keep answers clear.'}, {'role': 'user', 'content': user_msg}]}, timeout=60)
        j = r.json()
        if 'choices' in j and len(j['choices']) > 0:
            return jsonify({'reply': j['choices'][0]['message']['content']})
        err = j.get('error', {}).get('message', str(j))
        return jsonify({'reply': 'OpenRouter error: ' + str(err)})
    except Exception as e:
        return jsonify({'reply': 'Error: ' + str(e)})
@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'key_set': bool(OPENROUTER_API_KEY), 'model': MODEL})
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
