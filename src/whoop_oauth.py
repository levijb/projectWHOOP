# src/whoop_oauth.py
"""
Whoop OAuth 2.0 Authentication Helper
"""

import requests
from urllib.parse import urlencode, urlparse, parse_qs
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
import socket
import secrets
from requests.auth import HTTPBasicAuth


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handle OAuth callback"""
    
    def do_GET(self):
        query = urlparse(self.path).query
        params = parse_qs(query)
        
        if 'code' in params:
            returned_state = params.get('state', [None])[0]
            if returned_state != self.server.expected_state:
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b'<html><body><h1>State mismatch</h1></body></html>')
                return
            
            self.server.auth_code = params['code'][0]
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            response = """
            <html>
            <head><title>Success</title></head>
            <body style="font-family: Arial; text-align: center; padding: 50px;">
                <h1 style="color: green;">✓ Authentication Successful!</h1>
                <p>You can close this window and return to Jupyter.</p>
            </body>
            </html>
            """
            self.wfile.write(response.encode())
        elif 'error' in params:
            error = params['error'][0]
            error_desc = params.get('error_description', ['Unknown'])[0]
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            response = f"""
            <html>
            <body style="font-family: Arial; text-align: center; padding: 50px;">
                <h1 style="color: red;">✗ Authentication Failed</h1>
                <p>Error: {error}</p>
                <p>{error_desc}</p>
            </body>
            </html>
            """
            self.wfile.write(response.encode())
        else:
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html><body><h1>Invalid Request</h1></body></html>')
    
    def log_message(self, format, *args):
        pass


def get_whoop_access_token(client_id: str, client_secret: str, redirect_uri: str = "http://localhost:3000/callback"):
    """
    Complete OAuth flow to get access token
    """
    
    state = secrets.token_urlsafe(32)
    
    port = 3000
    if 'localhost:' in redirect_uri:
        try:
            port = int(redirect_uri.split('localhost:')[1].split('/')[0])
        except:
            port = 3000
    
    print(f"Using port: {port}")
    
    # Check port availability
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('localhost', port))
    if result == 0:
        sock.close()
        raise Exception(f"Port {port} is already in use.")
    sock.close()
    
    # Build authorization URL
    auth_params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'read:recovery read:cycles read:workout read:sleep read:profile read:body_measurement',
        'state': state
    }
    
    auth_url = f"https://api.prod.whoop.com/oauth/oauth2/auth?{urlencode(auth_params)}"
    
    print("=" * 70)
    print("WHOOP AUTHENTICATION")
    print("=" * 70)
    print(f"\nStep 1: Opening browser for authorization...")
    print(f"Redirect URI: {redirect_uri}")
    print(f"State: {state[:10]}...")
    print(f"\nIf browser doesn't open, go to:")
    print(f"\n{auth_url}\n")
    print("=" * 70)
    
    # Start server
    try:
        server = HTTPServer(('localhost', port), OAuthCallbackHandler)
        server.auth_code = None
        server.expected_state = state
        print(f"✓ Server started on port {port}")
    except OSError as e:
        raise Exception(f"Could not start server on port {port}: {e}")
    
    # Open browser
    try:
        webbrowser.open(auth_url)
        print("✓ Browser opened")
    except Exception as e:
        print(f"⚠ Could not open browser: {e}")
    
    print("\nWaiting for authorization...")
    print("(Timeout: 3 minutes)")
    print("=" * 70)
    
    # Wait for callback
    server.timeout = 180
    try:
        server.handle_request()
    except KeyboardInterrupt:
        print("\n\n✗ Cancelled")
        server.server_close()
        raise Exception("Cancelled")
    
    server.server_close()
    
    if not server.auth_code:
        raise Exception("Authorization failed or timed out")
    
    print("\n✓ Authorization code received!")
    
    # Exchange code for token using HTTP Basic Auth
    token_url = "https://api.prod.whoop.com/oauth/oauth2/token"
    
    # Method 1: Try with client credentials in body (original)
    token_data = {
        'grant_type': 'authorization_code',
        'code': server.auth_code,
        'redirect_uri': redirect_uri,
        'client_id': client_id,
        'client_secret': client_secret
    }
    
    print("\nStep 2: Exchanging code for token...")
    print("Trying method 1: Credentials in POST body...")
    
    try:
        response = requests.post(
            token_url,
            data=token_data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        
        if response.status_code == 401:
            print("  ✗ Method 1 failed (401)")
            print("\nTrying method 2: HTTP Basic Auth...")
            
            # Method 2: Use HTTP Basic Authentication
            token_data_basic = {
                'grant_type': 'authorization_code',
                'code': server.auth_code,
                'redirect_uri': redirect_uri
            }
            
            response = requests.post(
                token_url,
                data=token_data_basic,
                auth=HTTPBasicAuth(client_id, client_secret),
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            )
        
        response.raise_for_status()
        tokens = response.json()
        
        print("\n" + "=" * 70)
        print("✓ AUTHENTICATION SUCCESSFUL!")
        print("=" * 70)
        print(f"\nAccess Token: {tokens['access_token'][:20]}...")
        print(f"Expires in: {tokens.get('expires_in', 'unknown')} seconds")
        print("\n" + "=" * 70)
        
        return tokens
        
    except requests.exceptions.HTTPError as e:
        print(f"\n✗ Token exchange failed: {e}")
        if e.response is not None:
            print(f"Response: {e.response.text}")
        raise
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        raise


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str):
    """Refresh access token"""
    token_url = "https://api.prod.whoop.com/oauth/oauth2/token"
    
    token_data = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token
    }
    
    response = requests.post(
        token_url,
        data=token_data,
        auth=HTTPBasicAuth(client_id, client_secret),
        headers={'Content-Type': 'application/x-www-form-urlencoded'}
    )
    
    response.raise_for_status()
    return response.json()