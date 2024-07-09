import os
import base64
from datetime import datetime, timedelta
import re
import math
from flask import Flask, redirect, request, url_for, session, render_template, send_file, jsonify
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import io
import zipfile
import time
import json
from flask import jsonify

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Generate a random secret key for each session

# OAuth 2.0 configuration
CLIENT_SECRETS_FILE = "client_secret.json"
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def create_download_folder(folder_name, search_query):
    if folder_name:
        return folder_name
    else:
        sanitized_query = re.sub(r'[^\w\-_\. ]', '_', search_query)
        return f"{sanitized_query}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

def get_unique_folder_name(base_folder_name):
    folder_name = base_folder_name
    counter = 1
    while os.path.exists(folder_name):
        folder_name = f"{base_folder_name}_{counter}"
        counter += 1
    return folder_name

def get_unique_filename(file_name, existing_files):
    base_name, extension = os.path.splitext(file_name)
    counter = 1
    new_name = file_name
    
    while new_name in existing_files:
        new_name = f"{base_name}_{counter}{extension}"
        counter += 1
    
    return new_name

def get_label_id(service, label_name):
    try:
        results = service.users().labels().list(userId='me').execute()
        labels = results.get('labels', [])
        
        for label in labels:
            if label['name'].lower() == label_name.lower():
                return label['id']
        
        return None
    except Exception as e:
        print(f"An error occurred while getting label ID: {e}")
        return None

def search_emails(service, query, label_ids=None, page_token=None, max_results=None):
    try:
        params = {
            'userId': 'me',
            'q': query
        }
        if label_ids:
            params['labelIds'] = label_ids
        if page_token:
            params['pageToken'] = page_token
        if max_results:
            params['maxResults'] = max_results

        results = service.users().messages().list(**params).execute()
        return results
    except Exception as e:
        print(f"An error occurred while searching emails: {e}")
        return {}

def get_attachment(service, msg_id, attachment_id):
    try:
        attachment = service.users().messages().attachments().get(userId='me', messageId=msg_id, id=attachment_id).execute()
        file_data = base64.urlsafe_b64decode(attachment['data'].encode('UTF-8'))
        return file_data
    except Exception as e:
        print(f"An error occurred while getting attachment: {e}")
        return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/authorize')
def authorize():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, 
        scopes=SCOPES,
        redirect_uri=url_for('oauth2callback', _external=True)
    )
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true'
    )
    session['state'] = state
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = session.get('state')
    
    if not state:
        return 'State parameter missing.', 400
    
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, 
        scopes=SCOPES,
        state=state
    )
    flow.redirect_uri = url_for('oauth2callback', _external=True)

    authorization_response = request.url
    flow.fetch_token(authorization_response=authorization_response)
    
    credentials = flow.credentials
    session['credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    
    return redirect(url_for('download_form'))

@app.route('/download_form')
def download_form():
    if 'credentials' not in session:
        return redirect(url_for('authorize'))
    
    credentials = Credentials(**session['credentials'])
    service = build('gmail', 'v1', credentials=credentials)
    
    # Fetch all labels
    results = service.users().labels().list(userId='me').execute()
    labels = results.get('labels', [])
    
    return render_template('download_form.html', labels=labels)

@app.route('/preview_emails', methods=['POST'])
def preview_emails():
    if 'credentials' not in session:
        return jsonify({"error": "Not authenticated"}), 401
    
    credentials = Credentials(**session['credentials'])
    service = build('gmail', 'v1', credentials=credentials)
    
    search_query = request.form.get('search_query', '')
    start_date = request.form.get('start_date', '')
    end_date = request.form.get('end_date', '')
    name_filter = request.form.get('name_filter', '')
    labels = request.form.getlist('labels')

    query = "has:attachment"
    if search_query:
        query += f" {search_query}"
    if start_date:
        query += f" after:{start_date}"
    if end_date:
        query += f" before:{end_date}"
    if name_filter:
        query += f" filename:{name_filter}"

    results = search_emails(service, query, labels, max_results=500)
    messages = results.get('messages', [])
    total_count = results.get('resultSizeEstimate', 0)
    
    # Get some sample email subjects
    sample_subjects = []
    for msg in messages[:5]:  # Get subjects of first 5 emails
        email = service.users().messages().get(userId='me', id=msg['id'], format='metadata', 
                                               metadataHeaders=['subject']).execute()
        subject = next((header['value'] for header in email['payload']['headers'] 
                        if header['name'].lower() == 'subject'), 'No Subject')
        sample_subjects.append(subject)
    
    return jsonify({
        "count": len(messages),
        "total_count": total_count,
        "sample_subjects": sample_subjects
    })

@app.route('/download_resumes', methods=['POST'])
def download_resumes():
    if 'credentials' not in session:
        return redirect(url_for('authorize'))
    
    credentials = Credentials(**session['credentials'])
    service = build('gmail', 'v1', credentials=credentials)
    
    search_query = request.form.get('search_query', '')
    start_date = request.form.get('start_date', '')
    end_date = request.form.get('end_date', '')
    name_filter = request.form.get('name_filter', '')
    labels = request.form.getlist('labels')
    batch_size = int(request.form.get('batch_size', 15))
    max_emails = int(request.form.get('max_emails', 100))

    # Handle "Last Month" date range
    if start_date == "last_month":
        today = datetime.now()
        last_month = today.replace(day=1) - timedelta(days=1)
        start_date = last_month.replace(day=1).strftime('%Y-%m-%d')
        end_date = last_month.strftime('%Y-%m-%d')

    query = "has:attachment"
    if search_query:
        query += f" {search_query}"
    if start_date:
        query += f" after:{start_date}"
    if end_date:
        query += f" before:{end_date}"
    if name_filter:
        query += f" filename:{name_filter}"

    downloaded_files = []
    page_token = None
    total_emails_processed = 0

    while total_emails_processed < max_emails:
        results = search_emails(service, query, labels, page_token, max_results=min(max_emails - total_emails_processed, 100))
        messages = results.get('messages', [])
        
        if not messages:
            break

        for message in messages:
            msg = service.users().messages().get(userId='me', id=message['id']).execute()
            
            if 'parts' not in msg['payload']:
                continue

            for part in msg['payload']['parts']:
                if part.get('filename'):
                    if 'body' in part and 'attachmentId' in part['body']:
                        attachment_id = part['body']['attachmentId']
                        file_data = get_attachment(service, msg['id'], attachment_id)
                        
                        if file_data:
                            original_file_name = part['filename']
                            downloaded_files.append((original_file_name, file_data))

        total_emails_processed += len(messages)
        page_token = results.get('nextPageToken')
        if not page_token:
            break

    if downloaded_files:
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w') as zf:
            existing_files = set()
            for i, (file_name, file_data) in enumerate(downloaded_files):
                folder_index = i // batch_size
                folder_name = f"batch_{folder_index + 1}"
                unique_file_name = get_unique_filename(file_name, existing_files)
                existing_files.add(unique_file_name)
                zf.writestr(f"{folder_name}/{unique_file_name}", file_data)
        
        memory_file.seek(0)
        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name='resumes.zip'
        )
    else:
        return jsonify({"error": "No attachments found matching the specified criteria."}), 404

@app.route('/get_date_range', methods=['POST'])
def get_date_range():
    range_type = request.form.get('range_type')
    today = datetime.now().date()
    
    if range_type == 'today':
        start_date = end_date = today
    elif range_type == 'this_week':
        start_date = today - timedelta(days=today.weekday())
        end_date = today
    elif range_type == 'this_month':
        start_date = today.replace(day=1)
        end_date = today
    elif range_type == 'last_month':
        last_month = today.replace(day=1) - timedelta(days=1)
        start_date = last_month.replace(day=1)
        end_date = last_month
    else:
        return jsonify({"error": "Invalid range type"}), 400

    return jsonify({
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat()
    })

if __name__ == '__main__':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'  # Remove this in production
    app.run('localhost', 8080, debug=True)