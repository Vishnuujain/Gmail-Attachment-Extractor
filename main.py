import os
import base64
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from datetime import datetime
import gradio as gr
import re

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def authenticate():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds

def search_emails(service, query):
    try:
        results = service.users().messages().list(userId='me', q=query).execute()
        messages = results.get('messages', [])
        
        while 'nextPageToken' in results:
            page_token = results['nextPageToken']
            results = service.users().messages().list(userId='me', q=query, pageToken=page_token).execute()
            messages.extend(results.get('messages', []))
        
        return messages
    except Exception as e:
        print(f"An error occurred while searching emails: {e}")
        return []

def get_attachment(service, msg_id, attachment_id):
    try:
        attachment = service.users().messages().attachments().get(userId='me', messageId=msg_id, id=attachment_id).execute()
        file_data = base64.urlsafe_b64decode(attachment['data'].encode('UTF-8'))
        return file_data
    except Exception as e:
        print(f"An error occurred while getting attachment: {e}")
        return None

def create_download_folder(search_query):
    folder_name = re.sub(r'[^\w\-_\. ]', '_', search_query)
    folder_name = f"{folder_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    if not os.path.exists(folder_name):
        os.makedirs(folder_name)
    
    return folder_name

def get_unique_filename(folder_path, file_name):
    base_name, extension = os.path.splitext(file_name)
    counter = 1
    new_name = file_name
    
    while os.path.exists(os.path.join(folder_path, new_name)):
        new_name = f"{base_name}_{counter}{extension}"
        counter += 1
    
    return new_name

def download_attachments(search_query, start_date, end_date, min_size, max_size, name_filter):
    creds = authenticate()
    service = build('gmail', 'v1', credentials=creds)

    query = f"{search_query} has:attachment"
    if start_date:
        query += f" after:{start_date}"
    if end_date:
        query += f" before:{end_date}"
    if name_filter:
        query += f" filename:{name_filter}"

    print(f"Searching with query: {query}")
    messages = search_emails(service, query)
    print(f"Found {len(messages)} messages matching the search criteria.")

    download_folder = create_download_folder(search_query)
    downloaded_files = []

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
                        file_size = len(file_data)
                        if min_size <= file_size <= max_size:
                            original_file_name = part['filename']
                            file_name = get_unique_filename(download_folder, original_file_name)
                            file_path = os.path.join(download_folder, file_name)
                            
                            with open(file_path, 'wb') as f:
                                f.write(file_data)
                            downloaded_files.append(file_name)
                            print(f"Downloaded: {file_name}")

    if downloaded_files:
        return f"Downloaded {len(downloaded_files)} attachments to folder '{download_folder}': {', '.join(downloaded_files)}"
    else:
        return f"No attachments found matching the specified criteria. Folder '{download_folder}' was created but is empty."

def launch_interface():
    with gr.Blocks(css="footer {visibility: hidden}") as interface:
        gr.Markdown(
        """
        # Enhanced Gmail Attachment Downloader
        
        This tool allows you to download attachments from your Gmail account based on specific criteria.
        
        **Before you start:**
        1. Enable the Gmail API and download the `credentials.json` file from [Google Developers Console](https://developers.google.com/gmail/api/quickstart/nodejs).
        2. Place the `credentials.json` file in the same directory as this script.
        
        **Note:** 
        - On first use, you'll need to authorize the application to access your Gmail account.
        - A new folder will be created for each search query to organize downloads.
        - Files with the same name will be saved with a unique identifier to prevent overwriting.
        - Enter dates in the format YYYY/MM/DD (e.g., 2023/06/30).
        """
        )
        
        search_query = gr.Textbox(label="Search Query (e.g., 'red bus tax invoice')")
        
        with gr.Row():
            start_date = gr.Textbox(label="Start Date (YYYY/MM/DD)", placeholder="e.g., 2023/06/01")
            end_date = gr.Textbox(label="End Date (YYYY/MM/DD)", placeholder="e.g., 2023/06/30")
        
        with gr.Row():
            min_size = gr.Number(label="Minimum Size (bytes)", value=0)
            max_size = gr.Number(label="Maximum Size (bytes)", value=1000000000)
        
        name_filter = gr.Textbox(label="File Name Filter (optional)")
        
        download_button = gr.Button("Download Attachments")
        output = gr.Textbox(label="Output")
        
        download_button.click(
            download_attachments,
            inputs=[search_query, start_date, end_date, min_size, max_size, name_filter],
            outputs=output
        )
    
    interface.launch()

if __name__ == "__main__":
    launch_interface()