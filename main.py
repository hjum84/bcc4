import openai
import os
import datetime
import smartsheet
import csv
import io
import threading    # NEW: Imported threading for asynchronous tasks
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template, redirect, url_for, make_response, Response
from functools import wraps
import re
from models import SessionLocal, User

# Load environment variables
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

# Load content summary from file
with open("content_summary.txt", "r", encoding="utf-8") as f:
    content_summary = f.read()
    
# Initialize Flask application
app = Flask(__name__)

# Basic Auth settings
AUTHORIZED_USERNAME = os.getenv("AUTH_USERNAME")  # default: admin
AUTHORIZED_PASSWORD = os.getenv("AUTH_PASSWORD")  # default: password

def check_auth(username, password):
    """Check if a username/password combination is valid."""
    return username == AUTHORIZED_USERNAME and password == AUTHORIZED_PASSWORD

def authenticate():
    """Sends a 401 response that enables basic auth"""
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

# --- Smartsheet Integration Setup ---
SMARTSHEET_ACCESS_TOKEN = os.getenv("SMARTSHEET_ACCESS_TOKEN")
SMARTSHEET_SHEET_ID = os.getenv("SMARTSHEET_SHEET_ID")
SMARTSHEET_TIMESTAMP_COLUMN = os.getenv("SMARTSHEET_TIMESTAMP_COLUMN")
SMARTSHEET_QUESTION_COLUMN = os.getenv("SMARTSHEET_QUESTION_COLUMN")
SMARTSHEET_RESPONSE_COLUMN = os.getenv("SMARTSHEET_RESPONSE_COLUMN")

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
    Adds a new row with the current timestamp, the user's question,
    and the chatbot's reply.
    """
    if not smartsheet_client or not SMARTSHEET_SHEET_ID:
        return

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
    response = smartsheet_client.Sheets.add_rows(SMARTSHEET_SHEET_ID, [new_row])
    return response
# --- End of Smartsheet Integration Setup ---

# Home route: redirect to login page
@app.route('/')
def home():
    return redirect(url_for('login'))

# Registration route (Modified for PostgreSQL)
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        last_name = request.form.get('last_name')
        email = request.form.get('email')
        session = SessionLocal()  # Using SQLAlchemy session
        
        try:
            new_user = User(last_name=last_name, email=email)
            session.add(new_user)
            session.commit()
        except Exception as e:
            session.rollback()
            session.close()
            return "This email is already registered.", 400
        session.close()     
        return redirect(url_for('login'))
    return render_template('register.html')

# Login route (Modified to update visit count using SQLAlchemy)
@app.route('/login', methods=['GET', 'POST'])
def login():
    try:
        if request.method == 'POST':
            last_name = request.form.get('last_name')
            email = request.form.get('email')
            session = SessionLocal()  # Using SQLAlchemy session for login
            user = session.query(User).filter(User.last_name == last_name, User.email == email).first()
            if user:
                user.visit_count += 1
                session.commit()
                session.close()                
                return redirect(url_for('index'))
            else:
                session.close()
                return "You are not registered. Please register the chatbot.", 400
        return render_template('login.html')
    
    except Exception as e:
        return f"An error occurred: {str(e)}", 500

# Chatbot interface route
@app.route('/index')
def index():
    return render_template('index.html')

# Chat endpoint for processing user messages
@app.route('/chat', methods=['POST'])
def chat():
    user_message = request.json.get("message")
    if not user_message:
        return jsonify({"error": "A question is required."}), 400

    # Check quota using cookie
    quota = request.cookies.get('chat_quota')
    if quota:
        quota = int(quota)
    else:
        quota = 0

    if quota >= 300:
        return jsonify({"reply": "You have used all your quota for today."}), 200

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"You are an assistant that only answers questions based on the following content: {content_summary}"},
                {"role": "user", "content": user_message}
            ],
            max_tokens=500
        )
        
        chatbot_reply = response['choices'][0]['message']['content'].strip()

        # Truncate response to 300 words if necessary
        words = chatbot_reply.split()
        if len(words) > 500:
            truncated_text = ' '.join(words[:300])
            end_index = chatbot_reply.find(truncated_text) + len(truncated_text)
            rest_text = chatbot_reply[end_index:]
            sentence_end = re.search(r'[.?!]', rest_text)
            if sentence_end:
                chatbot_reply = chatbot_reply[:end_index + sentence_end.end()]
            else:
                chatbot_reply = truncated_text

        # NEW: Record conversation in Smartsheet asynchronously to avoid slowing down the response.
        def record_smartsheet_async(user_question, chatbot_reply):
            try:
                record_in_smartsheet(user_question, chatbot_reply)
            except Exception as smex:
                print("Error recording in Smartsheet:", smex)

        threading.Thread(target=record_smartsheet_async, args=(user_message, chatbot_reply)).start()
        # --- Change Made Here: Wrapped the record_in_smartsheet call in a background thread.

        # Create the response object and update the chat quota cookie
        response_obj = make_response(jsonify({"reply": chatbot_reply}))
        quota += 1
        expires = datetime.datetime.now() + datetime.timedelta(days=1)
        response_obj.set_cookie('chat_quota', str(quota), expires=expires)

        return response_obj

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Delete Registration Route
@app.route('/delete_registration', methods=['GET', 'POST'])
@requires_auth
def delete_registration():
    if request.method == 'GET':
        return render_template('delete_registration.html')
    
    data = request.get_json(silent=True)
    if data is None:
        data = request.form

    email = data.get('email')
    last_name = data.get('last_name')

    if not email or not last_name:
        return "Email and Last Name are required to delete registration.", 400

    email = email.strip()
    last_name = last_name.strip()

    session = SessionLocal()
    try:
        user = session.query(User).filter(User.email == email, User.last_name == last_name).first()
        if user:
            session.delete(user)
            session.commit()
            message = "Your registration has been successfully removed."
            status_code = 200
        else:
            message = "User not found. No registration to remove."
            status_code = 404
    except Exception as e:
        session.rollback()
        message = f"Error during deletion: {str(e)}"
        status_code = 500
    finally:
        session.close()

    return message, status_code

# Export Users Route (CSV Export, Protected by Authentication)
@app.route('/export_users', methods=['GET'])
@requires_auth
def export_users():
    session_db = SessionLocal()
    users = session_db.query(User).all()
    session_db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Last Name', 'Email', 'Visit Count'])
    for user in users:
        writer.writerow([user.last_name, user.email, user.visit_count])
    csv_content = output.getvalue()
    output.close()

    response_csv = make_response(csv_content)
    response_csv.headers['Content-Disposition'] = 'attachment; filename=users.csv'
    response_csv.headers['Content-Type'] = 'text/csv'
    return response_csv

# Protected route to view registered users
@app.route('/users')
@requires_auth
def show_users():
    session = SessionLocal()
    users = session.query(User).all()
    user_list = [{"last_name": user.last_name, "email": user.email, "visit_count": user.visit_count} for user in users]
    session.close()
    return jsonify(user_list)

# Export Page Route: renders export.html
@app.route('/export')
@requires_auth
def export_page():
    return render_template('export.html')

# Run the application
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
