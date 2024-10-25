from flask import Flask, request, jsonify, render_template, redirect, url_for, make_response
import openai
import os
from dotenv import load_dotenv
import datetime

# 환경 변수 로드
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

# Flask 애플리케이션 초기화
app = Flask(__name__)

# 사용자 정보 저장용 딕셔너리
users = {}

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
        
        # 이미 등록된 사용자인지 확인
        if email in users:
            return "This email is already registered.", 400
        
        # 사용자를 딕셔너리에 저장
        users[email] = last_name
        return redirect(url_for('login'))  # 회원 등록 후 로그인 페이지로 리디렉션
    return render_template('register.html')

# 로그인 페이지
@app.route('/login', methods=['GET', 'POST'])
def login():
    try:
        if request.method == 'POST':
            last_name = request.form.get('last_name')
            email = request.form.get('email')
            
            # 이메일과 성이 맞는지 확인
            if email in users and users[email] == last_name:
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
            max_tokens=500  # 충분한 응답을 얻기 위해 500 토큰 정도로 설정
        )
        
        # GPT에서 응답을 받음
        chatbot_reply = response['choices'][0]['message']['content'].strip()

        # 응답을 200단어 이내로 요약하기
        words = chatbot_reply.split()
        if len(words) > 200:
            chatbot_reply = ' '.join(words[:200])  # 첫 200단어만 가져와서 잘라냄

        # 응답 객체 생성
        response = make_response(jsonify({"reply": chatbot_reply}))

        # 쿠키 업데이트 (질문 횟수 증가)
        quota += 1
        expires = datetime.datetime.now() + datetime.timedelta(days=1)
        response.set_cookie('chat_quota', str(quota), expires=expires)

        return response

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
