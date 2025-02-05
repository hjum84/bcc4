import sqlite3
import openai
import os
import datetime
import smartsheet
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template, redirect, url_for, make_response, Response
from functools import wraps

# 환경 변수 로드 / Load environment variables
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

# content_summary.txt에서 요약된 내용을 로드 /Load content summary from file
with open("content_summary.txt", "r", encoding="utf-8") as f:
    content_summary = f.read()
    
# Flask 애플리케이션 초기화 /Initialize Flask application
app = Flask(__name__)

# Basic Auth 인증 설정 /Basic Auth settings
AUTHORIZED_USERNAME = os.getenv("AUTH_USERNAME")  # default: admin
AUTHORIZED_PASSWORD = os.getenv("AUTH_PASSWORD")  # default: password

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

# 데이터베이스 초기화 / Initialize database
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

# --- NEW: Smartsheet Integration Setup ---
# Expected environment variables:
#   SMARTSHEET_ACCESS_TOKEN - Your Smartsheet API access token
#   SMARTSHEET_SHEET_ID - The ID of the Smartsheet to add rows to
#   SMARTSHEET_TIMESTAMP_COLUMN - Column ID for the timestamp
#   SMARTSHEET_QUESTION_COLUMN  - Column ID for the user question
#   SMARTSHEET_RESPONSE_COLUMN  - Column ID for the chatbot response
SMARTSHEET_ACCESS_TOKEN = os.getenv("SMARTSHEET_ACCESS_TOKEN")
SMARTSHEET_SHEET_ID = os.getenv("SMARTSHEET_SHEET_ID")
SMARTSHEET_TIMESTAMP_COLUMN = os.getenv("SMARTSHEET_TIMESTAMP_COLUMN")
SMARTSHEET_QUESTION_COLUMN = os.getenv("SMARTSHEET_QUESTION_COLUMN")
SMARTSHEET_RESPONSE_COLUMN = os.getenv("SMARTSHEET_RESPONSE_COLUMN")

# Convert column IDs to integers if provided
if SMARTSHEET_TIMESTAMP_COLUMN:
    SMARTSHEET_TIMESTAMP_COLUMN = int(SMARTSHEET_TIMESTAMP_COLUMN)
if SMARTSHEET_QUESTION_COLUMN:
    SMARTSHEET_QUESTION_COLUMN = int(SMARTSHEET_QUESTION_COLUMN)
if SMARTSHEET_RESPONSE_COLUMN:
    SMARTSHEET_RESPONSE_COLUMN = int(SMARTSHEET_RESPONSE_COLUMN)

smartsheet_client = None
if SMARTSHEET_ACCESS_TOKEN:
    smartsheet_client = smartsheet.Smartsheet(SMARTSHEET_ACCESS_TOKEN)

def record_in_smartsheet(user_question, chatbot_reply):
    """
    Record the user's question and chatbot response in Smartsheet.
    A new row is added with the current timestamp, the user's question,
    and the chatbot's reply.
    """
    if not smartsheet_client or not SMARTSHEET_SHEET_ID:
        return

    # Create a new row object
    new_row = smartsheet.models.Row()
    new_row.to_top = True
    new_row.cells = [
        {
            'column_id': SMARTSHEET_TIMESTAMP_COLUMN,
            'value': datetime.datetime.now().isoformat()
        },
        {
            'column_id': SMARTSHEET_QUESTION_COLUMN,
            'value': user_question
        },
        {
            'column_id': SMARTSHEET_RESPONSE_COLUMN,
            'value': chatbot_reply
        }
    ]
    # Add the row to the specified Smartsheet
    response = smartsheet_client.Sheets.add_rows(SMARTSHEET_SHEET_ID, [new_row])
    return response
# --- End of Smartsheet Integration Setup ---

# 홈 경로에서 login.html 제공 (서버 실행 시 처음 표시되는 페이지) / Home route: redirect to login page
@app.route('/')
def home():
    return redirect(url_for('login'))  # 기본적으로 로그인 페이지로 리디렉션

# 회원 등록 페이지 / Registration route
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

# 로그인 페이지 /Login route
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

# 챗봇 페이지 (로그인 성공 시 이동) / Chatbot interface route
@app.route('/index')
def index():
    return render_template('index.html')

# 사용자 메시지에 대한 GPT 응답을 제공하는 엔드포인트 / Chat endpoint for processing user messages
@app.route('/chat', methods=['POST'])
def chat():
    user_message = request.json.get("message")
    if not user_message:
        return jsonify({"error": "A question is required."}), 400

    # 쿠키에서 쿼타 확인 / Check quota using cookie
    quota = request.cookies.get('chat_quota')
    if quota:
        quota = int(quota)
    else:
        quota = 0

    if quota >= 30:
        return jsonify({"reply": "You have used all your quota for today."}), 200

    # GPT-3.5-turbo를 사용해 질문에 응답
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": f"You are an assistant that only answers questions based on the following content: {content_summary}"},
                {"role": "user", "content": user_message}
            ],
            max_tokens=500
        )
        
        # GPT에서 응답을 받음
        chatbot_reply = response['choices'][0]['message']['content'].strip()

        # 응답을 300단어 이내로 요약하기 / Truncate response to 200 words if necessary
        words = chatbot_reply.split()
        if len(words) > 300:
             # Get the first 300 words
            truncated_text = ' '.join(words[:300])
            # Determine the end position of this truncated text in the original reply
            end_index = chatbot_reply.find(truncated_text) + len(truncated_text)
            # Get the rest of the text after the truncated portion
            rest_text = chatbot_reply[end_index:]
            # Search for the first sentence-ending punctuation in the rest of the text
            sentence_end = re.search(r'[.?!]', rest_text)
            if sentence_end:
                # Extend the reply to include up to the end of the sentence
                chatbot_reply = chatbot_reply[:end_index + sentence_end.end()]
            else:
                chatbot_reply = truncated_text
        # --- End of Updated Truncation Logic ---
        
         # --- NEW: Record the conversation in Smartsheet ---
        try:
            record_in_smartsheet(user_message, chatbot_reply)
        except Exception as smex:
            print("Error recording in Smartsheet:", smex)
        # --- End of Smartsheet recording ---

        # 응답 객체 생성
        response = make_response(jsonify({"reply": chatbot_reply}))

        # 쿠키 업데이트 (질문 횟수 증가)
        quota += 1
        expires = datetime.datetime.now() + datetime.timedelta(days=1)
        response.set_cookie('chat_quota', str(quota), expires=expires)

        return response

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 등록된 사용자 보기 페이지 (인증 필요) / Protected route to view registered users (requires Basic Auth)
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
