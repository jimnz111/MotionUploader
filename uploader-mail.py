#!/usr/bin/python
'''
Created on 6 Jun 2012

@author: Jeremy Blythe

Motion Uploader - uploads videos to Google Drive

Read the blog entry at http://jeremyblythe.blogspot.com for more information

Version 2: 10 Jun 2015 - rewritten for Google oauth2 and apiclient

Version 3: 16 May 2016 - Added gmail via apiclient - James Bushell
'''
import sys
import base64

import smtplib
from datetime import datetime

import os.path
import logging

import httplib2
from apiclient import discovery
from oauth2client import client
from oauth2client.file import Storage
from oauth2client.tools import run_flow
from googleapiclient.http import MediaFileUpload
from email.mime.text import MIMEText
from apiclient.discovery import build

import ConfigParser

import ipgetter

OAUTH_SCOPE = 'https://www.googleapis.com/auth/gmail.compose'

EXTERNAL_IP_FILE='/home/pi/internet_ip.txt'

class MotionUploader:
    def __init__(self, config_file_path):
        # Load config
        config = ConfigParser.ConfigParser()
        config.read(config_file_path)
        
        # OAuth folder
        self.oauth_folder = config.get('oauth', 'folder')

        # And my secrets file /JFB
        self.oauth_file = config.get('oauth', 'file')


        # GMail account credentials
        self.username = config.get('gmail', 'user')
        self.from_name = config.get('gmail', 'name')
        self.sender = config.get('gmail', 'sender')
        
        # Recipient email address (could be same as from_addr)
        self.recipient = config.get('gmail', 'recipient')        
        
        # Subject line for email
        self.subject = config.get('gmail', 'subject')
        
        # First line of email message
        self.message = config.get('gmail', 'message')
                
        # Folder (or collection) in Docs where you want the videos to go
        self.folder = config.get('docs', 'folder')
        
        # Folder (or collection) in Docs where you want the snapshot to go
        self.snapshot_folder = config.get('docs', 'snapshot-folder')
        
        # Options
        self.delete_after_upload = config.getboolean('options', 'delete-after-upload')
        self.send_email = config.getboolean('options', 'send-email')
        
        self._create_drive()

    def _create_drive(self):
        """Create a Drive service."""
        auth_required = True
        #Have we got some credentials already?
        storage = Storage(self.oauth_folder+'uploader_credentials.txt')    
        credentials = storage.get()
        try:
            if credentials:
                print 'Credentials needed'
		# Check for expiry
                if credentials.access_token_expired:
                    if credentials.refresh_token is not None:
                        credentials.refresh(httplib2.Http())
                        auth_required = False
                else:
                    auth_required = False
        except:
            # Something went wrong - try manual auth
            pass
            
        if auth_required:
            print 'Drive auth required'
            flow = client.flow_from_clientsecrets(
                self.oauth_folder+self.oauth_file,
                scope='https://www.googleapis.com/auth/drive',
                redirect_uri='urn:ietf:wg:oauth:2.0:oob')
        
            auth_uri = flow.step1_get_authorize_url()
            
            print 'Go to this link in your browser:'
            print auth_uri
        
            auth_code = raw_input('Enter the auth code: ')
            credentials = flow.step2_exchange(auth_code)
            storage.put(credentials)
               
        #Get the drive service
        self.http_auth = credentials.authorize(httplib2.Http())
        self.drive_service = discovery.build('drive', 'v2', self.http_auth)
               
    def _get_folder_id(self, folder_name):
        """Find and return the id of the folder given the title."""
        files = self.drive_service.files().list(q="title='%s' and mimeType contains 'application/vnd.google-apps.folder' and trashed=false" % folder_name).execute()
        if len(files['items']) == 1:
            folder_id = files['items'][0]['id']
            return folder_id
        else:
            raise Exception('Could not find the %s folder' % folder_name)
    
    def _send_email(self,msg):
        '''Send an email using the GMail account.'''

        print 'Sending message :\n'+msg

	STORAGE = Storage(self.oauth_folder + 'gmail.storage')
	flow = client.flow_from_clientsecrets(
            self.oauth_folder+self.oauth_file,
            scope=OAUTH_SCOPE)

        http = httplib2.Http()

        credentials = STORAGE.get()
        if credentials is None or credentials.invalid:
          print 'Credentials for mail needed'
          credentials = run_flow(flow, STORAGE, http=http)

        print 'Got mail storage auth'
        http = credentials.authorize(http)

	# Build the Gmail service from discovery
	gmail_service = discovery.build('gmail', 'v1', http=http)

        senddate=datetime.strftime(datetime.now(), '%Y-%m-%d')

        message = MIMEText(msg, 'html')
	message.set_charset('utf-8')
        message['to'] = self.recipient
        message['from'] = self.from_name
        message['subject'] = self.subject
        body = {'raw': base64.b64encode(message.as_string())}
        
        try:
	
          message = (gmail_service.users().messages().send(userId="me", body=body).execute())
          print('Message Id: %s' %message['id'])
          print(message)
        except Exception as error:
          print('An error occured trying to email: %s' % error)

    def upload_video(self, video_file_path):
        """Upload a video to the specified folder. Then optionally send an email and optionally delete the local file."""
        folder_id = self._get_folder_id(self.folder)
        
        media = MediaFileUpload(video_file_path, mimetype='video/avi')
        response = self.drive_service.files().insert(media_body=media, body={'title':os.path.basename(video_file_path), 'parents':[{u'id': folder_id}]}).execute()
        #print response
        video_link = response['alternateLink']
                       
        if self.send_email:
            msg = self.message
            if video_link:
                msg += '\n\n' + video_link
            externalAddress=open(EXTERNAL_IP_FILE, 'r').read()
            msg += '\n\n\n\nStreaming available at : http://' + externalAddress.replace('\n','') + ':8081'
            self._send_email(msg)    
 
        if self.delete_after_upload:
            os.remove(video_file_path)    
    
    def upload_snapshot(self, snapshot_file_path):
        """Upload a snapshot to the specified folder. Remove duplicates."""
        
        print "Uploading the snapshot file"

        folder_id = self._get_folder_id(self.snapshot_folder)
        file_name = os.path.basename(snapshot_file_path)
        #Delete the old snapshot
        files = self.drive_service.files().list(q="title='%s' and '%s' in parents and trashed=false" % (file_name, folder_id)).execute()
        if len(files['items']) >= 1:
            for f in files['items']:
                file_id = f['id']
                self.drive_service.files().delete(fileId=file_id).execute()
        #Now upload the new one
        media = MediaFileUpload(snapshot_file_path, mimetype='image/jpeg')
        self.drive_service.files().insert(media_body=media, body={'title':file_name, 'parents':[{u'id': folder_id}]}).execute()          
                      
    def get_snapshot_url(self, snapshot_file_path):
        """Print out the public url for this snapshot."""
        folder_id = self._get_folder_id(self.snapshot_folder)
        
        public_url = 'https://googledrive.com/host/%s/' % folder_id
        print public_url + os.path.basename(snapshot_file_path)          
                      
if __name__ == '__main__':         
    try:
        logging.basicConfig(level=logging.ERROR)
        if len(sys.argv) < 3:
            exit('Motion Uploader - uploads videos and snapshots to Google Drive\n'+
                 '   by Jeremy Blythe (http://jeremyblythe.blogspot.com)\n\n'+
                 '   Usage: uploader.py {config-file-path} {source-file-path} [option]\n'+
                 '   Options: snap : Upload a snapshot to the snapshot-folder\n'+
                 '            snapurl : Print the public url for the folder+file\n'+
                 '            None : Defaults to uploading video files')
        cfg_path = sys.argv[1]
        vid_path = sys.argv[2]
        if len(sys.argv) > 3:
            option = sys.argv[3]
        else:
            option = 'video'
                
        if not os.path.exists(cfg_path):
            exit('Config file does not exist [%s]' % cfg_path)    
        if not os.path.exists(vid_path):
            exit('Source file does not exist [%s]' % vid_path)
        if option.lower() == 'snap':
            MotionUploader(cfg_path).upload_snapshot(vid_path)
        elif option.lower() == 'snapurl':
            MotionUploader(cfg_path).get_snapshot_url(vid_path)
        else:    
            MotionUploader(cfg_path).upload_video(vid_path)        
    except Exception as e:
        exit('Error: [%s]' % e)
