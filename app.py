import os
import requests
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "meta-llama/llama-3.1-8b-instruct:free"

HTML = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tabby AI</title>
<style>
body{font-family:Arial;background:#0f172a;color:white;padding:15px;max-width:700px;margin:auto}
h2{text-align:center}
#chat{height:55vh;overflow-y:auto;background:#020617;padding:15px;border-radius:12px;margin-bottom:12px}
.msg{margin-bottom:12px;line-height:1.5}
.user{color:#93c5fd}
.tabby{color:#86efac}
input{width:78%;padding:12px;border-radius:8px;border:none;outline:none}
button{padding:12px 16px;border:none;border-radius:8px;background:#3b82f6;color:white;font-weight:bold;cursor:pointer}
</style>
</head>
<body>
<h2>🐱 Tabby AI</h2>
<p style="text-align:center;color:#94a3b8">Ask me anything</p>
<div id="chat"><div class="msg tabby"><b>Tabby:</b> Yo! I'm active now. Ask me anything — business, school, code, ideas, anything.</div></div>
<div style="display:flex;gap:8px">
<input id="msg" placeholder="Type your question..." onkeydown="if(event.key==='Enter')send()">
<button onclick="send()">Send</button>
</div>
<script>
async function send(){
  let inp = document.getElementById('msg');
  let m = inp.value.trim
