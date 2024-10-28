import sqlite3
import openai
import os
import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template, redirect, url_for, make_response, Response
from functools import wraps

# 환경 변수 로드
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

# Flask 애플리케이션 초기화
app = Flask(__name__)

# Basic Auth 인증 설정
AUTHORIZED_USERNAME = os.getenv("AUTH_USERNAME")  # 기본값: admin
AUTHORIZED_PASSWORD = os.getenv("AUTH_PASSWORD")  # 기본값: password

def check_auth(username, password):
    """지정된 사용자 인증을 확인합니다."""
    return username == AUTHORIZED_USERNAME and password == AUTHORIZED_PASSWORD

def authenticate():
    """401 응답을 반환하여 기본 인증을 요청합니다."""
    return Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'}
    )

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# 데이터베이스 초기화
def init_db():
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            last_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE
        )
    ''')
    conn.commit()
    conn.close()

# 서버 시작 시 데이터베이스 초기화
init_db()

# 홈 경로에서 login.html 제공 (서버 실행 시 처음 표시되는 페이지)
@app.route('/')
def home():
    return redirect(url_for('login'))  # 기본적으로 로그인 페이지로 리디렉션

# 회원 등록 페이지
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        last_name = request.form.get('last_name')
        email = request.form.get('email')
        
        # 데이터베이스에 사용자 정보 저장
        try:
            conn = sqlite3.connect('users.db')
            cursor = conn.cursor()
            cursor.execute('INSERT INTO users (last_name, email) VALUES (?, ?)', (last_name, email))
            conn.commit()
            conn.close()
        except sqlite3.IntegrityError:
            return "This email is already registered.", 400
        
        return redirect(url_for('login'))  # 회원 등록 후 로그인 페이지로 리디렉션
    return render_template('register.html')

# 로그인 페이지
@app.route('/login', methods=['GET', 'POST'])
def login():
    try:
        if request.method == 'POST':
            last_name = request.form.get('last_name')
            email = request.form.get('email')
            
            # 데이터베이스에서 사용자 정보 확인
            conn = sqlite3.connect('users.db')
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE last_name = ? AND email = ?', (last_name, email))
            user = cursor.fetchone()
            conn.close()
            
            if user:
                return redirect(url_for('index'))  # 로그인 성공 시 챗봇 페이지로 리디렉션
            else:
                return "You are not registered. Please register the chatbot.", 400
        return render_template('login.html')
    
    except Exception as e:
        return f"An error occurred: {str(e)}", 500

# 챗봇 페이지 (로그인 성공 시 이동)
@app.route('/index')
def index():
    return render_template('index.html')

# 사용자 메시지에 대한 GPT 응답을 제공하는 엔드포인트
@app.route('/chat', methods=['POST'])
def chat():
    user_message = request.json.get("message")
    if not user_message:
        return jsonify({"error": "A question is required."}), 400

    # 쿠키에서 쿼타 확인
    quota = request.cookies.get('chat_quota')
    if quota:
        quota = int(quota)
    else:
        quota = 0

    if quota >= 3:
        return jsonify({"reply": "You have used all your quota for today."}), 200

    # GPT-3.5-turbo를 사용해 질문에 응답
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an assistant that only answers questions related to BCC AI."},
                {"role": "user", "content": user_message}
            ],
            max_tokens=500
        )
        
        # GPT에서 응답을 받음
        chatbot_reply = response['choices'][0]['message']['content'].strip()

        # 응답을 200단어 이내로 요약하기
        words = chatbot_reply.split()
        if len(words) > 200:
            chatbot_reply = ' '.join(words[:200])

        # 응답 객체 생성
        response = make_response(jsonify({"reply": chatbot_reply}))

        # 쿠키 업데이트 (질문 횟수 증가)
        quota += 1
        expires = datetime.datetime.now() + datetime.timedelta(days=1)
        response.set_cookie('chat_quota', str(quota), expires=expires)

        return response

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 등록된 사용자 보기 페이지 (인증 필요)
@app.route('/users')
@requires_auth
def show_users():
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT last_name, email FROM users')
    users = cursor.fetchall()
    conn.close()
    return jsonify(users)  # 모든 등록된 사용자 정보를 JSON 형태로 반환

# Render 배포용 포트 설정
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
