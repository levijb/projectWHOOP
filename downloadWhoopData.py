#!/usr/bin/env python3
"""
WHOOP Data Collection Script

Authenticates with the WHOOP API and downloads historical data
including recovery, sleep, cycles, and workout information.
"""

import sys
import os
from datetime import datetime
import json

from dotenv import load_dotenv
from pathlib import Path


# Load .env from project root
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)


# Add parent directory to path for local imports
sys.path.append('../')

from src.whoop_client import WhoopClient
from src.whoop_oauth import get_whoop_access_token


def authenticate(env_path: str = './.env') -> str:
    """
    Authenticate with WHOOP API and return access token.
    Updates the .env file with the new token.
    """
    load_dotenv(env_path, override=True)
    
    client_id = os.getenv('WHOOP_CLIENT_ID')
    client_secret = os.getenv('WHOOP_CLIENT_SECRET')
    redirect_uri = "http://localhost:3000/callback"
    
    # Get fresh token
    tokens = get_whoop_access_token(client_id, client_secret, redirect_uri)
    access_token = tokens['access_token']
    
    print(f"\n✓ New access token obtained!")
    
    # Update .env file
    with open(env_path, 'r') as f:
        lines = f.readlines()
    
    with open(env_path, 'w') as f:
        token_updated = False
        for line in lines:
            if line.startswith('WHOOP_ACCESS_TOKEN='):
                f.write(f'WHOOP_ACCESS_TOKEN={access_token}\n')
                token_updated = True
            else:
                f.write(line)
        if not token_updated:
            f.write(f'WHOOP_ACCESS_TOKEN={access_token}\n')
    
    print("✓ .env file updated")
    return access_token


def connect_client(env_path: str = '../.env') -> WhoopClient:
    """
    Create and verify a WHOOP client connection.
    """
    load_dotenv(env_path, override=True)
    access_token = os.getenv('WHOOP_ACCESS_TOKEN')
    
    client = WhoopClient(access_token)
    
    # Test connection
    profile = client.get_user_profile()
    if profile:
        print(f"✓ Connected as: {profile.get('first_name')} {profile.get('last_name')}")
        return client
    else:
        print("✗ Connection failed - token may be expired")
        return None


def fetch_and_save_data(client: WhoopClient, days_back: int = 1000, output_dir: str = './data/raw') -> dict:
    """
    Fetch all historical data and save to JSON file.
    """
    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    # Get all data
    all_data = client.get_all_historical_data(days_back=days_back)
    
    # Save
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = os.path.join(output_dir, f'whoop_complete_{timestamp}.json')
    
    with open(filename, 'w') as f:
        json.dump(all_data, f, indent=2)
    
    print(f"\n✓ Saved to: {filename}")
    print(f"  File size: {os.path.getsize(filename) / 1024:.1f} KB")
    print(f"\nData Summary:")
    print(f"  Recovery: {len(all_data.get('recovery', []))}")
    print(f"  Sleep: {len(all_data.get('sleep', []))}")
    print(f"  Cycles: {len(all_data.get('cycles', []))}")
    print(f"  Workouts: {len(all_data.get('workouts', []))}")
    
    return all_data


def main():
    """
    Main entry point for the script.
    """
    env_path = './.env'
    
    # Load environment and check for existing token
    load_dotenv(env_path, override=True)
    access_token = os.getenv('WHOOP_ACCESS_TOKEN')
    
    # Try to connect with existing token first
    client = None
    if access_token:
        print("Attempting connection with existing token...")
        client = connect_client(env_path)
    
    # If connection failed, re-authenticate
    if client is None:
        print("\nStarting authentication flow...")
        authenticate(env_path)
        client = connect_client(env_path)
    
    if client is None:
        print("Failed to connect to WHOOP API. Exiting.")
        sys.exit(1)
    
    # Fetch and save data
    fetch_and_save_data(client, days_back=100)


if __name__ == '__main__':
    main()